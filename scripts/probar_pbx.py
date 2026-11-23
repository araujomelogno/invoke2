"""Prueba de punta a punta contra un Asterisk real (PBX de laboratorio).

Recorre los flujos de telefonía con el servicio `services/ari` corriendo de verdad:
pata permanente del puesto, preview con humano (timbre, atención, puente,
grabación, escucha del supervisor, corte y clasificación), ocupado, no contesta,
número inexistente, power con humano, power sin encuestadores (abandono),
contestador (AMD) y subida de la grabación.

La PSTN y el navegador se simulan con canales Local
(infra/asterisk/laboratorio/extensions_laboratorio.conf), así que no hace falta
trunk ni WebRTC. Lo que esta prueba NO cubre: el audio real del navegador
(SIP.js, ICE, TURN, códec negociado) y el trunk del proveedor; eso se verifica
con un puesto real (docs/despliegue.md, paso 14).

Requisitos: Asterisk con la configuración de infra/asterisk + el archivo de
laboratorio, `services/ari/main.py` corriendo con INVOKE_TRUNK=Local/{numero}@invoke-lab-pstn,
y las mismas variables de entorno (DATABASE_URL, ARI_URL, ARI_USUARIO, ARI_CLAVE).
Opcionales: LAB_ASTERISK_CLI (cómo llegar a la CLI de Asterisk; por defecto
`asterisk`) y LAB_REINICIO_ARI (comando que reinicia el servicio ari).

    python scripts/probar_pbx.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time
import uuid

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402

from invoke import db  # noqa: E402
from invoke.config import config  # noqa: E402
from invoke.enrutador import Usuario  # noqa: E402
from invoke.servicios import estacion, supervision  # noqa: E402

CUESTIONARIO = {"formato": 1, "id": "lab", "nombre": "Laboratorio", "bloques": [{"id": "B", "pantallas": [
    {"id": "S1", "preguntas": [{"id": "P1", "tipo": "simple", "texto": "¿Sí o no?",
                                "opciones": [{"codigo": "1", "texto": "Sí"}, {"codigo": "2", "texto": "No"}]}]}]}]}

HUMANO, OCUPADO, NO_CONTESTA, CONTESTADOR, INEXISTENTE = "0990000001", "0990000002", "0990000003", "0990000004", "0990009999"

resultados: list[tuple[str, bool, str]] = []


def ari() -> httpx.Client:
    c = config()
    return httpx.Client(base_url=c.ari_url, auth=(c.ari_usuario, c.ari_clave), timeout=10)


def esperar(sql: str, params: tuple, segundos: float = 20) -> dict | None:
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        with db.transaccion() as cur:
            fila = db.uno(cur, sql, params)
        if fila:
            return fila
        time.sleep(0.3)
    return None


def evento(tipo: str, llamada: str | None = None, participante: int | None = None, segundos: float = 20) -> dict | None:
    if llamada:
        return esperar("SELECT * FROM invoke.evento WHERE tipo = %s AND llamada_id = %s ORDER BY id LIMIT 1",
                       (tipo, llamada), segundos)
    return esperar("SELECT * FROM invoke.evento WHERE tipo = %s AND participante_id = %s ORDER BY id DESC LIMIT 1",
                   (tipo, participante), segundos)


def comprobar(nombre: str, ok: bool, detalle: str = "") -> bool:
    resultados.append((nombre, ok, detalle))
    print(f"{'OK ' if ok else 'MAL'}  {nombre}{(' — ' + detalle) if detalle else ''}", flush=True)
    return ok


def usuario(nombre: str, roles: list[str]) -> Usuario:
    uid = f"lab-{uuid.uuid4().hex[:8]}"
    with db.transaccion() as cur:
        cur.execute("INSERT INTO invoke.usuario (uid, email, nombre, roles) VALUES (%s, %s, %s, %s)",
                    (uid, f"{uid}@lab.invoke", nombre, roles))
        pid = db.uno(cur, """INSERT INTO invoke.participante (tipo, usuario_uid, nombre) VALUES ('encuestador_webrtc', %s, %s)
                             RETURNING id""", (uid, nombre))["id"]
    return Usuario(uid, f"{uid}@lab.invoke", nombre, roles, pid)


def estudio(modo: str, numeros: list[str], encuestadores: list[Usuario]) -> dict:
    codigo = f"LAB-{uuid.uuid4().hex[:6]}"
    with db.transaccion() as cur:
        e = db.uno(cur, """INSERT INTO invoke.estudio (codigo, nombre, modo_discado, es_estudio, n_inicial, ajuste_automatico)
                           VALUES (%s, %s, %s, false, 1, false) RETURNING *""", (codigo, "Laboratorio PBX", modo))
        cur.execute("DELETE FROM invoke.franja WHERE estudio_id = %s", (e["id"],))
        cur.execute("INSERT INTO invoke.franja VALUES (%s, 'todo', '00:00', '23:59:59')", (e["id"],))
        cur.execute("""INSERT INTO invoke.cuestionario (estudio_id, version, definicion, publicado_en)
                       VALUES (%s, 1, %s, now())""", (e["id"], db.Jsonb(CUESTIONARIO)))
        cur.execute("UPDATE invoke.estudio SET estado = 'activo' WHERE id = %s", (e["id"],))
        g = db.uno(cur, "INSERT INTO invoke.guia (estudio_id, nombre, semilla) VALUES (%s, 'lab', 1) RETURNING id", (e["id"],))
        for n in numeros:
            c = db.uno(cur, "INSERT INTO invoke.caso (estudio_id, guia_id, identificador) VALUES (%s, %s, %s) RETURNING id",
                       (e["id"], g["id"], n))
            cur.execute("INSERT INTO invoke.telefono (caso_id, estudio_id, numero) VALUES (%s, %s, %s)", (c["id"], e["id"], n))
        for u in encuestadores:
            cur.execute("INSERT INTO invoke.estudio_usuario VALUES (%s, %s)", (e["id"], u.uid))
    return e


def conectar_puesto(u: Usuario, e: dict, disponible: bool = True) -> bool:
    """Inicia sesión (rota la credencial SIP en realtime) y levanta la pata permanente."""
    with db.transaccion() as cur:
        estacion.iniciar_sesion(cur, u, e["id"])
    # La credencial que escribió la API tiene que ser visible para Asterisk (realtime),
    # con los códecs que corresponden al puesto (D1 + H4). Se mira por la CLI del PBX.
    cli = cli_asterisk(f"pjsip show endpoint p{u.participante_id}")
    if cli is not None:
        esperado = "(opus|alaw|ulaw)" if u.nombre.endswith("Opus") else "(alaw|ulaw)"
        allow = next((l.split(":", 1)[1].strip() for l in cli.splitlines() if l.strip().startswith("allow ")), "")
        comprobar(f"realtime: Asterisk carga el puesto p{u.participante_id} con códecs {esperado}",
                  f"Endpoint:  p{u.participante_id}" in cli and allow == esperado, f"allow={allow or '—'}")
    with ari() as a:
        a.post("/channels", params={"endpoint": "Local/espera@invoke-lab-puesto", "app": "invoke",
                                    "appArgs": f"puesto,{u.participante_id}"}).raise_for_status()
    ok = esperar("SELECT 1 FROM invoke.participante_estado WHERE participante_id = %s AND canal_audio IS NOT NULL",
                 (u.participante_id,), 10) is not None
    comprobar(f"pata permanente de {u.nombre} conectada (audio_conectado)", ok)
    if disponible:
        with db.transaccion() as cur:
            estacion.cambiar_estado(cur, u, "disponible")
    return ok


def cli_asterisk(comando: str) -> str | None:
    """Salida de la CLI de Asterisk; None si no hay cómo llegar a ella.

    LAB_ASTERISK_CLI dice cómo invocarla: por defecto `asterisk` (el script corre en
    el PBX); con el PBX en un contenedor, `docker exec invoke-pbx-lab asterisk`."""
    import os
    import shlex
    import shutil
    import subprocess

    base = shlex.split(os.environ.get("LAB_ASTERISK_CLI", "asterisk"))
    if not shutil.which(base[0]):
        return None
    return subprocess.run([*base, "-rx", comando], capture_output=True, text=True, timeout=10).stdout


def llamar_preview(u: Usuario) -> tuple[dict, str]:
    with db.transaccion() as cur:
        caso = estacion.siguiente_caso(cur, u)
        r = estacion.llamar(cur, u, {"caso_id": caso["id"], "telefono_id": caso["telefonos"][0]["id"]})
    return caso, r["llamada_id"]


def disposicion(llamada: str, segundos: float = 45) -> int | None:
    d = evento("disposicion_telefono", llamada, segundos=segundos)
    return d and d["datos"]["codigo"]


# ───────────────────────────────── escenarios ─────────────────────────────────

def preview_humano_con_escucha_y_grabacion(enc: Usuario, sup: Usuario) -> None:
    e = estudio("preview", [HUMANO], [enc])
    conectar_puesto(enc, e)
    caso, ll = llamar_preview(enc)
    comprobar("preview: el sistema origina", evento("llamada_originada", ll) is not None)
    comprobar("preview: «timbrando» llega (D3)", evento("llamada_timbrando", ll, segundos=10) is not None)
    comprobar("preview: el respondente atiende", evento("llamada_atendida", ll) is not None)
    con = evento("llamada_conectada", ll)
    comprobar("preview: se puentea con el puesto", con is not None and con["participante_id"] == enc.participante_id)
    with ari() as a:
        puente = a.get(f"/bridges/puesto-{enc.participante_id}").json()
    comprobar("el puente del puesto tiene la pata del puesto y la del respondente", len(puente.get("channels", [])) == 2,
              f"canales: {len(puente.get('channels', []))}")
    # Escucha del supervisor
    conectar_puesto(sup, e, disponible=False)
    with db.transaccion() as cur:
        supervision.escuchar(cur, sup, enc.participante_id)
    snoop = esperar("SELECT 1 FROM invoke.comando_telefonia WHERE tipo = 'escuchar' AND estado = 'hecho' AND participante_id = %s",
                    (enc.participante_id,), 10)
    with ari() as a:
        canales = a.get(f"/bridges/puesto-{sup.participante_id}").json().get("channels", [])
    comprobar("escucha: el canal snoop entra al puente del supervisor",
              snoop is not None and f"snoop-{enc.participante_id}" in canales, f"canales del supervisor: {canales}")
    with db.transaccion() as cur:
        supervision.dejar_de_escuchar(cur, sup, enc.participante_id)
    time.sleep(1)
    # Corte y clasificación
    with db.transaccion() as cur:
        estacion.comando(cur, enc, "colgar")
    comprobar("colgar: la llamada termina", evento("llamada_finalizada", ll) is not None)
    time.sleep(1)
    with db.transaccion() as cur:
        auto = db.uno(cur, "SELECT 1 FROM invoke.evento WHERE llamada_id = %s AND tipo = 'disposicion_telefono'", (ll,))
    comprobar("una llamada conectada no recibe disposición automática (la pone el encuestador)", auto is None)
    with db.transaccion() as cur:
        estacion.finalizar(cur, enc, {"caso_id": caso["id"], "llamada_id": ll, "disposicion_telefono": 4,
                                      "disposicion_encuesta": 4})
        estado = db.uno(cur, "SELECT estado FROM invoke.caso_estado WHERE caso_id = %s", (caso["id"],))["estado"]
    comprobar("clasificar: Contesta + Rechazada cierra el caso", estado == "cerrado")
    # Grabación: registrada por RecordingFinished y subida al bucket (doble local)
    g = evento("grabacion", ll, segundos=10)
    comprobar("grabación: Asterisk la termina y queda registrada", g is not None)
    if g:
        from services.ari.grabaciones import PbxAri
        from services.ari.main import ciclo_grabaciones

        class BucketLocal:
            nombre = "laboratorio"

            def __init__(self):
                self.dir = pathlib.Path(tempfile.mkdtemp())

            def subir(self, objeto, datos, md5):
                destino = self.dir / objeto
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(datos)

        bucket = BucketLocal()
        with ari() as a:
            r = ciclo_grabaciones(PbxAri(a), bucket)
            subida = esperar("SELECT * FROM invoke.grabacion_en_bucket WHERE llamada_id = %s", (ll,), 5)
            sigue = a.get(f"/recordings/stored/llamada-{ll}").status_code
        archivo = next(bucket.dir.rglob("*.wav"), None)
        comprobar("grabación: se baja por ARI, se sube y se borra del PBX",
                  r["subidas"] >= 1 and subida is not None and sigue == 404 and archivo is not None and archivo.stat().st_size > 44,
                  f"{r}, bytes={archivo.stat().st_size if archivo else 0}, en PBX: HTTP {sigue}")


def preview_sin_respuesta(enc: Usuario, numero: str, esperado: int, nombre: str) -> None:
    e = estudio("preview", [numero], [enc])
    conectar_puesto(enc, e)
    _caso, ll = llamar_preview(enc)
    codigo = disposicion(ll)
    comprobar(f"{nombre}: disposición automática {esperado}", codigo == esperado, f"obtenida {codigo}")


def power(enc: Usuario, numero: str, con_agente: bool, nombre: str, esperado: str) -> None:
    from services.pacer import main as pacer

    e = estudio("power", [numero], [enc])
    conectar_puesto(enc, e, disponible=con_agente)
    with db.transaccion() as cur:
        est = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (e["id"],))
    if not con_agente:
        # Sin nadie disponible el pacer no origina: se fuerza una reserva para probar el abandono.
        with db.transaccion() as cur:
            r = db.uno(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e["id"],))
            cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, llamada_id, datos, autor) VALUES ('originar', %s, %s, 'lab')""",
                        (r["llamada_id"], db.Jsonb({"numero": r["numero"], "amd": True, "timeout": 25})))
        ll = str(r["llamada_id"])
    else:
        pacer.ciclo_estudio(est)
        with db.transaccion() as cur:
            ll = str(db.uno(cur, "SELECT llamada_id FROM invoke.evento WHERE estudio_id = %s AND tipo = 'llamada_originada'",
                            (e["id"],))["llamada_id"])
    amd = evento("amd_resultado", ll, segundos=25)
    detalle = f"AMD={amd and amd['datos']['resultado']}"
    if esperado == "conectada":
        con = evento("llamada_conectada", ll, segundos=10)
        comprobar(f"{nombre}: atiende un humano y se conecta al encuestador disponible",
                  con is not None and con["participante_id"] == enc.participante_id, detalle)
        with ari() as a:
            a.delete(f"/channels/{ll}")
        comprobar(f"{nombre}: al cortar, el encuestador queda clasificando (D2: participante desde la base)",
                  esperar("SELECT 1 FROM invoke.participante_estado WHERE participante_id = %s AND estado = 'clasificando'",
                          (enc.participante_id,), 10) is not None)
        # Como en un turno real: el encuestador clasifica antes de seguir.
        with db.transaccion() as cur:
            caso = db.uno(cur, "SELECT caso_id FROM invoke.evento WHERE llamada_id = %s AND tipo = 'llamada_originada'", (ll,))
            estacion.finalizar(cur, enc, {"caso_id": caso["caso_id"], "llamada_id": ll, "disposicion_telefono": 4,
                                          "disposicion_encuesta": 4})
    else:
        codigo = disposicion(ll)
        comprobar(f"{nombre}: disposición {esperado}", str(codigo) == esperado, f"{detalle}, obtenida {codigo}")
        if esperado == "30":
            with db.transaccion() as cur:
                t = db.uno(cur, """SELECT EXTRACT(epoch FROM f.ocurrido_en - a.ocurrido_en) AS s
                                   FROM invoke.evento a JOIN invoke.evento f ON f.llamada_id = a.llamada_id
                                   WHERE a.llamada_id = %s AND a.tipo = 'llamada_abandonada' AND f.tipo = 'llamada_finalizada'""",
                           (ll,))
            comprobar(f"{nombre}: el mensaje de disculpas se escucha antes de cortar",
                      t is not None and float(t["s"]) >= 0.5, f"{t and round(float(t['s']), 2)} s entre abandono y corte")


