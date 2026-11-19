"""Subida de grabaciones del PBX al bucket (encargo de cierre, H2).

Se graban todas las llamadas de encuesta conectadas. Asterisk las deja en el
disco local de su VM: si nadie las saca, el disco se llena en días. Este módulo
las baja por ARI (GET /recordings/stored/{nombre}/file), las sube a Cloud
Storage verificando el MD5, registra el hecho 'grabacion_subida' y recién
entonces las borra del PBX (DELETE /recordings/stored/{nombre}).

Corre dentro de services/ari porque es el único proceso que habla con Asterisk
(ADR-003). La lista de pendientes sale de la vista invoke.grabacion_pendiente:
si el proceso se reinicia, retoma donde quedó. Un archivo nunca se borra del PBX
antes de que su subida quede registrada en el log.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger("invoke.grabaciones")


class Bucket(Protocol):
    nombre: str

    def subir(self, objeto: str, datos: bytes, md5_b64: str) -> None: ...


class Pbx(Protocol):
    def bajar(self, nombre: str) -> bytes | None: ...      # None = no existe en el PBX
    def borrar(self, nombre: str) -> None: ...


@dataclass
class Pendiente:
    llamada_id: str
    estudio: str
    nombre: str
    ocurrido_en: object      # datetime


def objeto_de(p: Pendiente) -> str:
    """gs://<bucket>/<estudio>/<AAAA>/<MM>/<DD>/<llamada>.wav — se lista y se borra por estudio y por fecha."""
    f = p.ocurrido_en
    return f"{p.estudio}/{f:%Y}/{f:%m}/{f:%d}/{p.llamada_id}.wav"


def subir_pendientes(pendientes: list[Pendiente], pbx: Pbx, bucket: Bucket,
                     registrar: Callable[[Pendiente, dict], None]) -> dict:
    """Procesa un lote. Devuelve un resumen {subidas, faltantes, errores}."""
    resumen = {"subidas": 0, "faltantes": 0, "errores": 0}
    for p in pendientes:
        try:
            datos = pbx.bajar(p.nombre)
            if datos is None:
                # Ya no está en el PBX: puede ser una subida anterior que no llegó a
                # registrarse. Se informa y se reintenta en el próximo ciclo.
                log.warning("grabación %s no está en el PBX", p.nombre)
                resumen["faltantes"] += 1
                continue
            md5 = base64.b64encode(hashlib.md5(datos).digest()).decode()
            objeto = objeto_de(p)
            bucket.subir(objeto, datos, md5)        # Cloud Storage rechaza la subida si el MD5 no coincide
            registrar(p, {"uri": f"gs://{bucket.nombre}/{objeto}", "objeto": objeto, "bytes": len(datos), "md5": md5})
            pbx.borrar(p.nombre)                    # solo después de que la subida quedó en el log
            resumen["subidas"] += 1
        except Exception:  # noqa: BLE001 - un archivo con problemas no frena a los demás
            log.exception("no se pudo subir la grabación %s", p.nombre)
            resumen["errores"] += 1
    return resumen


class BucketGcs:
    """Bucket real de Cloud Storage."""

    def __init__(self, nombre: str):
        from google.cloud import storage

        self.nombre = nombre
        self._bucket = storage.Client().bucket(nombre)

    def subir(self, objeto: str, datos: bytes, md5_b64: str) -> None:
        blob = self._bucket.blob(objeto)
        blob.md5_hash = md5_b64
        blob.upload_from_string(datos, content_type="audio/wav", if_generation_match=0)


class PbxAri:
    """Grabaciones guardadas en Asterisk, vía ARI REST."""

    def __init__(self, http):
        self.http = http

    def bajar(self, nombre: str) -> bytes | None:
        r = self.http.get(f"/recordings/stored/{nombre}/file", timeout=60)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def borrar(self, nombre: str) -> None:
        r = self.http.delete(f"/recordings/stored/{nombre}")
        if r.status_code not in (204, 404):
            r.raise_for_status()
