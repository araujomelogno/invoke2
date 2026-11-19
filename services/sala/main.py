"""Gateway de WebSocket de la sala (Cloud Run, mínimo de instancias ≥ 1).

Empuja a los navegadores lo que pasa en la base, sin que pregunten:
  * al encuestador: su caso asignado y su llamada conectada (camino H del PRD:
    pantalla lista en menos de un segundo desde que el respondente atiende),
    timbre, fin de llamada;
  * al supervisor: la foto de la sala y los indicadores del discador.

Fuente: LISTEN invoke_eventos (pg_notify del trigger de eventos). No hay estado
propio: si el proceso reinicia, los navegadores se reconectan y piden la foto.
El espejo a Firebase (ADR-001, excepción) es opcional: INVOKE_ESPEJO_FIREBASE=1.

Protocolo: el cliente abre /sala y manda {"token": "<id token de Firebase>",
"estudio": <id opcional, supervisores>}. Después solo recibe mensajes
{"tipo": ..., ...}. Envía {"tipo": "ping"} para mantener viva la conexión.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route, WebSocketRoute  # noqa: E402
from starlette.websockets import WebSocket, WebSocketDisconnect  # noqa: E402

from invoke import db  # noqa: E402
from invoke.auth import uid_de_token, usuario_por_uid  # noqa: E402
from invoke.config import config  # noqa: E402
from invoke.enrutador import ErrorHttp, _serializar  # noqa: E402
from invoke.servicios import muestra, supervision  # noqa: E402

log = logging.getLogger("invoke.sala")

PARA_ENCUESTADOR = {"caso_asignado", "llamada_originada", "llamada_timbrando", "llamada_conectada",
                    "llamada_finalizada", "disposicion_telefono", "audio_conectado", "audio_desconectado",
                    "escucha_iniciada", "escucha_finalizada", "estado_participante"}
PARA_SUPERVISOR = PARA_ENCUESTADOR | {"sesion_iniciada", "sesion_finalizada", "disposicion_encuesta",
                                      "llamada_abandonada", "pacer_ajuste", "llamada_descartada"}


class Sala:
    def __init__(self) -> None:
        self.encuestadores: dict[int, set[WebSocket]] = defaultdict(set)
        self.supervisores: dict[WebSocket, int | None] = {}
        self._pendiente_foto: set[int | None] = set()

    async def enviar(self, ws: WebSocket, datos: dict) -> None:
        try:
            await ws.send_text(json.dumps(datos, default=_serializar, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - el socket se limpia al desconectar
            pass

    async def al_evento(self, aviso: dict) -> None:
        tipo = aviso["tipo"]
        pid = aviso.get("participante_id")
        if pid and tipo in PARA_ENCUESTADOR and self.encuestadores.get(pid):
            mensaje = {"tipo": tipo, **aviso}
            if tipo in ("llamada_conectada", "caso_asignado") and aviso.get("caso_id"):
                # La ficha viaja con el aviso: la estación no hace un segundo viaje.
                mensaje["caso"] = await asyncio.to_thread(self._ficha, aviso["caso_id"])
            for ws in list(self.encuestadores[pid]):
                await self.enviar(ws, mensaje)
        if tipo in PARA_SUPERVISOR and self.supervisores:
            self._pendiente_foto.add(aviso.get("estudio_id"))

    @staticmethod
    def _ficha(caso_id: int) -> dict:
        with db.transaccion() as cur:
            return muestra.ficha_caso(cur, caso_id)

    @staticmethod
    def _foto(estudio_id: int | None) -> dict:
        with db.transaccion() as cur:
            foto = {"tipo": "sala", "puestos": supervision.sala(cur, estudio_id)}
            if estudio_id:
                foto["discador"] = supervision.indicadores_discador(cur, estudio_id)
            return foto

    async def difundir_fotos(self) -> None:
        """Como mucho una foto por segundo por estudio: la consola no necesita más."""
        while True:
            await asyncio.sleep(1)
            if not self._pendiente_foto:
                continue
            self._pendiente_foto.clear()
            por_estudio: dict[int | None, list[WebSocket]] = defaultdict(list)
            for ws, est in self.supervisores.items():
                por_estudio[est].append(ws)
            for est, sockets in por_estudio.items():
                try:
                    foto = await asyncio.to_thread(self._foto, est)
                except Exception:  # noqa: BLE001
                    log.exception("no se pudo armar la foto de la sala")
                    continue
                for ws in sockets:
                    await self.enviar(ws, foto)


sala = Sala()


async def escuchar_base() -> None:
    import psycopg

    cfg = config()
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(cfg.database_url, autocommit=True) as conn:
                await conn.execute("LISTEN invoke_eventos")
                log.info("escuchando invoke_eventos")
                async for n in conn.notifies():
                    try:
                        aviso = json.loads(n.payload)
                        await sala.al_evento(aviso)
                        if cfg.espejo_firebase and aviso.get("participante_id"):
                            asyncio.create_task(asyncio.to_thread(espejar_firebase, aviso))
                    except Exception:  # noqa: BLE001
                        log.exception("aviso no procesado")
        except Exception:  # noqa: BLE001
            log.exception("LISTEN caído; reintento en 2 s")
            await asyncio.sleep(2)


def espejar_firebase(aviso: dict) -> None:
    """Pinta presencia en Firestore. Nunca es fuente de verdad (ADR-001)."""
    from firebase_admin import firestore

    from invoke.auth import _firebase

    _firebase()
    with db.transaccion() as cur:
        pe = db.uno(cur, "SELECT estado, estudio_id, desde, caso_id FROM invoke.participante_estado WHERE participante_id = %s",
                    (aviso["participante_id"],))
    if pe:
        firestore.client().collection("presencia").document(str(aviso["participante_id"])).set(
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in pe.items()})


async def ws_sala(ws: WebSocket) -> None:
    await ws.accept()
    try:
        hola = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        if config().auth_dev and hola.get("dev_uid"):
            uid = hola["dev_uid"]
        else:
            uid = await asyncio.to_thread(uid_de_token, hola.get("token", ""))
        usuario = await asyncio.to_thread(usuario_por_uid, uid)
    except (ErrorHttp, ValueError, asyncio.TimeoutError, KeyError):
        await ws.close(code=4401)
        return
    es_supervisor = usuario.tiene("supervisor", "administrador") and hola.get("rol") == "supervisor"
    if es_supervisor:
        sala.supervisores[ws] = hola.get("estudio")
        await sala.enviar(ws, await asyncio.to_thread(sala._foto, hola.get("estudio")))
    elif usuario.participante_id:
        sala.encuestadores[usuario.participante_id].add(ws)
    await sala.enviar(ws, {"tipo": "bienvenida", "participante_id": usuario.participante_id, "supervisor": es_supervisor})
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("tipo") == "ping":
                await sala.enviar(ws, {"tipo": "pong"})
            elif msg.get("tipo") == "estudio" and es_supervisor:
                sala.supervisores[ws] = msg.get("estudio")
                await sala.enviar(ws, await asyncio.to_thread(sala._foto, msg.get("estudio")))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        sala.supervisores.pop(ws, None)
        if usuario.participante_id:
            sala.encuestadores[usuario.participante_id].discard(ws)


async def salud(_request):
    return PlainTextResponse("ok")


@contextlib.asynccontextmanager
async def ciclo_de_vida(_app):
    tareas = [asyncio.create_task(escuchar_base()), asyncio.create_task(sala.difundir_fotos())]
    yield
    for t in tareas:
        t.cancel()


app = Starlette(routes=[Route("/salud", salud), WebSocketRoute("/sala", ws_sala)], lifespan=ciclo_de_vida)

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
