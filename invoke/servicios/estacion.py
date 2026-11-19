"""Puesto del encuestador: sesión, caso, discado, entrevista y clasificación.

Cada acción es un hecho en el log (invoke.evento) y las invariantes las hace
cumplir la base: exclusividad de asignación, "solo 4 habilita encuesta", motivo
obligatorio en "termina y explica", no hay llamadas huérfanas (ADR-008).
"""
from __future__ import annotations

import random
import uuid
from typing import Any

from .. import db
from ..dominio.cuestionario import COMPLETA, Motor
from ..enrutador import ErrorHttp, Usuario
from . import credenciales, cuestionarios, estudios, muestra

ESTADOS_ELEGIBLES = ("disponible", "pausa")


def _participante(cur, u: Usuario) -> int:
    if u.participante_id:
        return u.participante_id
    p = db.uno(cur, """INSERT INTO invoke.participante (tipo, usuario_uid, nombre) VALUES ('encuestador_webrtc', %s, %s)
                       ON CONFLICT (usuario_uid) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id""",
               (u.uid, u.nombre))
    u.participante_id = p["id"]
    return p["id"]


def _estado(cur, pid: int) -> dict:
    return db.uno(cur, "SELECT * FROM invoke.participante_estado WHERE participante_id = %s", (pid,)) or {
        "estado": "desconectado", "estudio_id": None, "caso_id": None}


def estudios_disponibles(cur, u: Usuario) -> list[dict]:
    return db.todos(cur, """
        SELECT e.id, e.codigo, e.nombre, e.modo_discado, e.cliente
        FROM invoke.estudio e JOIN invoke.estudio_usuario eu ON eu.estudio_id = e.id
        WHERE eu.usuario_uid = %s AND e.estado = 'activo' ORDER BY e.nombre""", (u.uid,))


def iniciar_sesion(cur, u: Usuario, estudio_id: int) -> dict:
    asignado = db.uno(cur, """SELECT 1 FROM invoke.estudio_usuario eu JOIN invoke.estudio e ON e.id = eu.estudio_id
                              WHERE eu.estudio_id = %s AND eu.usuario_uid = %s AND e.estado = 'activo'""",
                      (estudio_id, u.uid))
    if not asignado:
        raise ErrorHttp(403, "no estás asignado a ese estudio o el estudio no está activo")
    pid = _participante(cur, u)
    actual = _estado(cur, pid)
    if actual["estado"] != "desconectado":
        _cerrar(cur, pid, actual, motivo="nueva_sesion")
    sesion = str(uuid.uuid4())
    db.evento(cur, "sesion_iniciada", estudio_id=estudio_id, participante_id=pid, datos={"sesion_id": sesion})
    est = estudios.obtener(cur, estudio_id)
    cq = cuestionarios.vigente(cur, estudio_id)
    return {
        "sesion_id": sesion,
        "participante_id": pid,
        "estudio": {k: est[k] for k in ("id", "codigo", "nombre", "modo_discado", "fuera_marco_habilitado",
                                         "preservar_encuestador", "tipo_marco")},
        "catalogo": estudios.catalogo(cur, estudio_id),
        "cuestionario": cq and {"id": cq["id"], "version": cq["version"], "definicion": cq["definicion"]},
        "sip": credenciales.rotar_sip(cur, pid, u.nombre),
        "ice": credenciales.ice(u.uid),
    }


def cambiar_estado(cur, u: Usuario, estado: str) -> dict:
    if estado not in ESTADOS_ELEGIBLES:
        raise ErrorHttp(400, "el encuestador solo elige entre disponible y pausa")
    pid = _participante(cur, u)
    pe = _estado(cur, pid)
    if pe["estado"] == "desconectado":
        raise ErrorHttp(409, "no hay sesión abierta")
    if pe["estado"] == "en_llamada":
        raise ErrorHttp(409, "no se cambia de estado en medio de una llamada")
    db.evento(cur, "estado_participante", estudio_id=pe["estudio_id"], participante_id=pid, datos={"estado": estado})
    return _estado(cur, pid)


