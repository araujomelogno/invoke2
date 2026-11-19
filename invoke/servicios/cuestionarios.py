"""Cuestionarios: borradores, publicación y versión vigente (ADR-006)."""
from __future__ import annotations

from .. import db
from ..dominio.cuestionario import validar_definicion
from ..enrutador import ErrorHttp


def vigente(cur, estudio_id: int) -> dict | None:
    return db.uno(cur, "SELECT * FROM invoke.cuestionario_vigente WHERE estudio_id = %s", (estudio_id,))


def versiones(cur, estudio_id: int) -> list[dict]:
    return db.todos(cur, """SELECT id, version, creado_en, publicado_en, definicion ->> 'nombre' AS nombre
                            FROM invoke.cuestionario WHERE estudio_id = %s ORDER BY version DESC""", (estudio_id,))


def obtener(cur, cuestionario_id: int) -> dict:
    c = db.uno(cur, "SELECT * FROM invoke.cuestionario WHERE id = %s", (cuestionario_id,))
    if not c:
        raise ErrorHttp(404, "cuestionario inexistente")
    return c


def guardar(cur, estudio_id: int, definicion: dict, autor: str) -> dict:
    """Guarda como borrador: reemplaza el último borrador o abre una versión nueva."""
    errores = validar_definicion(definicion)
    if errores:
        raise ErrorHttp(422, "cuestionario inválido: " + "; ".join(errores[:10]))
    borrador = db.uno(cur, """SELECT id FROM invoke.cuestionario WHERE estudio_id = %s AND publicado_en IS NULL
                              ORDER BY version DESC LIMIT 1""", (estudio_id,))
    if borrador:
        return db.uno(cur, "UPDATE invoke.cuestionario SET definicion = %s, creado_por = %s WHERE id = %s RETURNING *",
                      (db.Jsonb(definicion), autor, borrador["id"]))
    return db.uno(cur, """
        INSERT INTO invoke.cuestionario (estudio_id, version, definicion, creado_por)
        VALUES (%s, COALESCE((SELECT max(version) FROM invoke.cuestionario WHERE estudio_id = %s), 0) + 1, %s, %s)
        RETURNING *""", (estudio_id, estudio_id, db.Jsonb(definicion), autor))


def publicar(cur, cuestionario_id: int) -> dict:
    c = obtener(cur, cuestionario_id)
    errores = validar_definicion(c["definicion"])
    if errores:
        raise ErrorHttp(422, "no se publica un cuestionario inválido: " + "; ".join(errores[:10]))
    return db.uno(cur, "UPDATE invoke.cuestionario SET publicado_en = now() WHERE id = %s RETURNING *", (cuestionario_id,))
