"""Carga de guías (phonebook) con propiedades.

CSV con encabezado. Columnas reconocidas:
    telefono, telefono2, telefono3 ...   uno o más teléfonos del caso
    identificador                        id del caso en la base del cliente
    estrato, area                        para cuotas y reportes
    sustituto, grupo_sustituto           ranura y orden de sustitución (legacy)
Cualquier otra columna va a la ficha del caso (propiedades), disponible para
sustitución de texto en el cuestionario ({ficha.columna}) y para cuotas por propiedad.
"""
from __future__ import annotations

import csv
import io
import secrets

from .. import db
from ..enrutador import ErrorHttp

RESERVADAS = {"identificador", "estrato", "area", "sustituto", "grupo_sustituto"}


def _telefonos(fila: dict[str, str]) -> list[str]:
    claves = sorted((k for k in fila if k and k.lower().startswith("telefono")), key=lambda k: (len(k), k))
    return [fila[k].strip() for k in claves if fila.get(k) and fila[k].strip()]


def cargar_guia(cur, estudio_id: int, nombre: str, contenido_csv: str, autor: str, *,
                prioridad: int = 0, tope_fuera_marco: float | None = None,
                origen: str = "marco", separador: str | None = None, semilla: int | None = None) -> dict:
    """Carga una guía. El orden aleatorio de cada caso se deriva de (semilla, número de
    línea) (encargo de cierre H3): la misma guía con la misma semilla da el mismo orden.
    Sin semilla se genera una, que queda en la guía y en el evento 'muestra_cargada'."""
    if origen not in ("marco", "lista_externa"):
        raise ErrorHttp(400, "una guía cargada es del marco o una lista externa")
    if separador is None:
        primera = contenido_csv.split("\n", 1)[0]
        separador = ";" if primera.count(";") > primera.count(",") else ","
    lector = csv.DictReader(io.StringIO(contenido_csv.lstrip("﻿")), delimiter=separador)
    if not lector.fieldnames or not any(f.lower().startswith("telefono") for f in lector.fieldnames):
        raise ErrorHttp(400, "el CSV necesita al menos una columna telefono")
    if semilla is None:
        semilla = secrets.randbits(52)
    if not 0 <= int(semilla) < 2**63:
        raise ErrorHttp(400, "la semilla tiene que ser un entero no negativo")
    guia = db.uno(cur, """INSERT INTO invoke.guia (estudio_id, nombre, prioridad, tope_fuera_marco, semilla)
                          VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                  (estudio_id, nombre, prioridad, tope_fuera_marco, int(semilla)))
    casos = telefonos = 0
    rechazadas: list[dict] = []
    for n, fila in enumerate(lector, start=2):
        fila = {(k or "").strip().lower(): (v or "").strip() for k, v in fila.items()}
        tels = _telefonos(fila)
        if not tels:
            rechazadas.append({"linea": n, "motivo": "sin teléfono"})
            continue
        if bool(fila.get("sustituto")) != bool(fila.get("grupo_sustituto")):
            rechazadas.append({"linea": n, "motivo": "sustituto sin grupo (o al revés)"})
            continue
        props = {k: v for k, v in fila.items() if k and not k.startswith("telefono") and k not in RESERVADAS}
        cur.execute("SAVEPOINT fila")
        try:
            caso = db.uno(cur, """
                INSERT INTO invoke.caso (estudio_id, guia_id, origen, usuario_creador, identificador, propiedades,
                                         estrato, area, sustituto, grupo_sustituto, posicion)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (estudio_id, guia["id"], origen, autor if origen == "lista_externa" else None,
                 fila.get("identificador") or None, db.Jsonb(props), fila.get("estrato") or None,
                 fila.get("area") or None, fila.get("sustituto") or None,
                 int(fila["grupo_sustituto"]) if fila.get("grupo_sustituto") else None, n))
            for orden, t in enumerate(dict.fromkeys(tels), start=1):
                cur.execute("""INSERT INTO invoke.telefono (caso_id, estudio_id, numero, orden)
                               VALUES (%s, %s, %s, %s)""", (caso["id"], estudio_id, t, orden))
                telefonos += 1
            cur.execute("RELEASE SAVEPOINT fila")
            casos += 1
        except Exception as e:  # noqa: BLE001 - la fila se reporta y se sigue
            cur.execute("ROLLBACK TO SAVEPOINT fila")
            rechazadas.append({"linea": n, "motivo": getattr(getattr(e, "diag", None), "message_primary", None) or str(e)})
    en_lista_negra = db.uno(cur, """
        SELECT count(DISTINCT t.caso_id) AS n FROM invoke.telefono t
        JOIN invoke.caso c ON c.id = t.caso_id
        JOIN invoke.lista_negra l ON l.numero_normalizado = t.numero_normalizado
        WHERE c.guia_id = %s""", (guia["id"],))["n"]
    resumen = {"guia_id": guia["id"], "semilla": int(semilla), "casos": casos, "telefonos": telefonos,
               "rechazadas": rechazadas[:200], "total_rechazadas": len(rechazadas),
               "casos_con_numero_en_lista_negra": en_lista_negra}
    db.evento(cur, "muestra_cargada", estudio_id=estudio_id, autor=autor,
              datos={k: v for k, v in resumen.items() if k != "rechazadas"} | {"nombre": nombre, "origen": origen})
    return resumen


