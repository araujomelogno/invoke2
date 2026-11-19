"""Invariantes que viven en la base (CLAUDE.md: las invariantes no se repiten en Python).

Corren como invoke_app: si un test necesita un privilegio que la aplicación no
tiene, falla igual que fallaría en producción.
"""
import datetime as dt

import psycopg
import pytest

from invoke import db
from invoke.enrutador import ErrorHttp
from invoke.servicios import estacion, supervision

pytestmark = pytest.mark.usefixtures("dsn_base")


def test_evento_inmutable(cur, esc):
    e = esc.estudio()
    eid = db.evento(cur, "estudio_modificado", estudio_id=e["id"], datos={"x": 1})
    cur.execute("SAVEPOINT a")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("UPDATE invoke.evento SET datos = '{}' WHERE id = %s", (eid,))
    cur.execute("ROLLBACK TO SAVEPOINT a")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("DELETE FROM invoke.evento WHERE id = %s", (eid,))
    cur.execute("ROLLBACK TO SAVEPOINT a")
    # Ni siquiera el dueño puede: el trigger lo impide.
    cur.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.RaiseException, match="inmutable"):
        cur.execute("UPDATE invoke.evento SET datos = '{}' WHERE id = %s", (eid,))
    cur.execute("ROLLBACK TO SAVEPOINT a")


def test_la_app_no_escribe_proyecciones(cur, esc):
    e = esc.estudio()
    c = esc.caso(e)
    cur.execute("SAVEPOINT a")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("UPDATE invoke.caso_estado SET intentos = 0 WHERE caso_id = %s", (c["id"],))
    cur.execute("ROLLBACK TO SAVEPOINT a")


def test_asignacion_exclusiva_y_cascada(cur, esc):
    e = esc.estudio()
    a, b = esc.usuario("Ana"), esc.usuario("Beto")
    esc.sesion(a, e)
    esc.sesion(b, e)
    c1 = esc.caso(e)
    caso_a = estacion.siguiente_caso(cur, a)
    assert caso_a["id"] == c1["id"]
    assert estacion.siguiente_caso(cur, b) is None          # el único caso ya es de Ana
    assert estacion.siguiente_caso(cur, a)["id"] == c1["id"]  # idempotente
    # Un agendado para Beto, en ventana estrecha, gana sobre el pool.
    c2, c3 = esc.caso(e), esc.caso(e)
    ahora = dt.datetime.now(dt.timezone.utc)
    estacion.saltar_caso(cur, a, c1["id"], "prueba")
    esc.sesion(a, e)
    caso = estacion.siguiente_caso(cur, a)
    esc.intento(a, caso, 4, 5, agenda={"desde": (ahora - dt.timedelta(minutes=5)).isoformat(),
                                       "hasta": (ahora + dt.timedelta(minutes=20)).isoformat(),
                                       "preservar_encuestador": False})
    agendado = caso["id"]
    assert esc.caso_estado(agendado)["estado"] == "agendado"
    assert estacion.siguiente_caso(cur, b)["id"] == agendado


def test_asignacion_concurrente_skip_locked(dsn_base):
    """Dos conexiones reales compitiendo por un único caso: uno lo obtiene, el otro no."""
    from psycopg.rows import dict_row

    from tests.conftest import Escenario

    with psycopg.connect(dsn_base, row_factory=dict_row, autocommit=True) as c0:
        c0.execute("SET ROLE invoke_app")
        k = c0.cursor()
        esc = Escenario(k)
        e = esc.estudio()
        a, b = esc.usuario("A"), esc.usuario("B")
        esc.sesion(a, e)
        esc.sesion(b, e)
        esc.caso(e)
    c1 = psycopg.connect(dsn_base, row_factory=dict_row)
    c2 = psycopg.connect(dsn_base, row_factory=dict_row)
    try:
        for c in (c1, c2):
            c.execute("SET ROLE invoke_app")
        r1 = c1.execute("SELECT invoke.asignar_caso(%s, %s) AS c", (a.participante_id, e["id"])).fetchone()["c"]
        # c1 no hizo commit: la fila está bloqueada y c2 la saltea sin esperar.
        r2 = c2.execute("SELECT invoke.asignar_caso(%s, %s) AS c", (b.participante_id, e["id"])).fetchone()["c"]
        c1.commit()
        c2.commit()
        assert r1 is not None and r2 is None
    finally:
        c1.close()
        c2.close()


