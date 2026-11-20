"""Escucha de ARI (Cloud Run, mínimo de instancias = 1, máximo = 1).

Consumidor único (ADR-010): mantiene el WebSocket de eventos contra Asterisk,
traduce cada evento a hechos en Postgres (traductor.py) y ejecuta la cola de
comandos de telefonía (invoke.comando_telefonia) que escriben la API y el pacer.

Variables: DATABASE_URL, ARI_URL, ARI_USUARIO, ARI_CLAVE, ARI_APP, INVOKE_TRUNK,
INVOKE_CALLER_ID. Expone /salud en PORT para el health check de Cloud Run.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.parse
import uuid

import httpx
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from invoke import db  # noqa: E402
from invoke.config import config  # noqa: E402

from services.ari.grabaciones import BucketGcs, PbxAri, Pendiente, subir_pendientes  # noqa: E402
from services.ari.traductor import Traductor  # noqa: E402

log = logging.getLogger("invoke.ari")


def es_llamada(canal: str | None) -> bool:
    """Las llamadas de INVOKE se originan con channelId = llamada_id (un UUID). Los demás
    canales (patas de puesto, snoop, mitades ";2" de los Local) no son llamadas y no se
    buscan en la base (verificado contra Asterisk 20.6 y 22.5: rompían la consulta)."""
    try:
        uuid.UUID(str(canal))
        return True
    except ValueError:
        return False


class PuertoReal:
    """Implementación del puerto del traductor contra Postgres y ARI REST."""

    def __init__(self, cliente: httpx.Client):
        self.http = cliente
        self.puentes: dict[int, str] = {}

    # ── base ──
    def evento(self, tipo, **campos):
        with db.transaccion() as cur:
            db.evento(cur, tipo, **campos)

    def llamada(self, llamada_id):
        if not es_llamada(llamada_id):
            return None
        with db.transaccion() as cur:
            o = db.uno(cur, """SELECT caso_id, participante_id, estudio_id, datos FROM invoke.evento
                               WHERE llamada_id = %s AND tipo = 'llamada_originada' LIMIT 1""", (llamada_id,))
        if not o:
            return None
        return {"caso_id": o["caso_id"], "participante_id": o["participante_id"], "estudio_id": o["estudio_id"],
                "grabar": o["datos"].get("grabar"), "tipo": o["datos"].get("tipo")}

    def tomar_participante(self, llamada_id):
        with db.transaccion() as cur:
            return db.uno(cur, "SELECT invoke.tomar_participante_para(%s) AS p", (llamada_id,))["p"]

    def canal_de(self, participante_id):
        with db.transaccion() as cur:
            r = db.uno(cur, "SELECT canal_audio FROM invoke.participante_estado WHERE participante_id = %s", (participante_id,))
        return r and r["canal_audio"]

    def estado_llamada(self, llamada_id):
        """Estado persistido de la llamada (encargo D2): la base es la fuente, no la memoria."""
        if not es_llamada(llamada_id):
            return {}
        with db.transaccion() as cur:
            fila = db.uno(cur, """
                SELECT l.atendida_en IS NOT NULL AS atendida, l.conectada_en IS NOT NULL AS conectada,
                       l.participante_id, l.amd,
                       EXISTS (SELECT 1 FROM invoke.evento a WHERE a.llamada_id = l.llamada_id
                               AND a.tipo = 'llamada_abandonada') AS abandonada
                FROM invoke.llamada l WHERE l.llamada_id = %s""", (llamada_id,))
        return fila or {}

    def tiene_disposicion(self, llamada_id):
        if not es_llamada(llamada_id):
            return False
        with db.transaccion() as cur:
            # Una llamada descartada por el pacer tampoco lleva disposición: no fue un intento.
            return db.uno(cur, "SELECT 1 FROM invoke.evento WHERE llamada_id = %s AND tipo IN ('disposicion_telefono', 'llamada_descartada') LIMIT 1",
                          (llamada_id,)) is not None

    # ── ARI ──
    def _post(self, ruta, **params):
        r = self.http.post(ruta, params=params)
        r.raise_for_status()
        return r.json() if r.content else None

    def responder(self, canal):
        self._post(f"/channels/{canal}/answer")

    def puente_de(self, participante_id):
        if participante_id not in self.puentes:
            bid = f"puesto-{participante_id}"
            self._post(f"/bridges/{bid}", type="mixing", name=bid)
            self.puentes[participante_id] = bid
        return self.puentes[participante_id]

    def agregar_a_puente(self, puente, canal):
        self._post(f"/bridges/{puente}/addChannel", channel=canal)

    def colgar(self, canal):
        r = self.http.delete(f"/channels/{canal}")
        if r.status_code not in (204, 404):
            r.raise_for_status()

    def reproducir(self, canal, sonido, reproduccion_id):
        try:
            self._post(f"/channels/{canal}/play/{reproduccion_id}", media=f"sound:{sonido}", lang="es")
            return True
        except httpx.HTTPError:
            log.warning("no se pudo reproducir %s en %s", sonido, canal)
            return False

    def continuar_amd(self, canal):
        """Saca la llamada de Stasis hacia el dialplan invoke-amd, que corre AMD() y la
        devuelve a Stasis como "cliente" con el resultado."""
        self._post(f"/channels/{canal}/continue", context="invoke-amd", extension="s", priority=1)

    def grabar(self, puente, nombre):
        self._post(f"/bridges/{puente}/record", name=nombre, format="wav", ifExists="overwrite")


def ejecutar_comando(puerto: PuertoReal, cmd: dict, traductor: Traductor | None = None) -> str:
    """Ejecuta un comando de la cola contra ARI. Devuelve un texto de resultado."""
    cfg = config()
    d = cmd["datos"] or {}
    tipo = cmd["tipo"]
    if tipo == "originar":
        llamada = str(cmd["llamada_id"])
        endpoint = cfg.trunk.format(numero=d["numero"])
        params = {"endpoint": endpoint, "channelId": llamada, "timeout": d.get("timeout", 30)}
        if cfg.caller_id:
            params["callerId"] = cfg.caller_id
        # Toda llamada se origina hacia la aplicación: así queda suscripta desde que nace y
        # su ChannelDestroyed llega siempre, también si nunca atienden. En power y
        # predictivo entra primero como "amd" y el traductor la manda al dialplan a
        # pasar por AMD() (verificado contra Asterisk 20.6 y 22.5: originada directo al dialplan,
        # su fin nunca llegaba y la llamada quedaba sin terminar ni clasificar).
        rol = "amd" if d.get("amd") else "cliente"
        params.update({"app": cfg.ari_app, "appArgs": f"{rol},{llamada}"})
        r = puerto.http.post("/channels", params=params, json={"variables": {"LLAMADA": llamada}})
        r.raise_for_status()
        if traductor is not None:
            traductor.registrar_originada(llamada)
        return "originada"
    if tipo == "colgar":
        puerto.colgar(d.get("canal") or str(cmd["llamada_id"]))
        return "colgada"
    if tipo == "mudo":
        canal = puerto.canal_de(cmd["participante_id"])
        accion = puerto.http.post if d.get("activar", True) else puerto.http.delete
        accion(f"/channels/{canal}/mute", params={"direction": "in"}).raise_for_status()
        return "mudo" if d.get("activar", True) else "con audio"
    if tipo == "dtmf":
        puerto._post(f"/channels/{cmd['llamada_id']}/dtmf", dtmf=d["digitos"])
        return "dtmf enviado"
    if tipo == "escuchar":
        # Snoop sobre la pata del encuestador (oye a las dos partes); el canal snoop
        # entra a Stasis y el traductor lo suma al puente del supervisor.
        canal_enc = puerto.canal_de(cmd["participante_id"])
        puerto._post(f"/channels/{canal_enc}/snoop", spy="both", whisper="out" if d.get("susurro") else "none",
                     app=cfg.ari_app, appArgs=f"snoop,{d['supervisor_participante']}",
                     snoopId=f"snoop-{cmd['participante_id']}")
        return "escuchando"
    if tipo == "dejar_escuchar":
        puerto.colgar(f"snoop-{cmd['participante_id']}")
        return "escucha terminada"
    raise ValueError(f"comando no soportado: {tipo}")


def tomar_comandos() -> list[dict]:
    with db.transaccion() as cur:
        return db.todos(cur, """
            UPDATE invoke.comando_telefonia SET estado = 'tomado', tomado_en = clock_timestamp()
            WHERE id IN (SELECT id FROM invoke.comando_telefonia WHERE estado = 'pendiente'
                         ORDER BY id LIMIT 20 FOR UPDATE SKIP LOCKED)
            RETURNING *""")


def cerrar_comando(cmd_id: int, ok: bool, resultado: str) -> None:
    with db.transaccion() as cur:
        cur.execute("UPDATE invoke.comando_telefonia SET estado = %s, resultado = %s WHERE id = %s",
                    ("hecho" if ok else "error", resultado[:500], cmd_id))


async def bucle_comandos(puerto: PuertoReal, traductor: Traductor) -> None:
    import psycopg

    cfg = config()
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(cfg.database_url, autocommit=True) as conn:
                if cfg.rol_db:
                    await conn.execute(f'SET ROLE "{cfg.rol_db}"')
                await conn.execute("LISTEN invoke_comandos")
                while True:
                    for cmd in await asyncio.to_thread(tomar_comandos):
                        try:
                            res = await asyncio.to_thread(ejecutar_comando, puerto, cmd, traductor)
                            await asyncio.to_thread(cerrar_comando, cmd["id"], True, res)
                        except Exception as e:  # noqa: BLE001
                            log.exception("comando %s falló", cmd["id"])
                            await asyncio.to_thread(cerrar_comando, cmd["id"], False, str(e))
                    try:
                        async with asyncio.timeout(2):
                            async for _ in conn.notifies():
                                break
                    except TimeoutError:
                        pass
        except Exception:  # noqa: BLE001
            log.exception("bucle de comandos caído; reintento en 2 s")
            await asyncio.sleep(2)


def ciclo_grabaciones(pbx, bucket, lote: int = 20) -> dict:
    """Un ciclo de subida (encargo H2): pendientes del log → bucket → hecho → borrado del PBX."""
    with db.transaccion() as cur:
        filas = db.todos(cur, """
            SELECT g.llamada_id::text AS llamada_id, COALESCE(e.codigo, 'sin-estudio') AS estudio,
                   COALESCE(g.archivo, 'llamada-' || g.llamada_id) AS archivo, g.ocurrido_en
            FROM invoke.grabacion_pendiente g LEFT JOIN invoke.estudio e ON e.id = g.estudio_id
            ORDER BY g.ocurrido_en LIMIT %s""", (lote,))
    pendientes = [Pendiente(f["llamada_id"], f["estudio"], f["archivo"].removesuffix(".wav"), f["ocurrido_en"])
                  for f in filas]

    def registrar(p: Pendiente, datos: dict) -> None:
        with db.transaccion() as cur:
            db.evento(cur, "grabacion_subida", llamada_id=p.llamada_id, autor="ari",
                      caso_id=(db.uno(cur, "SELECT caso_id FROM invoke.evento WHERE llamada_id = %s AND tipo = 'llamada_originada'",
                                      (p.llamada_id,)) or {}).get("caso_id"), datos=datos)

    return subir_pendientes(pendientes, pbx, bucket, registrar)


async def bucle_grabaciones(puerto: PuertoReal) -> None:
    cfg = config()
    if not cfg.grabaciones_bucket:
        # Sin bucket, las grabaciones se acumulan en el disco del PBX hasta llenarlo.
        log.error("GRABACIONES_BUCKET no está configurado: las grabaciones NO se suben y el disco del PBX se llena")
        return
    bucket = BucketGcs(cfg.grabaciones_bucket)
    pbx = PbxAri(puerto.http)
    while True:
        try:
            r = await asyncio.to_thread(ciclo_grabaciones, pbx, bucket)
            if any(r.values()):
                log.info("grabaciones: %s", r)
        except Exception:  # noqa: BLE001
            log.exception("ciclo de grabaciones falló")
        await asyncio.sleep(20)


async def bucle_eventos(traductor: Traductor) -> None:
    cfg = config()
    base = cfg.ari_url.replace("http", "ws", 1).rstrip("/")
    q = urllib.parse.urlencode({"app": cfg.ari_app, "api_key": f"{cfg.ari_usuario}:{cfg.ari_clave}"})
    url = f"{base}/events?{q}"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                log.info("conectado a ARI")
                async for mensaje in ws:
                    ev = json.loads(mensaje)
                    try:
                        await asyncio.to_thread(traductor.manejar, ev)
                    except Exception:  # noqa: BLE001
                        log.exception("evento ARI no procesado: %s", ev.get("type"))
        except Exception:  # noqa: BLE001
            log.exception("WebSocket ARI caído; reintento en 2 s")
            await asyncio.sleep(2)


async def salud(puerto_http: int) -> None:
    async def atender(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    servidor = await asyncio.start_server(atender, "0.0.0.0", puerto_http)
    async with servidor:
        await servidor.serve_forever()


def reconstruir_puestos(traductor: Traductor) -> None:
    """Al reiniciar, recupera qué canal es de qué puesto desde la base (no desde Asterisk)."""
    with db.transaccion() as cur:
        for r in db.todos(cur, "SELECT participante_id, canal_audio FROM invoke.participante_estado WHERE canal_audio IS NOT NULL"):
            traductor.puestos[r["canal_audio"]] = r["participante_id"]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = config()
    cliente = httpx.Client(base_url=cfg.ari_url, auth=(cfg.ari_usuario, cfg.ari_clave), timeout=5)
    puerto = PuertoReal(cliente)
    traductor = Traductor(puerto)
    reconstruir_puestos(traductor)
    await asyncio.gather(bucle_eventos(traductor), bucle_comandos(puerto, traductor), bucle_grabaciones(puerto), salud(int(os.environ.get("PORT", "8080"))))


if __name__ == "__main__":
    asyncio.run(main())
