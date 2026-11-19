"""Consola de supervisión en vivo (Fase 1, ampliada en Fase 2).

Con sala 100% remota es el único mecanismo de control: estado y caso en curso
por encuestador, tiempo en el estado, calidad de red del puesto, escucha de
llamadas, y desde la Fase 2 los indicadores del discador.
"""
from __future__ import annotations

import datetime as dt

from .. import db
from ..dominio import formulas
from ..enrutador import ErrorHttp, Usuario

UMBRAL_PERDIDA = 3.0    # % de pérdida de paquetes que marca un puesto degradado
UMBRAL_JITTER = 30.0    # ms


def sala(cur, estudio_id: int | None = None) -> list[dict]:
    filas = db.todos(cur, """
        SELECT p.id AS participante_id, p.nombre, p.tipo, pe.estado, pe.estudio_id, e.codigo AS estudio,
               pe.desde, EXTRACT(epoch FROM (clock_timestamp() - pe.desde))::int AS segundos_en_estado,
               pe.caso_id, pe.llamada_id, pe.canal_audio IS NOT NULL AS audio_conectado,
               c.identificador AS caso_identificador, c.origen AS caso_origen,
               q.perdida_pct, q.jitter_ms, q.rtt_ms, q.relay, q.codec, q.tomada_en AS calidad_en
        FROM invoke.participante p
        JOIN invoke.participante_estado pe ON pe.participante_id = p.id
        LEFT JOIN invoke.estudio e ON e.id = pe.estudio_id
        LEFT JOIN invoke.caso c ON c.id = pe.caso_id
        LEFT JOIN LATERAL (SELECT * FROM invoke.calidad_red q WHERE q.participante_id = p.id
                           AND q.tomada_en > clock_timestamp() - interval '2 minutes'
                           ORDER BY q.tomada_en DESC LIMIT 1) q ON true
        WHERE pe.estado <> 'desconectado' AND (%s::bigint IS NULL OR pe.estudio_id = %s)
        ORDER BY e.codigo, p.nombre""", (estudio_id, estudio_id))
    for f in filas:
        f["red_degradada"] = bool(
            (f["perdida_pct"] is not None and float(f["perdida_pct"]) > UMBRAL_PERDIDA)
            or (f["jitter_ms"] is not None and float(f["jitter_ms"]) > UMBRAL_JITTER))
    return filas


def indicadores_discador(cur, estudio_id: int) -> dict:
    est = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (estudio_id,))
    if not est:
        raise ErrorHttp(404, "estudio inexistente")
    ahora = dt.datetime.now(dt.timezone.utc)
    desde = ahora - dt.timedelta(minutes=est["ventana_abandono_min"])
    ab = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, %s, %s)", (estudio_id, desde, ahora))
    descartadas = db.uno(cur, """SELECT count(*) AS n FROM invoke.evento WHERE estudio_id = %s AND tipo = 'llamada_descartada'
                                 AND ocurrido_en >= %s""", (estudio_id, desde))["n"]
    en_curso = db.uno(cur, """SELECT count(*) AS n FROM invoke.llamada WHERE estudio_id = %s AND finalizada_en IS NULL
                              AND NOT descartada AND originada_en > clock_timestamp() - interval '10 minutes'""",
                      (estudio_id,))["n"]
    ultimo_ajuste = db.uno(cur, """SELECT ocurrido_en, datos FROM invoke.evento WHERE estudio_id = %s AND tipo = 'pacer_ajuste'
                                   ORDER BY id DESC LIMIT 1""", (estudio_id,))
    tasas = formulas.tasas_abandono(ab["abandonadas"], ab["conectadas"], ab["fp_amd"])
    return {
        "modo": est["modo_discado"],
        "n_vigente": float(est["n_vigente"] or est["n_inicial"]),
        "n_maximo": float(est["n_maximo"]),
        "abandono_objetivo": float(est["abandono_objetivo"]),
        "ventana_min": est["ventana_abandono_min"],
        "abandono": {k: v.como_dict() for k, v in tasas.items()},
        "sobre_umbral": (tasas["abandono_real"].valor or 0) > float(est["abandono_objetivo"]),
        "descartadas_ventana": descartadas,
        "llamadas_en_curso": en_curso,
        "ultimo_ajuste": ultimo_ajuste,
    }


def fijar_n(cur, u: Usuario, estudio_id: int, n: float, ajuste_automatico: bool | None = None) -> dict:
    """El supervisor baja (o sube) N desde la consola, sin intervención técnica."""
    est = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (estudio_id,))
    n = round(float(n), 2)
    if not 1 <= n <= float(est["n_maximo"]):
        raise ErrorHttp(400, f"N tiene que estar entre 1 y {est['n_maximo']}")
    sets, params = ["n_vigente = %s"], [n]
    if ajuste_automatico is not None:
        sets.append("ajuste_automatico = %s")
        params.append(ajuste_automatico)
    cur.execute(f"UPDATE invoke.estudio SET {', '.join(sets)} WHERE id = %s", (*params, estudio_id))
    db.evento(cur, "pacer_ajuste", estudio_id=estudio_id, autor=u.uid,
              datos={"n_anterior": float(est["n_vigente"] or est["n_inicial"]), "n_nuevo": n, "origen": "supervisor",
                     "ajuste_automatico": ajuste_automatico, "motivo": "ajuste manual desde la consola"})
    return indicadores_discador(cur, estudio_id)


