"""Usuarios y roles. Firebase Auth autentica; los roles viven en Postgres."""
from __future__ import annotations

from .. import db
from ..enrutador import ErrorHttp

ROLES = ("encuestador", "supervisor", "administrador", "investigador")


def listar(cur) -> list[dict]:
    return db.todos(cur, """SELECT u.*, p.id AS participante_id, COALESCE(p.opus_habilitado, false) AS opus_habilitado
                            FROM invoke.usuario u
                            LEFT JOIN invoke.participante p ON p.usuario_uid = u.uid ORDER BY u.nombre""")


def _uid_firebase(email: str, nombre: str, clave: str | None) -> str:
    from firebase_admin import auth as fb

    from ..auth import _firebase

    _firebase()
    try:
        return fb.get_user_by_email(email).uid
    except fb.UserNotFoundError:
        if not clave:
            raise ErrorHttp(400, "el usuario no existe en Firebase: indicá una clave inicial") from None
        return fb.create_user(email=email, password=clave, display_name=nombre).uid


def guardar(cur, datos: dict) -> dict:
    email = (datos.get("email") or "").strip().lower()
    nombre = (datos.get("nombre") or "").strip()
    roles = [r for r in datos.get("roles", []) if r in ROLES]
    if not email or not nombre or not roles:
        raise ErrorHttp(400, "email, nombre y al menos un rol")
    uid = datos.get("uid") or _uid_firebase(email, nombre, datos.get("clave_inicial"))
    u = db.uno(cur, """
        INSERT INTO invoke.usuario (uid, email, nombre, roles, activo) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (uid) DO UPDATE SET email = EXCLUDED.email, nombre = EXCLUDED.nombre,
                                        roles = EXCLUDED.roles, activo = EXCLUDED.activo
        RETURNING *""", (uid, email, nombre, roles, datos.get("activo", True)))
    if "encuestador" in roles or "supervisor" in roles:
        # H4: Opus se habilita por puesto; toma efecto en el próximo inicio de sesión
        # (credenciales.rotar_sip reescribe ps_endpoints.allow).
        cur.execute("""INSERT INTO invoke.participante (tipo, usuario_uid, nombre, opus_habilitado)
                       VALUES ('encuestador_webrtc', %s, %s, %s)
                       ON CONFLICT (usuario_uid) DO UPDATE SET nombre = EXCLUDED.nombre,
                                                               opus_habilitado = EXCLUDED.opus_habilitado""",
                    (uid, nombre, bool(datos.get("opus_habilitado", False))))
    return u
