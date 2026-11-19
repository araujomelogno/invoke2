"""Servidor de desarrollo: web/ estático + /api (enrutador) + /sala (WebSocket).

    DATABASE_URL=postgresql://... INVOKE_AUTH_DEV=1 python scripts/servidor_local.py

Con INVOKE_AUTH_DEV=1 la web acepta ?dev_uid=<uid> en lugar de Firebase Auth.
Nunca se habilita en producción: Cloud Functions no lee esa variable del entorno
de despliegue (CLAUDE.md).
"""
from __future__ import annotations

import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.routing import Mount, Route, WebSocketRoute  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from invoke.api import despachar  # noqa: E402
from invoke.enrutador import Peticion  # noqa: E402
from services.sala.main import ciclo_de_vida, ws_sala  # noqa: E402


async def api(request: Request) -> Response:
    import asyncio

    p = Peticion(request.method, request.url.path, dict(request.query_params), await request.body(),
                 dict(request.headers))
    r = await asyncio.to_thread(despachar, p)
    return Response(r.cuerpo, r.estado, r.cabeceras, media_type=r.tipo)


app = Starlette(
    routes=[
        Route("/api/{ruta:path}", api, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        WebSocketRoute("/sala", ws_sala),
        Mount("/", StaticFiles(directory=RAIZ / "web", html=True)),
    ],
    lifespan=ciclo_de_vida,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
