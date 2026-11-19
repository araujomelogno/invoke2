"""Grabaciones en el bucket: dónde está cada una y cómo escucharla (encargo de cierre, H2).

La subida la hace services/ari/grabaciones.py; acá solo se lee el log y se firma
una URL temporal para la consola de supervisión.
"""
from __future__ import annotations

from .. import db
from ..enrutador import ErrorHttp


def de_llamada(cur, llamada_id: str) -> dict:
    g = db.uno(cur, "SELECT * FROM invoke.grabacion_en_bucket WHERE llamada_id = %s", (llamada_id,))
    if g:
        return {"estado": "en_bucket", **g}
    if db.uno(cur, "SELECT 1 FROM invoke.grabacion_pendiente WHERE llamada_id = %s", (llamada_id,)):
        return {"estado": "pendiente_de_subida"}
    raise ErrorHttp(404, "esa llamada no tiene grabación")


def escuchar(cur, llamada_id: str) -> dict:
    g = de_llamada(cur, llamada_id)
    if g["estado"] != "en_bucket":
        raise ErrorHttp(409, "la grabación todavía está en el PBX; se sube en unos minutos")
    return {"url": url_firmada(g["uri"]), "vence_en_minutos": 15}


def url_firmada(uri: str, minutos: int = 15) -> str:
    """URL temporal para escuchar una grabación desde la consola.

    En Cloud Functions y Cloud Run no hay clave privada: se firma con la cuenta de
    servicio vía IAM (requiere roles/iam.serviceAccountTokenCreator sobre sí misma).
    """
    import datetime as dt

    import google.auth
    from google.auth.transport import requests as gar
    from google.cloud import storage

    if not uri.startswith("gs://"):
        raise ValueError("uri inválida")
    nombre_bucket, objeto = uri[5:].split("/", 1)
    credenciales, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credenciales.refresh(gar.Request())
    blob = storage.Client(credentials=credenciales).bucket(nombre_bucket).blob(objeto)
    return blob.generate_signed_url(
        version="v4", expiration=dt.timedelta(minutes=minutos), method="GET",
        service_account_email=getattr(credenciales, "service_account_email", None),
        access_token=credenciales.token)