def reinicio_con_llamada_en_curso(enc: Usuario) -> None:
    """D2 contra Asterisk: el servicio ari se reinicia con una llamada conectada; al
    cortar, no se inventa un no-contacto y el encuestador queda clasificando."""
    import os
    import subprocess

    comando = os.environ.get("LAB_REINICIO_ARI")
    if not comando:
        print("—    reinicio de ari con llamada en curso: se saltea (definí LAB_REINICIO_ARI)", flush=True)
        return
    e = estudio("preview", [HUMANO], [enc])
    conectar_puesto(enc, e)
    _caso, ll = llamar_preview(enc)
    comprobar("reinicio: la llamada está conectada antes de reiniciar", evento("llamada_conectada", ll) is not None)
    subprocess.run(comando, shell=True, check=True)
    time.sleep(5)
    with ari() as a:
        a.delete(f"/channels/{ll}")
    fin = evento("llamada_finalizada", ll, segundos=15)
    time.sleep(1)
    with db.transaccion() as cur:
        auto = db.uno(cur, "SELECT datos FROM invoke.evento WHERE llamada_id = %s AND tipo = 'disposicion_telefono'", (ll,))
    comprobar("reinicio: el fin de la llamada se registra con el ari nuevo", fin is not None)
    comprobar("reinicio: no se inventa una disposición de no-contacto (D2)", auto is None, f"disposición: {auto}")
    comprobar("reinicio: el encuestador queda clasificando",
              esperar("SELECT 1 FROM invoke.participante_estado WHERE participante_id = %s AND estado = 'clasificando'",
                      (enc.participante_id,), 10) is not None)