def actualizar_guia(cur, guia_id: int, datos: dict) -> dict:
    campos = {k: v for k, v in datos.items() if k in ("nombre", "prioridad", "activa", "tope_fuera_marco", "agendar_por_grupo")}
    if not campos:
        raise ErrorHttp(400, "nada para actualizar")
    sets = ", ".join(f"{k} = %s" for k in campos)
    g = db.uno(cur, f"UPDATE invoke.guia SET {sets} WHERE id = %s RETURNING *", (*campos.values(), guia_id))
    if not g:
        raise ErrorHttp(404, "guía inexistente")
    return g


def ficha_caso(cur, caso_id: int) -> dict:
    caso = db.uno(cur, """
        SELECT c.*, ce.estado, ce.intentos, ce.ultima_disp_tel, ce.ultima_disp_enc, ce.agenda_desde, ce.agenda_hasta,
               ce.abandonada_previa, ce.disponible_desde, g.nombre AS guia
        FROM invoke.caso c JOIN invoke.caso_estado ce ON ce.caso_id = c.id JOIN invoke.guia g ON g.id = c.guia_id
        WHERE c.id = %s""", (caso_id,))
    if not caso:
        raise ErrorHttp(404, "caso inexistente")
    caso["telefonos"] = db.todos(cur, """
        SELECT t.id, t.numero, t.orden, EXISTS (SELECT 1 FROM invoke.lista_negra l WHERE l.numero_normalizado = t.numero_normalizado) AS bloqueado
        FROM invoke.telefono t WHERE t.caso_id = %s ORDER BY t.orden""", (caso_id,))
    caso["historial"] = db.todos(cur, """
        SELECT l.originada_en, l.numero, l.disp_tel, dt.etiqueta AS disp_tel_etiqueta, l.modo,
               (SELECT (x.datos ->> 'codigo')::int FROM invoke.evento x WHERE x.llamada_id = l.llamada_id
                  AND x.tipo = 'disposicion_encuesta' ORDER BY x.id DESC LIMIT 1) AS disp_enc,
               (SELECT x.datos ->> 'motivo' FROM invoke.evento x WHERE x.llamada_id = l.llamada_id
                  AND x.tipo = 'disposicion_encuesta' ORDER BY x.id DESC LIMIT 1) AS nota,
               p.nombre AS encuestador
        FROM invoke.llamada l
        JOIN invoke.estudio e ON e.id = l.estudio_id
        LEFT JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = e.version_diccionario AND dt.codigo = l.disp_tel
        LEFT JOIN invoke.participante p ON p.id = l.participante_id
        WHERE l.caso_id = %s ORDER BY l.originada_en DESC LIMIT 30""", (caso_id,))
    return caso