def test_reintento_espera_y_tope(cur, esc):
    e = esc.estudio(tope_intentos_caso=2)
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    caso = estacion.siguiente_caso(cur, a)
    t0 = dt.datetime.now(dt.timezone.utc)
    esc.intento(a, caso, 6)  # no contesta: 120 min
    ce = esc.caso_estado(c["id"])
    assert ce["estado"] == "pendiente" and ce["asignado_a"] is None
    assert ce["disponible_desde"] >= t0 + dt.timedelta(minutes=119)
    assert estacion.siguiente_caso(cur, a) is None   # todavía esperando
    # Segundo intento (forzado): alcanza el tope y el cierre queda como hecho.
    db.evento(cur, "caso_asignado", caso_id=c["id"], participante_id=a.participante_id)
    esc.intento(a, caso, 3)
    ce = esc.caso_estado(c["id"])
    assert ce["estado"] == "cerrado" and ce["motivo_cierre"] == "tope_intentos"
    assert db.uno(cur, "SELECT count(*) AS n FROM invoke.evento WHERE caso_id = %s AND tipo = 'caso_cerrado'", (c["id"],))["n"] == 1


def test_rotacion_de_franja(cur, esc):
    e = esc.estudio(franjas=(("manana", "00:00", "12:00"), ("tarde", "12:00", "23:59:59")))
    r = db.uno(cur, """SELECT invoke.calcular_proximo_intento(%s, '2026-11-20 10:00-03', 'rotar', 'manana', '{}') AS t""",
               (e["id"],))["t"]
    # Montevideo: la franja de tarde arranca a las 12:00 locales = 15:00 UTC.
    assert r == dt.datetime(2026, 11, 20, 15, 0, tzinfo=dt.timezone.utc)
    r = db.uno(cur, """SELECT invoke.calcular_proximo_intento(%s, '2026-11-20 10:00-03', 'misma', 'manana', '{}') AS t""",
               (e["id"],))["t"]
    assert r == dt.datetime(2026, 11, 20, 13, 0, tzinfo=dt.timezone.utc)
    # Tope por franja alcanzado en la tarde: rotar no tiene a dónde ir y cae en mañana del día siguiente.
    r = db.uno(cur, """SELECT invoke.calcular_proximo_intento(%s, '2026-11-20 13:00-03', 'rotar', 'manana', '{"tarde": 3}') AS t""",
               (e["id"],))["t"]
    assert r == dt.datetime(2026, 11, 21, 3, 0, tzinfo=dt.timezone.utc)
    # Todas las franjas agotadas: no hay próximo intento.
    r = db.uno(cur, """SELECT invoke.calcular_proximo_intento(%s, now(), 'rotar', 'manana', '{"tarde": 3, "manana": 3}') AS t""",
               (e["id"],))["t"]
    assert r is None


def test_lista_negra_transversal(cur, esc):
    e1, e2 = esc.estudio(), esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e1)
    c1 = esc.caso(e1, telefono="099 123 456")
    c2 = esc.caso(e2, telefono="+598 99123456")
    caso = estacion.siguiente_caso(cur, a)
    esc.intento(a, caso, 5)
    assert esc.caso_estado(c1["id"])["estado"] == "cerrado"
    esc.sesion(a, e2)
    assert estacion.siguiente_caso(cur, a) is None   # el mismo número normalizado está bloqueado en otro estudio


def test_solo_contesta_habilita_encuesta(cur, esc):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    estacion.siguiente_caso(cur, a)
    with pytest.raises(psycopg.errors.RaiseException, match="disposición 4"):
        esc.intento(a, c, 6, 4)


def test_termina_y_explica_exige_motivo(cur, esc):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    estacion.siguiente_caso(cur, a)
    with pytest.raises(psycopg.errors.RaiseException, match="motivo"):
        esc.intento(a, c, 4, 7)