def siguiente_caso(cur, u: Usuario) -> dict | None:
    pid = _participante(cur, u)
    pe = _estado(cur, pid)
    if pe["estado"] == "desconectado":
        raise ErrorHttp(409, "no hay sesión abierta")
    caso_id = db.uno(cur, "SELECT invoke.asignar_caso(%s, %s) AS caso", (pid, pe["estudio_id"]))["caso"]
    if caso_id is None:
        return None
    return muestra.ficha_caso(cur, caso_id)


def caso_actual(cur, u: Usuario) -> dict | None:
    pid = _participante(cur, u)
    fila = db.uno(cur, "SELECT caso_id FROM invoke.caso_estado WHERE asignado_a = %s", (pid,))
    return muestra.ficha_caso(cur, fila["caso_id"]) if fila else None


def saltar_caso(cur, u: Usuario, caso_id: int, motivo: str) -> None:
    """Devuelve a la cola un caso asignado que todavía no se llamó (preview)."""
    pid = _participante(cur, u)
    ce = db.uno(cur, "SELECT * FROM invoke.caso_estado WHERE caso_id = %s", (caso_id,))
    if not ce or ce["asignado_a"] != pid:
        raise ErrorHttp(409, "ese caso no está asignado a vos")
    if ce["estado"] == "en_llamada":
        raise ErrorHttp(409, "el caso ya tiene una llamada: clasificalo")
    db.evento(cur, "caso_liberado", caso_id=caso_id, participante_id=pid, datos={"motivo": motivo or "saltado"})


# ─────────────────────────────── discado ───────────────────────────────

def clasificar_numero(cur, u: Usuario, numero: str, caso_actual_id: int | None) -> dict:
    pid = _participante(cur, u)
    pe = _estado(cur, pid)
    return db.uno(cur, "SELECT invoke.clasificar_numero(%s, %s, %s) AS r",
                  (pe["estudio_id"], numero, caso_actual_id))["r"]


def resolver_numero(cur, u: Usuario, datos: dict) -> dict:
    """Diálogo de tres opciones (política de casos fuera de marco). No se puede saltear."""
    pid = _participante(cur, u)
    opcion = datos.get("opcion")
    numero = (datos.get("numero") or "").strip()
    if opcion == "mismo_caso":
        caso = db.uno(cur, "SELECT caso_id FROM invoke.caso_estado WHERE asignado_a = %s", (pid,))
        if not caso:
            raise ErrorHttp(409, "«otro teléfono del mismo caso» requiere un caso abierto")
        tel = db.uno(cur, "SELECT invoke.agregar_telefono(%s, %s, %s) AS id", (caso["caso_id"], numero, u.uid))["id"]
        return {"caso_id": caso["caso_id"], "telefono_id": tel}
    if opcion in ("derivado", "reemplazo_manual"):
        if db.uno(cur, "SELECT 1 FROM invoke.caso_estado WHERE asignado_a = %s", (pid,)):
            raise ErrorHttp(409, "clasificá el caso actual antes de llamar a una persona derivada")
        origen = datos.get("caso_origen")
        trabajado = db.uno(cur, "SELECT 1 FROM invoke.evento WHERE caso_id = %s AND participante_id = %s LIMIT 1",
                           (origen, pid))
        if not trabajado:
            raise ErrorHttp(409, "el caso origen tiene que ser uno que trabajaste")
        if not (datos.get("motivo") or "").strip():
            raise ErrorHttp(400, "contá brevemente por qué se deriva")
        nuevo = db.uno(cur, "SELECT invoke.crear_caso_fuera_de_marco(%s, %s, %s, %s, %s, %s, %s) AS id",
                       (origen, numero, opcion, u.uid, datos["motivo"].strip(), datos.get("estrato"),
                        db.Jsonb(datos["propiedades"]) if datos.get("propiedades") else None))["id"]
        pe = _estado(cur, pid)
        db.evento(cur, "caso_asignado", estudio_id=pe["estudio_id"], caso_id=nuevo, participante_id=pid,
                  datos={"paso": "fuera_de_marco"})
        tel = db.uno(cur, "SELECT id FROM invoke.telefono WHERE caso_id = %s", (nuevo,))["id"]
        return {"caso_id": nuevo, "telefono_id": tel}
    if opcion == "administrativa":
        return {"administrativa": True, "numero": numero}
    raise ErrorHttp(400, "opción inválida: mismo_caso, derivado, reemplazo_manual o administrativa")


