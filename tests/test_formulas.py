"""Un test por decisión del documento de fórmulas (sección "Decisiones tomadas"),
más el cálculo de punta a punta desde el log de eventos."""
import datetime as dt

import pytest

from invoke import db
from invoke.dominio import formulas as f
from invoke.servicios import estacion, reportes, supervision

CONTEOS = {"C": 50, "I": 11, "R": 17, "NC": 111, "AB": 0, "NE": 6, "UD": 80}


def test_decision_1_e_igual_a_uno():
    t = f.tasas_marco(CONTEOS)
    assert t["respuesta"].numerador == 50
    assert t["respuesta"].denominador == 50 + 11 + 17 + 111 + 0 + 80   # toda la UD entra
    with pytest.raises(ValueError):
        f.tasas_marco(CONTEOS, e=0)


def test_decision_2_encuestadores_por_cooperacion_y_completas_por_hora():
    t = f.tasas_marco(CONTEOS)
    assert t["cooperacion"].numerador == 50 and t["cooperacion"].denominador == 50 + 11 + 17
    p = f.productividad(24, 5.81, 3.2)
    assert p["completas_por_hora"] == 4.13


def test_decision_3_y_5_parcial_no_cuenta_salvo_promocion():
    sin = f.tasas_marco({**CONTEOS})
    con = f.tasas_marco({**CONTEOS, "C": 51, "I": 10})   # una parcial promovida pasa de I a C
    assert con["respuesta"].numerador == sin["respuesta"].numerador + 1
    assert con["respuesta"].denominador == sin["respuesta"].denominador


def test_decision_4_filtrada_y_no_cumple_son_no_elegibles(cur):
    filas = db.todos(cur, "SELECT codigo, simbolo FROM invoke.disposicion_encuesta WHERE version_diccionario = 1 AND codigo IN (8, 20, 21, 22)")
    assert {r["simbolo"] for r in filas} == {"NE"}


def test_ne_fuera_de_respuesta_dentro_de_contacto():
    t = f.tasas_marco(CONTEOS)
    assert t["contacto"].numerador == 50 + 11 + 17 + 6
    assert t["contacto"].denominador == t["respuesta"].denominador + 6


def test_fuera_de_cuota_es_elegible(cur):
    assert db.uno(cur, "SELECT simbolo FROM invoke.disposicion_encuesta WHERE version_diccionario = 1 AND codigo = 6")["simbolo"] == "NC"


def test_rechazo_mismo_denominador_que_respuesta():
    t = f.tasas_marco(CONTEOS)
    assert t["rechazo"].denominador == t["respuesta"].denominador


def test_abandono_real_suma_falsos_positivos():
    t = f.tasas_abandono(abandonadas=3, conectadas=97, fp_amd=2)
    assert t["abandono_reportada"].valor == pytest.approx(0.03)
    assert t["abandono_real"].valor == pytest.approx(5 / 102)


def test_presentacion():
    assert f.Tasa("x", 1, 2).presentar() == "1 de 2"                 # denominador < 30: cifra absoluta
    assert f.Tasa("x", 50, 189).presentar() == "26,5% (50/189)"     # un decimal y su denominador
    assert f.Tasa("x", 0, 0).presentar().startswith("—")


def test_de_punta_a_punta(cur, esc, conn):
    """Un estudio chico con un caso por grupo: el conteo sale del log, no de contadores."""
    e = esc.estudio(tope_intentos_caso=1, fuera_marco_habilitado=True)
    a = esc.usuario()
    s = esc.usuario("Sup", roles=("supervisor",))
    esc.sesion(a, e)
    casos = {k: esc.caso(e) for k in ("C", "I_promovida", "I", "R", "NC_agenda", "NC_cuota", "NE", "UD_tope", "UD_nvll", "nunca")}

    def tomar(c):
        db.evento(cur, "caso_asignado", caso_id=c["id"], participante_id=a.participante_id)

    tomar(casos["C"])
    estacion.guardar_respuestas(cur, a, casos["C"]["id"], {"SEXO": "1", "NOTA": 5}, None)
    esc.intento(a, casos["C"], 4, 3)
    for k in ("I_promovida", "I"):
        tomar(casos[k])
        esc.intento(a, casos[k], 4, 7, motivo="se cortó")
    supervision.promover_parcial(cur, s, casos["I_promovida"]["id"], "completa en lo sustantivo")
    tomar(casos["R"])
    esc.intento(a, casos["R"], 4, 4)
    tomar(casos["NC_agenda"])
    esc.intento(a, casos["NC_agenda"], 4, 5, agenda={"desde": "2031-01-01T10:00:00Z"})
    tomar(casos["NC_cuota"])
    esc.intento(a, casos["NC_cuota"], 4, 6)
    tomar(casos["NE"])
    esc.intento(a, casos["NE"], 4, 21)
    tomar(casos["UD_tope"])
    esc.intento(a, casos["UD_tope"], 6)       # tope 1: no-contacto definitivo
    tomar(casos["UD_nvll"])
    esc.intento(a, casos["UD_nvll"], 5)
    # Derivado completo: no entra al marco.
    d = estacion.resolver_numero(cur, a, {"opcion": "derivado", "numero": "092000111", "caso_origen": casos["R"]["id"],
                                          "motivo": "lo sugirió el titular"})
    estacion.guardar_respuestas(cur, a, d["caso_id"], {"SEXO": "2", "NOTA": 9}, None)
    esc.intento(a, {"id": d["caso_id"], "telefono_id": d["telefono_id"]}, 4, 3)

    parcial = db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'parcial') AS c", (e["id"],))["c"]
    # En un parcial, la agendada con cita futura y el caso nunca llamado están en proceso.
    assert parcial["marco"] == {"C": 2, "I": 1, "R": 1, "NC": 1, "AB": 0, "NE": 1, "UD": 2, "EP": 2, "SC": 0}
    cierre = db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'cierre') AS c", (e["id"],))["c"]
    # Al cierre: la agendada sin cierre es NC y el nunca llamado es UD.
    assert cierre["marco"]["NC"] == 2 and cierre["marco"]["UD"] == 3 and cierre["marco"]["EP"] == 0
    assert cierre["fuera_marco"]["derivado"] == {"casos": 1, "completas": 1}

    ficha = reportes.ficha_tecnica(cur, e["id"], "parcial")
    tr = ficha["tasa_respuesta"]
    assert (tr["numerador"], tr["denominador"]) == (2, 2 + 1 + 1 + 1 + 0 + 2)
    assert ficha["completas_entregadas"] == 3 and ficha["completas_fuera_de_marco"] == 1
    assert ficha["version_diccionario"] == 1 and ficha["aviso_corte"]
    # Reproducibilidad: el mismo corte en el tiempo da el mismo número aunque haya hechos nuevos.
    t = dt.datetime.now(dt.timezone.utc)
    antes = db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'parcial', %s) AS c", (e["id"], t))["c"]["marco"]
    tomar(casos["nunca"])
    esc.intento(a, casos["nunca"], 4, 4)
    assert db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'parcial', %s) AS c", (e["id"], t))["c"]["marco"] == antes