def test_completa_validada_por_el_servidor(cur, esc):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    estacion.siguiente_caso(cur, a)
    with pytest.raises(ErrorHttp, match="no está completa"):
        esc.intento(a, c, 4, 3)
    estacion.guardar_respuestas(cur, a, c["id"], {"SEXO": "2", "NOTA": 8}, None)
    llamada = esc.intento(a, c, 4, 3)
    assert esc.caso_estado(c["id"])["estado"] == "cerrado"
    assert llamada


def test_promocion_de_parcial(cur, esc, conn):
    e = esc.estudio()
    a, s = esc.usuario(), esc.usuario("Sup", roles=("supervisor",))
    esc.sesion(a, e)
    c = esc.caso(e)
    estacion.siguiente_caso(cur, a)
    esc.intento(a, c, 4, 7, motivo="cortó a mitad")
    with pytest.raises(psycopg.errors.RaiseException, match="autor y motivo"), conn.transaction():
        supervision.promover_parcial(cur, s, c["id"], "  ")
    supervision.promover_parcial(cur, s, c["id"], "faltaban solo preguntas de perfil")
    assert db.uno(cur, "SELECT codigo FROM invoke.caso_ultima_disp_enc WHERE caso_id = %s", (c["id"],))["codigo"] == 3
    with pytest.raises(psycopg.errors.RaiseException, match="Interrumpida"), conn.transaction():
        supervision.promover_parcial(cur, s, c["id"], "otra vez")


def test_proyeccion_reconstruible(cur, esc, conn):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    caso = estacion.siguiente_caso(cur, a)
    esc.intento(a, caso, 6)
    antes = esc.caso_estado(c["id"])
    cur.execute("RESET ROLE")
    cur.execute("SELECT invoke.reconstruir_caso_estado(%s)", (c["id"],))
    cur.execute("SET ROLE invoke_app")
    despues = esc.caso_estado(c["id"])
    for k in ("estado", "intentos", "intentos_por_franja", "ultima_disp_tel", "disponible_desde", "abandonada_previa"):
        assert antes[k] == despues[k], k


def test_sustitutos(cur, esc):
    e = esc.estudio(sustitutos_habilitados=True)
    a = esc.usuario()
    esc.sesion(a, e)
    t = esc.caso(e, sustituto="R1", grupo_sustituto=1)
    s = esc.caso(e, sustituto="R1", grupo_sustituto=2)
    assert esc.caso_estado(t["id"])["activo"] and not esc.caso_estado(s["id"])["activo"]
    caso = estacion.siguiente_caso(cur, a)
    assert caso["id"] == t["id"]
    esc.intento(a, caso, 4, 4)  # rechazo: activa el siguiente sustituto
    assert esc.caso_estado(s["id"])["activo"]
    assert estacion.siguiente_caso(cur, a)["id"] == s["id"]


def test_cuotas_por_propiedad_y_pregunta(cur, esc):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    cur.execute("""INSERT INTO invoke.cuota (estudio_id, nombre, tipo, propiedad, valores, objetivo)
                   VALUES (%s, 'mujeres', 'propiedad', 'sexo', '{F}', 1)""", (e["id"],))
    cur.execute("""INSERT INTO invoke.cuota (estudio_id, nombre, tipo, pregunta, valores, objetivo)
                   VALUES (%s, 'nota alta', 'pregunta', 'NOTA', '{9,10}', 1)""", (e["id"],))
    c1 = esc.caso(e, propiedades={"sexo": "F"})
    caso = estacion.siguiente_caso(cur, a)
    assert caso["id"] == c1["id"]
    estacion.guardar_respuestas(cur, a, c1["id"], {"SEXO": "2", "NOTA": 10}, None)
    esc.intento(a, caso, 4, 3)
    c2 = esc.caso(e, propiedades={"sexo": "F"})
    c3 = esc.caso(e, propiedades={"sexo": "M"})
    caso = estacion.siguiente_caso(cur, a)
    assert caso["id"] == c3["id"]   # la cuota de mujeres está llena: c2 queda afuera
    llenas = estacion.evaluar_cuotas(cur, a, c3["id"], {"NOTA": 9})
    assert [q["nombre"] for q in llenas] == ["nota alta"]
    assert estacion.evaluar_cuotas(cur, a, c3["id"], {"NOTA": 5}) == []
    assert c2