def main() -> None:
    print(f"Asterisk: {config().ari_url} · trunk: {config().trunk}\n", flush=True)
    enc, sup = usuario("Encuestadora lab", ["encuestador"]), usuario("Supervisor lab", ["supervisor"])
    con_opus = usuario("Encuestador con Opus", ["encuestador"])
    with db.transaccion() as cur:
        cur.execute("UPDATE invoke.participante SET opus_habilitado = true WHERE id = %s", (con_opus.participante_id,))
    if cli_asterisk("pjsip show endpoint trunk") is not None:
        comprobar("el trunk de pjsip.conf carga junto con los puestos de realtime",
                  "Endpoint:  trunk" in cli_asterisk("pjsip show endpoint trunk"))
    conectar_puesto(con_opus, estudio("preview", [], [con_opus]), disponible=False)
    preview_humano_con_escucha_y_grabacion(enc, sup)
    preview_sin_respuesta(enc, OCUPADO, 3, "ocupado")
    preview_sin_respuesta(enc, INEXISTENTE, 7, "número inexistente")
    preview_sin_respuesta(enc, NO_CONTESTA, 6, "no contesta")
    power(enc, HUMANO, True, "power con humano", "conectada")
    power(enc, HUMANO, False, "power sin encuestadores", "30")
    power(enc, CONTESTADOR, True, "contestador", "31")
    reinicio_con_llamada_en_curso(enc)
    malos = [r for r in resultados if not r[1]]
    print(f"\n{len(resultados) - len(malos)} de {len(resultados)} comprobaciones bien")
    sys.exit(1 if malos else 0)


if __name__ == "__main__":
    main()