def test_los_reportes_leen_el_diccionario_del_estudio(cur, esc):
    """Encargo de cierre D5 y regla 12: un estudio con diccionario v2 se reporta con la
    semántica de v2. En este v2 de prueba, el código 30 deja de ser abandono (AB) y pasa
    a elegibilidad desconocida (UD); con v1 el mismo caso es AB."""
    cur.execute("RESET ROLE")
    cur.execute("INSERT INTO invoke.version_documento VALUES ('diccionario', 2, now(), 'v2 de prueba')")
    cur.execute("INSERT INTO invoke.version_diccionario (version) VALUES (2)")
    cur.execute("""INSERT INTO invoke.disposicion_telefono
                   SELECT 2, codigo, etiqueta, comportamiento, estado, cuenta_intento, es_estado_interno,
                          CASE WHEN codigo = 30 THEN 'UD' ELSE simbolo END, activa_sustituto, equivalente_cdr, sin_equivalente
                   FROM invoke.disposicion_telefono WHERE version_diccionario = 1""")
    cur.execute("""INSERT INTO invoke.disposicion_encuesta
                   SELECT 2, codigo, etiqueta, comportamiento, final, es_estado_interno, extendido, simbolo, efecto_caso,
                          activa_sustituto, visible_encuestador, equivalente_cdr, sin_equivalente
                   FROM invoke.disposicion_encuesta WHERE version_diccionario = 1""")
    cur.execute("""INSERT INTO invoke.regla_reintento_defecto
                   SELECT 2, codigo_telefono, espera_min, tope, ventana FROM invoke.regla_reintento_defecto
                   WHERE version_diccionario = 1""")
    cur.execute("SET ROLE invoke_app")

    resultados = {}
    for version in (1, 2):
        e = esc.estudio(modo_discado="power", version_diccionario=version, tope_intentos_caso=1)
        c = esc.caso(e)
        ll = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e["id"],))[0]["llamada_id"]
        db.evento(cur, "llamada_atendida", caso_id=c["id"], llamada_id=ll)
        db.evento(cur, "llamada_abandonada", caso_id=c["id"], llamada_id=ll)
        db.evento(cur, "disposicion_telefono", caso_id=c["id"], llamada_id=ll, datos={"codigo": 30, "automatica": True})
        conteo = db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'cierre') AS c", (e["id"],))["c"]["marco"]
        ab = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, now() - interval '1 hour', now() + interval '1 min')",
                    (e["id"],))
        resultados[version] = (conteo["AB"], conteo["UD"], ab["abandonadas"])
    assert resultados[1] == (1, 0, 1)
    assert resultados[2] == (0, 1, 0)


def test_literales_que_quedan(cur, esc):
    """D5: los literales que quedan en los reportes, documentados en 0011, con su test.
    'UD' para el caso nunca resuelto al cierre y 31 como contestador detectado."""
    e = esc.estudio(modo_discado="power")
    esc.caso(e)
    assert db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'cierre') AS c", (e["id"],))["c"]["marco"]["UD"] == 1
    c = esc.caso(e)
    ll = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e["id"],))[0]["llamada_id"]
    db.evento(cur, "llamada_atendida", caso_id=c["id"], llamada_id=ll)
    db.evento(cur, "disposicion_telefono", caso_id=c["id"], llamada_id=ll, datos={"codigo": 31, "automatica": True})
    ab = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, now() - interval '1 hour', now() + interval '1 min')", (e["id"],))
    assert ab["amd_maquina"] == 1 and ab["abandonadas"] == 0
