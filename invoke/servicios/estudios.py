"""Administración de estudios: parámetros, modo de discado, franjas y reintentos."""
from __future__ import annotations

from typing import Any

from .. import db
from ..enrutador import ErrorHttp

# Campos que el administrador puede editar. Las versiones de diccionario y
# fórmulas se fijan al activar (trigger estudio_versiones_fijas).
EDITABLES = {
    "nombre", "cliente", "es_estudio", "tipo_marco", "objetivo_completas",
    "modo_discado", "n_inicial", "n_maximo", "abandono_objetivo", "ajuste_automatico",
    "ventana_abandono_min", "min_agentes_predictivo",
    "fuera_marco_habilitado", "umbral_alerta_derivados", "sustitutos_habilitados",
    "ignorar_agendados", "preservar_encuestador",
    "tope_intentos_caso", "tope_intentos_franja", "zona_horaria", "proporcion_frescos",
    "version_diccionario", "version_formulas",
}
ESTADOS = {"borrador", "activo", "pausado", "cerrado"}


def listar(cur, solo_activos: bool = False) -> list[dict]:
    return db.todos(cur, f"""
        SELECT e.*, (SELECT count(*) FROM invoke.caso c WHERE c.estudio_id = e.id) AS casos,
               (SELECT count(*) FROM invoke.caso_ultima_disp_enc u JOIN invoke.caso c ON c.id = u.caso_id
                 WHERE c.estudio_id = e.id AND u.codigo = 3) AS completas
        FROM invoke.estudio e {"WHERE e.estado = 'activo'" if solo_activos else ""}
        ORDER BY e.estado = 'cerrado', e.creado_en DESC""")


def obtener(cur, estudio_id: int) -> dict:
    e = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (estudio_id,))
    if not e:
        raise ErrorHttp(404, "estudio inexistente")
    e["franjas"] = db.todos(cur, "SELECT nombre, desde, hasta FROM invoke.franja WHERE estudio_id = %s ORDER BY desde", (estudio_id,))
    e["reintentos"] = db.todos(cur, """
        SELECT r.codigo_telefono, d.etiqueta, r.espera_min, r.tope, r.ventana
        FROM invoke.regla_reintento r
        JOIN invoke.estudio e ON e.id = r.estudio_id
        JOIN invoke.disposicion_telefono d ON d.version_diccionario = e.version_diccionario AND d.codigo = r.codigo_telefono
        WHERE r.estudio_id = %s ORDER BY r.codigo_telefono""", (estudio_id,))
    e["guias"] = db.todos(cur, """
        SELECT g.*, (SELECT count(*) FROM invoke.caso c WHERE c.guia_id = g.id) AS casos,
               (SELECT count(*) FROM invoke.caso c WHERE c.guia_id = g.id AND c.origen <> 'marco') AS fuera_marco
        FROM invoke.guia g WHERE g.estudio_id = %s ORDER BY g.prioridad DESC, g.id""", (estudio_id,))
    e["encuestadores"] = db.todos(cur, """
        SELECT u.uid, u.nombre, u.email FROM invoke.estudio_usuario eu JOIN invoke.usuario u ON u.uid = eu.usuario_uid
        WHERE eu.estudio_id = %s ORDER BY u.nombre""", (estudio_id,))
    return e


def crear(cur, datos: dict[str, Any], autor: str) -> dict:
    if not datos.get("codigo") or not datos.get("nombre"):
        raise ErrorHttp(400, "el estudio necesita código y nombre")
    campos = {k: v for k, v in datos.items() if k in EDITABLES | {"codigo"}}
    cols = ", ".join(campos)
    marcas = ", ".join(["%s"] * len(campos))
    e = db.uno(cur, f"INSERT INTO invoke.estudio ({cols}) VALUES ({marcas}) RETURNING *", tuple(campos.values()))
    db.evento(cur, "estudio_modificado", estudio_id=e["id"], autor=autor, datos={"accion": "creado", "campos": campos})
    return e


