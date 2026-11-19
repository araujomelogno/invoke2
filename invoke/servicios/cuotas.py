"""Cuotas por pregunta, área y propiedad. El avance es una consulta, nunca un contador."""
from __future__ import annotations

from .. import db
from ..enrutador import ErrorHttp

CAMPOS = ("nombre", "tipo", "pregunta", "propiedad", "valores", "objetivo", "porcentaje", "activa")


def avance(cur, estudio_id: int) -> list[dict]:
    return db.todos(cur, "SELECT * FROM invoke.cuota_avance WHERE estudio_id = %s ORDER BY nombre", (estudio_id,))


def crear(cur, estudio_id: int, datos: dict) -> dict:
    d = {k: datos.get(k) for k in CAMPOS if k in datos}
    if isinstance(d.get("valores"), str):
        d["valores"] = [v.strip() for v in d["valores"].split(",") if v.strip()]
    cols = ", ".join(["estudio_id", *d])
    return db.uno(cur, f"INSERT INTO invoke.cuota ({cols}) VALUES (%s{', %s' * len(d)}) RETURNING *",
                  (estudio_id, *d.values()))


def actualizar(cur, cuota_id: int, datos: dict) -> dict:
    d = {k: datos[k] for k in CAMPOS if k in datos}
    if not d:
        raise ErrorHttp(400, "nada para actualizar")
    sets = ", ".join(f"{k} = %s" for k in d)
    q = db.uno(cur, f"UPDATE invoke.cuota SET {sets} WHERE id = %s RETURNING *", (*d.values(), cuota_id))
    if not q:
        raise ErrorHttp(404, "cuota inexistente")
    return q


def borrar(cur, cuota_id: int) -> None:
    cur.execute("DELETE FROM invoke.cuota WHERE id = %s", (cuota_id,))


def evaluar(cur, caso_id: int, respuestas: dict) -> list[dict]:
    """Cuotas llenas en las que el caso caería con las respuestas en curso."""
    return db.todos(cur, "SELECT * FROM invoke.evaluar_cuotas(%s, %s)", (caso_id, db.Jsonb(respuestas)))