def test_fuera_de_marco(cur, esc, conn):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e, estrato="empresas")
    caso = estacion.siguiente_caso(cur, a)
    esc.intento(a, caso, 4, 5, agenda={"desde": "2030-01-01T10:00:00Z", "hasta": "2030-01-01T11:00:00Z"})
    r = estacion.clasificar_numero(cur, a, "098 765 432", None)
    assert r["situacion"] == "desconocido" and r["puede_derivado"] is False and r["motivo_derivado"]
    with pytest.raises(psycopg.errors.RaiseException, match="no admite"), conn.transaction():
        estacion.resolver_numero(cur, a, {"opcion": "derivado", "numero": "098765432", "caso_origen": c["id"],
                                          "motivo": "colega"})
    cur.execute("UPDATE invoke.estudio SET fuera_marco_habilitado = true WHERE id = %s", (e["id"],))
    # El número ya existente en la muestra se detecta (caso límite de la política).
    assert estacion.clasificar_numero(cur, a, c["numero"], None)["situacion"] == "en_otro_caso"
    nuevo = estacion.resolver_numero(cur, a, {"opcion": "derivado", "numero": "098765432", "caso_origen": c["id"],
                                              "motivo": "es el gerente de compras"})
    d = db.uno(cur, "SELECT * FROM invoke.caso WHERE id = %s", (nuevo["caso_id"],))
    assert d["origen"] == "derivado" and d["caso_origen"] == c["id"] and d["estrato"] == "empresas"
    assert d["usuario_creador"] == a.uid
    estacion.guardar_respuestas(cur, a, d["id"], {"SEXO": "1", "NOTA": 7}, None)
    esc.intento(a, {"id": d["id"], "telefono_id": nuevo["telefono_id"]}, 4, 3)
    conteo = db.uno(cur, "SELECT invoke.conteo_tasas(%s, 'parcial') AS c", (e["id"],))["c"]
    assert conteo["marco"]["C"] == 0                     # no entra a las tasas del marco
    assert conteo["fuera_marco"]["derivado"]["completas"] == 1
    assert conteo["tamano_marco"] == 1


def test_tope_fuera_de_marco_por_guia(cur, esc):
    e = esc.estudio(fuera_marco_habilitado=True)
    cur.execute("UPDATE invoke.guia SET tope_fuera_marco = 0.4 WHERE id = %s", (e["guia_id"],))
    a = esc.usuario()
    c1 = esc.caso(e)
    esc.caso(e)
    cur.execute("SELECT invoke.crear_caso_fuera_de_marco(%s, '091000001', 'derivado', %s, 'm')", (c1["id"], a.uid))
    with pytest.raises(psycopg.errors.RaiseException, match="tope"):
        cur.execute("SELECT invoke.crear_caso_fuera_de_marco(%s, '091000002', 'derivado', %s, 'm')", (c1["id"], a.uid))


def test_llamada_huerfana_rechazada(cur, esc):
    e = esc.estudio()
    a = esc.usuario()
    db.evento(cur, "llamada_originada", estudio_id=e["id"], llamada_id="00000000-0000-0000-0000-000000000001",
              participante_id=a.participante_id, datos={"numero": "099", "tipo": "encuesta"})
    with pytest.raises(psycopg.errors.RaiseException, match="huérfanas"):
        db.evento(cur, "disposicion_telefono", estudio_id=e["id"], llamada_id="00000000-0000-0000-0000-000000000001",
                  datos={"codigo": 6})