def escuchar(cur, u: Usuario, participante_id: int, susurro: bool = False) -> dict:
    pe = db.uno(cur, "SELECT * FROM invoke.participante_estado WHERE participante_id = %s", (participante_id,))
    if not pe or not pe["llamada_id"]:
        raise ErrorHttp(409, "ese encuestador no está en llamada")
    if not u.participante_id:
        raise ErrorHttp(409, "el supervisor necesita su puesto de audio conectado para escuchar")
    sup = db.uno(cur, "SELECT canal_audio FROM invoke.participante_estado WHERE participante_id = %s", (u.participante_id,))
    if not sup or not sup["canal_audio"]:
        raise ErrorHttp(409, "conectá el audio de la consola antes de escuchar")
    cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, participante_id, llamada_id, autor, datos)
                   VALUES ('escuchar', %s, %s, %s, %s)""",
                (participante_id, pe["llamada_id"], u.uid,
                 db.Jsonb({"supervisor_participante": u.participante_id, "susurro": susurro})))
    db.evento(cur, "escucha_iniciada", estudio_id=pe["estudio_id"], participante_id=participante_id,
              llamada_id=pe["llamada_id"], autor=u.uid, datos={"susurro": susurro})
    return {"ok": True}


def dejar_de_escuchar(cur, u: Usuario, participante_id: int) -> None:
    cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, participante_id, autor) VALUES ('dejar_escuchar', %s, %s)""",
                (participante_id, u.uid))
    db.evento(cur, "escucha_finalizada", participante_id=participante_id, autor=u.uid)


def promover_parcial(cur, u: Usuario, caso_id: int, motivo: str) -> dict:
    """Decisión 6 del diccionario: una Interrumpida solo pasa a Completa si la promueve un supervisor."""
    db.evento(cur, "disposicion_encuesta", caso_id=caso_id, autor=u.uid,
              datos={"codigo": 3, "origen": "promocion", "desde": 7, "motivo": (motivo or "").strip()})
    return {"ok": True}


def aplicar_estado_extendido(cur, u: Usuario, caso_id: int, codigo: int, motivo: str) -> dict:
    db.evento(cur, "disposicion_encuesta", caso_id=caso_id, autor=u.uid,
              datos={"codigo": int(codigo), "origen": "supervisor", "motivo": motivo})
    return {"ok": True}


def parciales(cur, estudio_id: int) -> list[dict]:
    return db.todos(cur, """
        SELECT c.id AS caso_id, c.identificador, u.ocurrido_en, p.nombre AS encuestador,
               (SELECT x.datos ->> 'motivo' FROM invoke.evento x WHERE x.id = u.evento_id) AS motivo,
               (SELECT count(*) FROM invoke.respuesta_vigente r WHERE r.caso_id = c.id) AS respuestas
        FROM invoke.caso_ultima_disp_enc u JOIN invoke.caso c ON c.id = u.caso_id
        LEFT JOIN invoke.participante p ON p.id = u.participante_id
        WHERE c.estudio_id = %s AND u.codigo = 7 ORDER BY u.ocurrido_en DESC""", (estudio_id,))


def revision_amd(cur, estudio_id: int, limite: int = 50) -> list[dict]:
    """Muestra de llamadas clasificadas como máquina (31) con grabación, para escuchar."""
    return db.todos(cur, """
        SELECT llamada_id, caso_id, numero, originada_en, grabacion, reclasificada
        FROM invoke.llamada WHERE estudio_id = %s AND disp_tel_original = 31 AND grabacion IS NOT NULL
        ORDER BY reclasificada, random() LIMIT %s""", (estudio_id, limite))


def reclasificar_amd(cur, u: Usuario, llamada_id: str) -> dict:
    l = db.uno(cur, "SELECT caso_id, estudio_id FROM invoke.llamada WHERE llamada_id = %s", (llamada_id,))
    if not l:
        raise ErrorHttp(404, "llamada inexistente")
    db.evento(cur, "reclasificacion_amd", estudio_id=l["estudio_id"], caso_id=l["caso_id"], llamada_id=llamada_id,
              autor=u.uid, datos={"de": 31, "a": 32})
    return {"ok": True}


def fuera_de_marco_por_encuestador(cur, estudio_id: int) -> list[dict]:
    """Control 2 de la política: cuántos creó cada uno, marcando al que se desvía de la sala."""
    filas = db.todos(cur, """
        SELECT u.nombre, c.usuario_creador, count(*) AS creados,
               count(*) FILTER (WHERE d.codigo = 3) AS completas
        FROM invoke.caso c JOIN invoke.usuario u ON u.uid = c.usuario_creador
        LEFT JOIN invoke.caso_ultima_disp_enc d ON d.caso_id = c.id
        WHERE c.estudio_id = %s AND c.origen IN ('derivado', 'reemplazo_manual')
        GROUP BY u.nombre, c.usuario_creador ORDER BY creados DESC""", (estudio_id,))
    if len(filas) >= 2:
        valores = [f["creados"] for f in filas]
        media = sum(valores) / len(valores)
        desvio = (sum((v - media) ** 2 for v in valores) / len(valores)) ** 0.5
        for f in filas:
            f["desviado"] = desvio > 0 and f["creados"] > media + 2 * desvio
    return filas