def llamar(cur, u: Usuario, datos: dict) -> dict:
    """Manual y preview: el encuestador inicia la llamada (desde la ficha o el teclado)."""
    pid = _participante(cur, u)
    pe = _estado(cur, pid)
    if pe["estado"] == "desconectado":
        raise ErrorHttp(409, "no hay sesión abierta")
    if pe.get("llamada_id"):
        raise ErrorHttp(409, "ya hay una llamada en curso")
    if not pe.get("canal_audio"):
        raise ErrorHttp(409, "el audio del puesto no está conectado")
    est = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (pe["estudio_id"],))
    llamada = str(uuid.uuid4())
    if datos.get("administrativa"):
        numero = (datos.get("numero") or "").strip()
        if est["modo_discado"] != "manual":
            raise ErrorHttp(409, "las llamadas administrativas se hacen en estudios con discado manual")
        db.evento(cur, "llamada_originada", estudio_id=est["id"], llamada_id=llamada, participante_id=pid,
                  datos={"numero": numero, "modo": "manual", "tipo": "administrativa", "grabar": False})
    else:
        caso_id = int(datos["caso_id"])
        ce = db.uno(cur, "SELECT * FROM invoke.caso_estado WHERE caso_id = %s", (caso_id,))
        if not ce or ce["asignado_a"] != pid:
            raise ErrorHttp(409, "ese caso no está asignado a vos")
        tel = db.uno(cur, """SELECT t.*, EXISTS (SELECT 1 FROM invoke.lista_negra l WHERE l.numero_normalizado = t.numero_normalizado) AS bloqueado
                             FROM invoke.telefono t WHERE t.id = %s AND t.caso_id = %s""",
                     (datos.get("telefono_id"), caso_id))
        if not tel:
            raise ErrorHttp(409, "el número no pertenece al caso: clasificalo primero")
        if tel["bloqueado"]:
            raise ErrorHttp(409, "ese número está en la lista de no volver a llamar")
        numero = tel["numero"]
        db.evento(cur, "llamada_originada", estudio_id=est["id"], caso_id=caso_id, llamada_id=llamada,
                  participante_id=pid,
                  datos={"telefono_id": tel["id"], "numero": numero, "modo": est["modo_discado"], "tipo": "encuesta",
                         "grabar": random.random() < float(est["muestreo_grabacion"])})
    cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, participante_id, llamada_id, datos)
                   VALUES ('originar', %s, %s, %s)""",
                (pid, llamada, db.Jsonb({"numero": numero, "canal_participante": pe["canal_audio"]})))
    return {"llamada_id": llamada, "numero": numero}


def comando(cur, u: Usuario, tipo: str, datos: dict | None = None) -> None:
    pid = _participante(cur, u)
    pe = _estado(cur, pid)
    if tipo not in ("colgar", "mudo", "transferir", "dtmf"):
        raise ErrorHttp(400, "comando inválido")
    if not pe.get("llamada_id"):
        raise ErrorHttp(409, "no hay llamada en curso")
    cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, participante_id, llamada_id, datos, autor)
                   VALUES (%s, %s, %s, %s, %s)""", (tipo, pid, pe["llamada_id"], db.Jsonb(datos or {}), u.uid))


