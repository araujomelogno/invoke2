"""Exportación de respuestas: .sav (SPSS), CSV y Excel, con el origen del caso en
cada registro (para poder excluir los fuera de marco del análisis ponderado)."""
from __future__ import annotations

import csv
import io
import os
import tempfile
from typing import Any

from .. import db
from ..enrutador import ErrorHttp

COLUMNAS_FIJAS = ["caso_id", "identificador", "origen", "caso_origen", "estrato", "area", "guia",
                  "grupo_calculo", "disposicion_encuesta", "disposicion_telefono", "intentos",
                  "encuestador_cierre", "version_cuestionario", "ponderable"]


def _variables(definicion: dict) -> tuple[list[str], dict[str, dict]]:
    """Variables en el orden del cuestionario, con su metadato para etiquetas SPSS."""
    orden: list[str] = []
    meta: dict[str, dict] = {}
    for b in definicion["bloques"]:
        for p in b["pantallas"]:
            for q in p["preguntas"]:
                etiquetas = {o["codigo"]: o["texto"] for o in q.get("opciones", [])}
                if q["tipo"] == "grilla":
                    for f in q.get("filas", []):
                        v = f"{q['id']}.{f['codigo']}"
                        orden.append(v)
                        meta[v] = {"texto": f"{q.get('texto', '')} — {f['texto']}", "etiquetas": etiquetas}
                elif q["tipo"] == "multiple":
                    for o in q.get("opciones", []):
                        v = f"{q['id']}_{o['codigo']}"
                        orden.append(v)
                        meta[v] = {"texto": f"{q.get('texto', '')} — {o['texto']}", "etiquetas": {"0": "No", "1": "Sí"},
                                   "multiple_de": q["id"], "codigo": o["codigo"]}
                else:
                    orden.append(q["id"])
                    meta[q["id"]] = {"texto": q.get("texto", ""), "etiquetas": etiquetas}
                if any(o.get("otro") for o in q.get("opciones", [])):
                    orden.append(f"{q['id']}.otro")
                    meta[f"{q['id']}.otro"] = {"texto": f"{q.get('texto', '')} — otro (especificar)", "etiquetas": {}}
    return orden, meta


def datos(cur, estudio_id: int, solo_completas: bool = True) -> tuple[list[str], list[dict], dict]:
    cq = db.uno(cur, "SELECT * FROM invoke.cuestionario_vigente WHERE estudio_id = %s", (estudio_id,))
    if not cq:
        raise ErrorHttp(409, "el estudio no tiene cuestionario publicado")
    variables, meta = _variables(cq["definicion"])
    casos = db.todos(cur, """
        SELECT k.caso_id, c.identificador, c.origen, c.caso_origen, c.estrato, c.area, g.nombre AS guia,
               k.simbolo AS grupo_calculo, k.ultima_disp_enc AS disposicion_encuesta,
               k.ultima_disp_tel AS disposicion_telefono, k.intentos, p.nombre AS encuestador_cierre
        FROM invoke.clasificar_casos(%s, 'parcial', clock_timestamp()) k
        JOIN invoke.caso c ON c.id = k.caso_id
        JOIN invoke.guia g ON g.id = c.guia_id
        LEFT JOIN invoke.participante p ON p.id = k.participante_cierre
        WHERE (NOT %s OR k.simbolo = 'C')
        ORDER BY k.caso_id""", (estudio_id, solo_completas))
    ids = [c["caso_id"] for c in casos]
    respuestas: dict[int, dict[str, Any]] = {}
    if ids:
        for r in db.todos(cur, "SELECT caso_id, variable, valor FROM invoke.respuesta_vigente WHERE caso_id = ANY(%s)", (ids,)):
            respuestas.setdefault(r["caso_id"], {})[r["variable"]] = r["valor"]
    filas = []
    for c in casos:
        r = respuestas.get(c["caso_id"], {})
        fila = dict(c)
        fila["version_cuestionario"] = cq["version"]
        # Política: un caso fuera de marco no tiene peso de diseño y queda fuera del análisis ponderado.
        fila["ponderable"] = 1 if c["origen"] == "marco" else 0
        for v in variables:
            m = meta[v]
            if "multiple_de" in m:
                elegidas = r.get(m["multiple_de"]) or []
                fila[v] = 1 if m["codigo"] in [str(x) for x in elegidas] else 0
            else:
                val = r.get(v)
                fila[v] = ",".join(map(str, val)) if isinstance(val, list) else val
        filas.append(fila)
    return COLUMNAS_FIJAS + variables, filas, meta


def a_csv(columnas: list[str], filas: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columnas, extrasaction="ignore")
    w.writeheader()
    w.writerows(filas)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def a_xlsx(columnas: list[str], filas: list[dict], meta: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "datos"
    ws.append(columnas)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="E96436")
    for f in filas:
        ws.append([f.get(c) for c in columnas])
    ws.freeze_panes = "B2"
    dic = wb.create_sheet("diccionario")
    dic.append(["variable", "texto", "código", "etiqueta"])
    for v in columnas:
        m = meta.get(v)
        if not m:
            continue
        if m["etiquetas"]:
            for cod, et in m["etiquetas"].items():
                dic.append([v, m["texto"], cod, et])
        else:
            dic.append([v, m["texto"], None, None])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _nombre_spss(v: str, usados: set[str]) -> str:
    base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in v)
    if not base[0].isalpha():
        base = "V" + base
    base = base[:60]
    nombre, i = base, 1
    while nombre.lower() in usados:
        i += 1
        nombre = f"{base[:56]}_{i}"
    usados.add(nombre.lower())
    return nombre


def a_sav(columnas: list[str], filas: list[dict], meta: dict) -> bytes:
    import pandas as pd
    import pyreadstat

    usados: set[str] = set()
    nombres = {c: _nombre_spss(c, usados) for c in columnas}
    df = pd.DataFrame([{nombres[c]: f.get(c) for c in columnas} for f in filas], columns=list(nombres.values()))
    etiquetas_var = {nombres[c]: (meta.get(c, {}).get("texto") or c)[:255] for c in columnas}
    etiquetas_val = {}
    for c in columnas:
        m = meta.get(c)
        if m and m["etiquetas"] and all(str(k).lstrip("-").isdigit() for k in m["etiquetas"]):
            col = nombres[c]
            df[col] = pd.to_numeric(df[col], errors="coerce")
            etiquetas_val[col] = {int(k): v[:120] for k, v in m["etiquetas"].items()}
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, "datos.sav")
        pyreadstat.write_sav(df, ruta, column_labels=etiquetas_var, variable_value_labels=etiquetas_val)
        with open(ruta, "rb") as fh:
            return fh.read()


TIPOS = {
    "csv": ("text/csv; charset=utf-8", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "sav": ("application/x-spss-sav", "sav"),
}


def exportar(cur, estudio_id: int, formato: str, solo_completas: bool = True) -> tuple[bytes, str, str]:
    if formato not in TIPOS:
        raise ErrorHttp(400, "formato: csv, xlsx o sav")
    columnas, filas, meta = datos(cur, estudio_id, solo_completas)
    codigo = db.uno(cur, "SELECT codigo FROM invoke.estudio WHERE id = %s", (estudio_id,))["codigo"]
    contenido = {"csv": lambda: a_csv(columnas, filas), "xlsx": lambda: a_xlsx(columnas, filas, meta),
                 "sav": lambda: a_sav(columnas, filas, meta)}[formato]()
    tipo, ext = TIPOS[formato]
    return contenido, tipo, f"{codigo}.{ext}"
