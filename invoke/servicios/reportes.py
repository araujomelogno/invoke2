"""Reportes del piloto, derivados del diccionario y del documento de fórmulas.

Solo lo que el estudio piloto usa (riesgo de alcance de la Fase 1): ficha técnica
con tasas, productividad, cuotas, fuera de marco y calibración del AMD.
"""
from __future__ import annotations

import datetime as dt

from .. import db
from ..dominio import formulas
from ..enrutador import ErrorHttp


def _hasta(valor: str | None) -> dt.datetime:
    if not valor:
        return dt.datetime.now(dt.timezone.utc)
    try:
        t = dt.datetime.fromisoformat(valor)
    except ValueError as e:
        raise ErrorHttp(400, "fecha inválida (ISO 8601)") from e
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def ficha_tecnica(cur, estudio_id: int, corte: str = "parcial", hasta: str | None = None) -> dict:
    if corte not in ("parcial", "cierre"):
        raise ErrorHttp(400, "corte: parcial o cierre")
    est = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (estudio_id,))
    if not est:
        raise ErrorHttp(404, "estudio inexistente")
    if not est["es_estudio"]:
        raise ErrorHttp(409, "este registro está marcado como prueba/capacitación: no tiene reporte")
    if corte == "cierre" and est["estado"] != "cerrado":
        raise ErrorHttp(409, "el reporte de cierre se pide con el estudio cerrado")
    t = _hasta(hasta)
    conteo = db.uno(cur, "SELECT invoke.conteo_tasas(%s, %s, %s) AS c", (estudio_id, corte, t))["c"]
    abandono = None
    if est["modo_discado"] in ("power", "predictivo"):
        abandono = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, %s, %s)", (estudio_id, est["creado_en"], t))
    return formulas.ficha_tecnica(conteo, est, abandono)


def productividad(cur, estudio_id: int, desde: str | None, hasta: str | None) -> list[dict]:
    t1 = _hasta(hasta)
    t0 = _hasta(desde) if desde else t1 - dt.timedelta(days=1)
    filas = db.todos(cur, "SELECT * FROM invoke.productividad(%s, %s, %s)", (estudio_id, t0, t1))
    for f in filas:
        f.update(formulas.productividad(f["completas"], float(f["horas_sesion"]), float(f["horas_en_llamada"])))
        f["cooperacion"] = formulas.Tasa("Cooperación", f["completas"],
                                         f["completas"] + f["interrumpidas"] + f["rechazos"]).como_dict()
    return filas


def fuera_de_marco(cur, estudio_id: int) -> dict:
    return {
        "por_origen": db.todos(cur, """
            SELECT c.origen, count(*) AS casos, count(*) FILTER (WHERE d.codigo = 3) AS completas
            FROM invoke.caso c LEFT JOIN invoke.caso_ultima_disp_enc d ON d.caso_id = c.id
            WHERE c.estudio_id = %s GROUP BY c.origen ORDER BY c.origen""", (estudio_id,)),
        "alertas_por_estrato": db.todos(cur, "SELECT * FROM invoke.alerta_derivados WHERE estudio_id = %s ORDER BY estrato",
                                        (estudio_id,)),
        "cadena": db.todos(cur, """
            WITH RECURSIVE cadena AS (
              SELECT id, caso_origen, 1 AS nivel, ARRAY[id] AS ruta FROM invoke.caso
              WHERE estudio_id = %s AND origen = 'marco'
                AND id IN (SELECT caso_origen FROM invoke.caso WHERE estudio_id = %s AND caso_origen IS NOT NULL)
              UNION ALL
              SELECT c.id, c.caso_origen, cadena.nivel + 1, cadena.ruta || c.id
              FROM invoke.caso c JOIN cadena ON c.caso_origen = cadena.id
            )
            SELECT ruta, nivel FROM cadena WHERE nivel > 1 ORDER BY ruta""", (estudio_id, estudio_id)),
    }


def calibracion_amd(cur, estudio_id: int) -> dict:
    fila = db.uno(cur, "SELECT * FROM invoke.calibracion_amd WHERE estudio_id = %s", (estudio_id,)) or {
        "detectadas": 0, "falsos_positivos": 0}
    revisadas = db.uno(cur, """SELECT count(*) AS n FROM invoke.llamada WHERE estudio_id = %s
                               AND disp_tel_original = 31 AND grabacion IS NOT NULL""", (estudio_id,))["n"]
    return {**fila, "con_grabacion": revisadas,
            "tasa_falsos_positivos": formulas.Tasa("Falsos positivos del AMD", fila["falsos_positivos"],
                                                  fila["detectadas"]).como_dict()}


def cdr(cur, estudio_id: int | None, desde: str | None, hasta: str | None, limite: int = 5000) -> list[dict]:
    t1 = _hasta(hasta)
    t0 = _hasta(desde) if desde else t1 - dt.timedelta(days=7)
    return db.todos(cur, """
        SELECT * FROM invoke.cdr_compat
        WHERE calldate >= %s AND calldate < %s AND (%s::bigint IS NULL OR campana = (SELECT codigo FROM invoke.estudio WHERE id = %s))
        ORDER BY calldate LIMIT %s""", (t0, t1, estudio_id, estudio_id, limite))