# ─────────────────────────────── entrevista ───────────────────────────────

def respuestas_vigentes(cur, caso_id: int) -> dict[str, Any]:
    return {r["variable"]: r["valor"] for r in db.todos(
        cur, "SELECT variable, valor FROM invoke.respuesta_vigente WHERE caso_id = %s", (caso_id,))}


def guardar_respuestas(cur, u: Usuario, caso_id: int, respuestas: dict[str, Any], llamada_id: str | None,
                       pantalla: dict | None = None) -> dict:
    pid = _participante(cur, u)
    ce = db.uno(cur, "SELECT asignado_a FROM invoke.caso_estado WHERE caso_id = %s", (caso_id,))
    if not ce or ce["asignado_a"] != pid:
        raise ErrorHttp(409, "ese caso no está asignado a vos")
    cq = db.uno(cur, """SELECT q.id FROM invoke.cuestionario_vigente q JOIN invoke.caso c ON c.estudio_id = q.estudio_id
                        WHERE c.id = %s""", (caso_id,))
    actuales = respuestas_vigentes(cur, caso_id)
    nuevas = {k: v for k, v in respuestas.items() if actuales.get(k, "__ausente__") != v}
    for var, val in nuevas.items():
        cur.execute("""INSERT INTO invoke.respuesta (caso_id, cuestionario_id, participante_id, llamada_id, variable, valor)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (caso_id, cq["id"], pid, llamada_id, var, db.Jsonb(val)))
    if pantalla:
        db.evento(cur, "pantalla", caso_id=caso_id, participante_id=pid, llamada_id=llamada_id,
                  datos={"pantalla": pantalla.get("id"), "segundos": pantalla.get("segundos")})
    return {"guardadas": len(nuevas)}


def iniciar_encuesta(cur, u: Usuario, caso_id: int, llamada_id: str | None) -> None:
    pid = _participante(cur, u)
    db.evento(cur, "encuesta_iniciada", caso_id=caso_id, participante_id=pid, llamada_id=llamada_id)


def evaluar_cuotas(cur, u: Usuario, caso_id: int, respuestas: dict) -> list[dict]:
    from . import cuotas

    combinadas = respuestas_vigentes(cur, caso_id) | respuestas
    return cuotas.evaluar(cur, caso_id, combinadas)


def finalizar(cur, u: Usuario, datos: dict) -> dict:
    """Clasificación del intento: disposición de teléfono y, si contestó, de encuesta."""
    pid = _participante(cur, u)
    caso_id = int(datos["caso_id"])
    llamada_id = datos.get("llamada_id")
    disp_tel = datos.get("disposicion_telefono")
    disp_enc = datos.get("disposicion_encuesta")

    if llamada_id:
        ya = db.uno(cur, "SELECT (datos ->> 'codigo')::int AS c FROM invoke.evento WHERE llamada_id = %s AND tipo = 'disposicion_telefono'",
                    (llamada_id,))
        if ya is None:
            if disp_tel is None:
                raise ErrorHttp(400, "falta la disposición de teléfono")
            db.evento(cur, "disposicion_telefono", caso_id=caso_id, llamada_id=llamada_id, participante_id=pid,
                      datos={"codigo": int(disp_tel), "automatica": False})
        else:
            disp_tel = ya["c"]

    if disp_enc is not None:
        disp_enc = int(disp_enc)
        if disp_enc == COMPLETA:
            _validar_completa(cur, caso_id)
        d = {"codigo": disp_enc, "origen": datos.get("origen", "encuestador")}
        if d["origen"] not in ("encuestador", "sistema"):
            raise ErrorHttp(400, "origen inválido")
        if datos.get("motivo"):
            d["motivo"] = str(datos["motivo"]).strip()
        if datos.get("agenda"):
            ag = datos["agenda"]
            d.update({"agenda_desde": ag["desde"], "agenda_hasta": ag.get("hasta"),
                      "preservar_encuestador": bool(ag.get("preservar_encuestador", True))})
        db.evento(cur, "disposicion_encuesta", caso_id=caso_id, llamada_id=llamada_id, participante_id=pid, datos=d)
    elif disp_tel == 4:
        raise ErrorHttp(400, "si contestó, la encuesta necesita su disposición")

    # Si el caso quedó asignado sin acción pendiente, se libera.
    ce = db.uno(cur, "SELECT estado, asignado_a FROM invoke.caso_estado WHERE caso_id = %s", (caso_id,))
    if ce["asignado_a"] == pid and disp_tel != 4:
        db.evento(cur, "caso_liberado", caso_id=caso_id, participante_id=pid, datos={"motivo": "clasificado"})
    pe = _estado(cur, pid)
    siguiente = datos.get("siguiente_estado", "disponible")
    if siguiente in ESTADOS_ELEGIBLES and pe["estado"] != siguiente:
        db.evento(cur, "estado_participante", estudio_id=pe["estudio_id"], participante_id=pid, datos={"estado": siguiente})
    return {"caso": muestra.ficha_caso(cur, caso_id)}


def _validar_completa(cur, caso_id: int) -> None:
    """El servidor no confía en el navegador: rehace el recorrido con el motor Python."""
    c = db.uno(cur, """SELECT c.id, c.propiedades, q.definicion FROM invoke.caso c
                       JOIN invoke.cuestionario_vigente q ON q.estudio_id = c.estudio_id WHERE c.id = %s""", (caso_id,))
    if not c:
        raise ErrorHttp(409, "el estudio no tiene cuestionario publicado")
    res = Motor(c["definicion"], c["propiedades"], caso_id).recorrer(respuestas_vigentes(cur, caso_id))
    if res["errores"]:
        pantalla, errs = next(iter(res["errores"].items()))
        raise ErrorHttp(422, f"la entrevista no está completa: {pantalla}: {'; '.join(f'{k}: {v}' for k, v in errs.items())}")
    if res["fin"] != COMPLETA:
        raise ErrorHttp(422, f"el flujo del cuestionario termina con la disposición {res['fin']}, no con Completa")


# ─────────────────────────── red, audio y cierre ───────────────────────────

def calidad_red(cur, u: Usuario, muestras: list[dict]) -> None:
    pid = _participante(cur, u)
    for m in muestras[:20]:
        cur.execute("""INSERT INTO invoke.calidad_red (participante_id, llamada_id, perdida_pct, jitter_ms, rtt_ms, relay, codec, mos)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (pid, m.get("llamada_id"), m.get("perdida_pct"), m.get("jitter_ms"), m.get("rtt_ms"),
                     m.get("relay"), m.get("codec"), m.get("mos")))


def prueba_audio(cur, u: Usuario, resultado: dict) -> None:
    pid = _participante(cur, u)
    db.evento(cur, "prueba_audio", participante_id=pid, datos=resultado)


def cerrar_sesion(cur, u: Usuario) -> None:
    pid = _participante(cur, u)
    _cerrar(cur, pid, _estado(cur, pid), motivo="cierre")


def _cerrar(cur, pid: int, pe: dict, motivo: str) -> None:
    caso = db.uno(cur, "SELECT caso_id, estado FROM invoke.caso_estado WHERE asignado_a = %s", (pid,))
    if caso and caso["estado"] == "asignado":
        db.evento(cur, "caso_liberado", caso_id=caso["caso_id"], participante_id=pid, datos={"motivo": motivo})
    if pe.get("canal_audio"):
        cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, participante_id, datos) VALUES ('colgar', %s, %s)""",
                    (pid, db.Jsonb({"canal": pe["canal_audio"]})))
    db.evento(cur, "sesion_finalizada", estudio_id=pe.get("estudio_id"), participante_id=pid,
              datos={"sesion_id": str(pe["sesion_id"]) if pe.get("sesion_id") else None, "motivo": motivo})
