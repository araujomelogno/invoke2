"""Configuración por variables de entorno (sin secretos en el código: CLAUDE.md).

Se lee en cada llamada a config(), no al importar, para que los tests y los
entornos puedan cambiarla sin reiniciar el proceso.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(nombre: str, defecto: str = "") -> str:
    return os.environ.get(nombre, defecto)


def _bool(nombre: str, defecto: bool = False) -> bool:
    return _env(nombre, str(defecto)).strip().lower() in ("1", "true", "si", "sí", "yes")


def _lista(nombre: str, defecto: str = "") -> tuple[str, ...]:
    return tuple(u.strip() for u in _env(nombre, defecto).split(",") if u.strip())


@dataclass(frozen=True)
class Config:
    database_url: str
    rol_db: str
    proyecto_firebase: str
    auth_dev: bool              # SOLO local: acepta X-Dev-Uid sin token
    zona_horaria: str
    ari_url: str
    ari_usuario: str
    ari_clave: str
    ari_app: str
    sip_wss: str
    sip_dominio: str
    trunk: str
    caller_id: str
    turn_urls: tuple[str, ...]
    turn_secreto: str
    stun_urls: tuple[str, ...]
    sala_ws_url: str
    espejo_firebase: bool
    pacer_intervalo_s: float
    grabaciones_bucket: str


def config() -> Config:
    return Config(
        database_url=_env("DATABASE_URL"),
        rol_db=_env("INVOKE_DB_ROL", "invoke_app"),
        proyecto_firebase=_env("FIREBASE_PROJECT", _env("GCLOUD_PROJECT")),
        auth_dev=_bool("INVOKE_AUTH_DEV"),
        zona_horaria=_env("INVOKE_TZ", "America/Montevideo"),
        ari_url=_env("ARI_URL", "http://127.0.0.1:8088/ari"),
        ari_usuario=_env("ARI_USUARIO", "invoke"),
        ari_clave=_env("ARI_CLAVE"),
        ari_app=_env("ARI_APP", "invoke"),
        sip_wss=_env("SIP_WSS", "wss://pbx.invoke.local:8089/ws"),
        sip_dominio=_env("SIP_DOMINIO", "pbx.invoke.local"),
        trunk=_env("INVOKE_TRUNK", "PJSIP/{numero}@trunk"),
        caller_id=_env("INVOKE_CALLER_ID"),
        turn_urls=_lista("TURN_URLS"),
        turn_secreto=_env("TURN_SECRETO"),
        stun_urls=_lista("STUN_URLS"),
        sala_ws_url=_env("SALA_WS_URL", "ws://127.0.0.1:8080/sala"),
        espejo_firebase=_bool("INVOKE_ESPEJO_FIREBASE"),
        pacer_intervalo_s=float(_env("PACER_INTERVALO_S", "2")),
        grabaciones_bucket=_env("GRABACIONES_BUCKET"),
    )
