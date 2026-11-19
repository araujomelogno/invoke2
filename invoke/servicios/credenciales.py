"""Credenciales efímeras del puesto: SIP (Asterisk realtime) y TURN (coturn REST).

Cero instalación en el puesto: el navegador recibe, al iniciar sesión, una clave
SIP nueva (válida hasta el próximo inicio) y credenciales TURN con vencimiento.
En la base solo queda el hash md5(usuario:realm:clave); nunca la clave.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from .. import db
from ..config import config


def endpoint_de(participante_id: int) -> str:
    return f"p{participante_id}"


def rotar_sip(cur, participante_id: int, nombre: str) -> dict:
    cfg = config()
    ep = endpoint_de(participante_id)
    clave = secrets.token_urlsafe(18)
    realm = cfg.sip_dominio
    md5 = hashlib.md5(f"{ep}:{realm}:{clave}".encode()).hexdigest()
    cur.execute("""INSERT INTO asterisk.ps_aors (id) VALUES (%s) ON CONFLICT (id) DO NOTHING""", (ep,))
    cur.execute("""INSERT INTO asterisk.ps_auths (id, username, realm, md5_cred) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET md5_cred = EXCLUDED.md5_cred, realm = EXCLUDED.realm""",
                (ep, ep, realm, md5))
    allow = codecs_de(cur, participante_id)
    cur.execute("""INSERT INTO asterisk.ps_endpoints (id, aors, auth, callerid, allow) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET callerid = EXCLUDED.callerid, allow = EXCLUDED.allow""",
                (ep, ep, ep, f'"{nombre}" <{ep}>', allow))
    return {"uri": f"sip:{ep}@{cfg.sip_dominio}", "usuario": ep, "clave": clave, "wss": cfg.sip_wss,
            "realm": realm, "destino_sala": f"sip:sala-{participante_id}@{cfg.sip_dominio}", "codecs": allow}


# ADR-004 y encargo de cierre (D1, H4): G.711 extremo a extremo por defecto, para que
# Asterisk solo puentee. Opus solo en los puestos que lo tengan habilitado.
CODECS_G711 = "alaw,ulaw"
CODECS_CON_OPUS = "opus,alaw,ulaw"


def codecs_de(cur, participante_id: int) -> str:
    cur.execute("SELECT opus_habilitado FROM invoke.participante WHERE id = %s", (participante_id,))
    fila = cur.fetchone()
    opus = bool(fila and (fila["opus_habilitado"] if isinstance(fila, dict) else fila[0]))
    return CODECS_CON_OPUS if opus else CODECS_G711


def ice(usuario_uid: str, vigencia_s: int = 12 * 3600, ahora: float | None = None) -> list[dict]:
    """Servidores ICE. TURN con el mecanismo use-auth-secret de coturn."""
    cfg = config()
    servidores: list[dict] = [{"urls": list(cfg.stun_urls)}] if cfg.stun_urls else []
    if cfg.turn_urls and cfg.turn_secreto:
        vence = int((ahora or time.time()) + vigencia_s)
        usuario = f"{vence}:{usuario_uid}"
        firma = hmac.new(cfg.turn_secreto.encode(), usuario.encode(), hashlib.sha1).digest()
        servidores.append({"urls": list(cfg.turn_urls), "username": usuario,
                           "credential": base64.b64encode(firma).decode()})
    return servidores


__all__ = ["endpoint_de", "ice", "rotar_sip", "db"]