def actualizar(cur, estudio_id: int, datos: dict[str, Any], autor: str) -> dict:
    actual = obtener(cur, estudio_id)
    cambios = {k: v for k, v in datos.items() if k in EDITABLES and actual.get(k) != v}
    desconocidos = set(datos) - EDITABLES - {"estado", "franjas", "reintentos"}
    if desconocidos:
        raise ErrorHttp(400, f"campos no editables: {', '.join(sorted(desconocidos))}")
    if cambios:
        sets = ", ".join(f"{k} = %s" for k in cambios)
        cur.execute(f"UPDATE invoke.estudio SET {sets} WHERE id = %s", (*cambios.values(), estudio_id))
    if "estado" in datos and datos["estado"] != actual["estado"]:
        cambiar_estado(cur, estudio_id, datos["estado"], autor)
        cambios["estado"] = datos["estado"]
    if "franjas" in datos:
        _franjas(cur, estudio_id, datos["franjas"])
        cambios["franjas"] = datos["franjas"]
    if "reintentos" in datos:
        _reintentos(cur, estudio_id, datos["reintentos"])
        cambios["reintentos"] = datos["reintentos"]
    if cambios:
        db.evento(cur, "estudio_modificado", estudio_id=estudio_id, autor=autor,
                  datos={"accion": "parametros", "cambios": {k: _json(v) for k, v in cambios.items()}})
    return obtener(cur, estudio_id)


def _json(v):
    import decimal

    return float(v) if isinstance(v, decimal.Decimal) else v


def cambiar_estado(cur, estudio_id: int, estado: str, autor: str) -> None:
    if estado not in ESTADOS:
        raise ErrorHttp(400, "estado inválido")
    if estado == "activo":
        vig = db.uno(cur, "SELECT 1 FROM invoke.cuestionario_vigente WHERE estudio_id = %s", (estudio_id,))
        if not vig:
            raise ErrorHttp(409, "no se activa un estudio sin cuestionario publicado")
    cur.execute(
        """UPDATE invoke.estudio SET estado = %s,
               cerrado_en = CASE WHEN %s = 'cerrado' THEN now() ELSE NULL END
           WHERE id = %s""", (estado, estado, estudio_id))


def _franjas(cur, estudio_id: int, franjas: list[dict]) -> None:
    if not franjas:
        raise ErrorHttp(400, "el estudio necesita al menos una franja")
    cur.execute("DELETE FROM invoke.franja WHERE estudio_id = %s", (estudio_id,))
    for f in franjas:
        cur.execute("INSERT INTO invoke.franja (estudio_id, nombre, desde, hasta) VALUES (%s, %s, %s, %s)",
                    (estudio_id, f["nombre"], f["desde"], f["hasta"]))


def _reintentos(cur, estudio_id: int, reglas: list[dict]) -> None:
    for r in reglas:
        cur.execute(
            """INSERT INTO invoke.regla_reintento (estudio_id, codigo_telefono, espera_min, tope, ventana)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (estudio_id, codigo_telefono)
               DO UPDATE SET espera_min = EXCLUDED.espera_min, tope = EXCLUDED.tope, ventana = EXCLUDED.ventana""",
            (estudio_id, r["codigo_telefono"], r["espera_min"], r.get("tope"), r["ventana"]))


def asignar_encuestadores(cur, estudio_id: int, uids: list[str]) -> None:
    cur.execute("DELETE FROM invoke.estudio_usuario WHERE estudio_id = %s", (estudio_id,))
    for uid in uids:
        cur.execute("INSERT INTO invoke.estudio_usuario (estudio_id, usuario_uid) VALUES (%s, %s)", (estudio_id, uid))


def catalogo(cur, estudio_id: int) -> dict:
    """Diccionario vigente del estudio (con su versión), para la estación y la administración."""
    v = db.uno(cur, "SELECT version_diccionario FROM invoke.estudio WHERE id = %s", (estudio_id,))
    if not v:
        raise ErrorHttp(404, "estudio inexistente")
    ver = v["version_diccionario"]
    return {
        "version": ver,
        "telefono": db.todos(cur, "SELECT * FROM invoke.disposicion_telefono WHERE version_diccionario = %s ORDER BY codigo", (ver,)),
        "encuesta": db.todos(cur, "SELECT * FROM invoke.disposicion_encuesta WHERE version_diccionario = %s ORDER BY codigo", (ver,)),
    }
