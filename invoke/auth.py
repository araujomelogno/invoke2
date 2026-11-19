"""Autenticación con Firebase Auth (ADR-010) y resolución de roles desde Postgres.

El token de Firebase prueba quién es; los roles y el participante salen de
invoke.usuario, no de custom claims, para que haya una sola fuente de verdad.
"""
from __future__ import annotations

import functools

from . import db
from .config import config
from .enrutador import ErrorHttp, Peticion, Usuario


@functools.lru_cache(maxsize=1)
def _firebase():
    import firebase_admin
    from firebase_admin import auth as fb_auth

    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": config().proyecto_firebase} if config().proyecto_firebase else None)
    return fb_auth


def uid_de_token(token: str) -> str:
    try:
        datos = _firebase().verify_id_token(token, check_revoked=False)
    except Exception as e:  # noqa: BLE001 - cualquier fallo de verificación es 401
        raise ErrorHttp(401, "sesión inválida o vencida") from e
    return datos["uid"]


def usuario_por_uid(uid: str) -> Usuario:
    with db.transaccion() as cur:
        u = db.uno(cur, """SELECT u.uid, u.email, u.nombre, u.roles, u.activo, p.id AS participante_id
                           FROM invoke.usuario u LEFT JOIN invoke.participante p ON p.usuario_uid = u.uid
                           WHERE u.uid = %s""", (uid,))
    if not u or not u["activo"]:
        raise ErrorHttp(403, "usuario no habilitado en INVOKE 2")
    return Usuario(u["uid"], u["email"], u["nombre"], list(u["roles"]), u["participante_id"])


def autenticar(p: Peticion) -> Usuario:
    cab = p.cabecera("Authorization") or ""
    if cab.startswith("Bearer "):
        return usuario_por_uid(uid_de_token(cab[7:]))
    if config().auth_dev and p.cabecera("X-Dev-Uid"):
        return usuario_por_uid(p.cabecera("X-Dev-Uid"))
    if config().auth_dev and p.query.get("dev_uid"):
        return usuario_por_uid(p.query["dev_uid"])
    raise ErrorHttp(401, "falta el token de sesión")