def test_pacer_abandono_y_preview_posterior(cur, esc):
    e = esc.estudio(modo_discado="power")
    a = esc.usuario()
    c = esc.caso(e)
    reservas = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 5)", (e["id"],))
    assert [r["caso_id"] for r in reservas] == [c["id"]]
    ll = reservas[0]["llamada_id"]
    db.evento(cur, "llamada_atendida", caso_id=c["id"], llamada_id=ll)
    # Nadie disponible: abandonada.
    assert db.uno(cur, "SELECT invoke.tomar_participante_para(%s) AS p", (ll,))["p"] is None
    db.evento(cur, "llamada_abandonada", caso_id=c["id"], llamada_id=ll)
    db.evento(cur, "disposicion_telefono", caso_id=c["id"], llamada_id=ll, datos={"codigo": 30, "automatica": True})
    ce = esc.caso_estado(c["id"])
    assert ce["abandonada_previa"] and ce["estado"] == "pendiente"
    # Nunca vuelve al pacer...
    cur.execute("SELECT invoke.reservar_casos_pacer(%s, 5)", (e["id"],))
    assert cur.fetchall() == []
    # ...pero sí al asignador, en preview, cuando vence la espera.
    cur.execute("RESET ROLE")
    cur.execute("UPDATE invoke.caso_estado SET disponible_desde = now() - interval '1 min' WHERE caso_id = %s", (c["id"],))
    cur.execute("SET ROLE invoke_app")
    esc.sesion(a, e)
    assert estacion.siguiente_caso(cur, a)["id"] == c["id"]
    t = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, now() - interval '1 hour', now() + interval '1 min')", (e["id"],))
    assert t["abandonadas"] == 1 and float(t["tab_reportada"]) == 1.0


def test_power_conecta_al_disponible(cur, esc):
    e = esc.estudio(modo_discado="power")
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    ll = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e["id"],))[0]["llamada_id"]
    assert db.uno(cur, "SELECT invoke.tomar_participante_para(%s) AS p", (ll,))["p"] == a.participante_id
    ce = esc.caso_estado(c["id"])
    assert ce["asignado_a"] == a.participante_id and ce["estado"] == "en_llamada"
    assert esc.estado(a)["estado"] == "en_llamada"


def test_reclasificacion_amd(cur, esc):
    e = esc.estudio(modo_discado="power")
    s = esc.usuario("Sup", roles=("supervisor",))
    c = esc.caso(e)
    ll = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e["id"],))[0]["llamada_id"]
    db.evento(cur, "llamada_atendida", caso_id=c["id"], llamada_id=ll)
    db.evento(cur, "amd_resultado", caso_id=c["id"], llamada_id=ll, datos={"resultado": "MACHINE"})
    db.evento(cur, "disposicion_telefono", caso_id=c["id"], llamada_id=ll, datos={"codigo": 31, "automatica": True})
    supervision.reclasificar_amd(cur, s, str(ll))
    l = db.uno(cur, "SELECT disp_tel, reclasificada FROM invoke.llamada WHERE llamada_id = %s", (ll,))
    assert l["disp_tel"] == 32 and l["reclasificada"]
    t = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, now() - interval '1 hour', now() + interval '1 min')", (e["id"],))
    assert t["fp_amd"] == 1 and float(t["tab_real"]) == 1.0


def test_cdr_compat_sin_equivalente(cur, esc):
    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    caso = estacion.siguiente_caso(cur, a)
    esc.intento(a, caso, 4, 4)
    fila = db.uno(cur, "SELECT * FROM invoke.cdr_compat WHERE campana = %s", (e["codigo"],))
    assert fila["disposicion"] == "Contesta|Rechazada"   # equivalencia pendiente: etiqueta propia
    cur.execute("RESET ROLE")
    cur.execute("SELECT 1")
    cur.execute("SET ROLE invoke_app")
    assert db.uno(cur, "SELECT invoke.etiqueta_cdr(NULL, true, 30::smallint, 'Abandonada') AS t")["t"] == "SIN_EQUIVALENTE:30"
    assert c


def test_versiones_fijas_tras_activar(cur, esc):
    e = esc.estudio()
    with pytest.raises(psycopg.errors.RaiseException, match="versiones"):
        cur.execute("UPDATE invoke.estudio SET version_formulas = 1, version_diccionario = 2 WHERE id = %s", (e["id"],))
