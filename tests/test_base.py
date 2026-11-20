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


def test_codec_g711_por_defecto_y_opus_por_puesto(cur, esc):
    """Encargo de cierre D1 + H4: sin Opus salvo que el puesto lo tenga habilitado."""
    from invoke.servicios import credenciales

    a, b = esc.usuario("SinOpus"), esc.usuario("ConOpus")
    cur.execute("UPDATE invoke.participante SET opus_habilitado = true WHERE id = %s", (b.participante_id,))
    assert credenciales.rotar_sip(cur, a.participante_id, a.nombre)["codecs"] == "alaw,ulaw"
    assert credenciales.rotar_sip(cur, b.participante_id, b.nombre)["codecs"] == "opus,alaw,ulaw"
    filas = dict((r["id"], r["allow"]) for r in db.todos(cur, "SELECT id, allow FROM asterisk.ps_endpoints WHERE id = ANY(%s)",
                                                         ([f"p{a.participante_id}", f"p{b.participante_id}"],)))
    assert filas == {f"p{a.participante_id}": "alaw,ulaw", f"p{b.participante_id}": "opus,alaw,ulaw"}
    # Deshabilitar Opus surte efecto en la próxima rotación.
    cur.execute("UPDATE invoke.participante SET opus_habilitado = false WHERE id = %s", (b.participante_id,))
    credenciales.rotar_sip(cur, b.participante_id, b.nombre)
    assert db.uno(cur, "SELECT allow FROM asterisk.ps_endpoints WHERE id = %s", (f"p{b.participante_id}",))["allow"] == "alaw,ulaw"
    # El valor por defecto de la columna ya no incluye Opus.
    cur.execute("RESET ROLE")
    d = db.uno(cur, """SELECT column_default FROM information_schema.columns
                       WHERE table_schema = 'asterisk' AND table_name = 'ps_endpoints' AND column_name = 'allow'""")
    cur.execute("SET ROLE invoke_app")
    assert "opus" not in d["column_default"]


# ───────────────────────── Encargo de cierre: H3 y H1 ─────────────────────────

CSV_GUIA = "telefono;identificador\n" + "\n".join(f"09{1000000 + i};C{i:03d}" for i in range(40)) + "\n"


def _orden_de_guia(cur, guia_id):
    return [r["identificador"] for r in db.todos(
        cur, "SELECT identificador FROM invoke.caso WHERE guia_id = %s ORDER BY orden_aleatorio", (guia_id,))]


def test_semilla_reproduce_el_orden(cur, esc):
    """H3: la misma guía con la misma semilla da el mismo orden; con otra semilla, otro."""
    from invoke.servicios import muestra

    e1, e2 = esc.estudio(), esc.estudio()
    a = esc.usuario("Adm", roles=("administrador",))
    g1 = muestra.cargar_guia(cur, e1["id"], "g", CSV_GUIA, a.uid, semilla=12345)
    g2 = muestra.cargar_guia(cur, e2["id"], "g", CSV_GUIA, a.uid, semilla=12345)
    g3 = muestra.cargar_guia(cur, e2["id"], "g3", CSV_GUIA, a.uid, semilla=999)
    o1, o2, o3 = (_orden_de_guia(cur, g["guia_id"]) for g in (g1, g2, g3))
    assert o1 == o2 and o1 != o3
    assert o1 != sorted(o1)                                 # no es el orden del archivo
    ev = db.uno(cur, "SELECT datos FROM invoke.evento WHERE tipo = 'muestra_cargada' AND estudio_id = %s", (e1["id"],))
    assert ev["datos"]["semilla"] == 12345
    # Sin semilla explícita se genera una y queda registrada.
    g4 = muestra.cargar_guia(cur, e1["id"], "g4", CSV_GUIA, a.uid)
    assert db.uno(cur, "SELECT semilla FROM invoke.guia WHERE id = %s", (g4["guia_id"],))["semilla"] == g4["semilla"]
    # El orden no se modifica después.
    cur.execute("SAVEPOINT a")
    with pytest.raises(psycopg.errors.RaiseException, match="no se modifican"):
        cur.execute("UPDATE invoke.caso SET orden_aleatorio = 0.5 WHERE guia_id = %s", (g1["guia_id"],))
    cur.execute("ROLLBACK TO SAVEPOINT a")


def test_el_pool_lee_el_indice(cur, esc):
    """H3: con una guía grande, tomar el siguiente caso es una lectura del índice, no un ordenamiento."""
    e = esc.estudio()
    cur.execute("RESET ROLE")
    cur.execute("""INSERT INTO invoke.caso (estudio_id, guia_id, posicion)
                   SELECT %s, %s, g FROM generate_series(1, 20000) g""", (e["id"], e["guia_id"]))
    cur.execute("""INSERT INTO invoke.telefono (caso_id, estudio_id, numero)
                   SELECT id, estudio_id, '09' || (1000000 + id)::text FROM invoke.caso WHERE guia_id = %s""", (e["guia_id"],))
    cur.execute("ANALYZE invoke.caso; ANALYZE invoke.caso_estado; ANALYZE invoke.telefono")
    cur.execute("""EXPLAIN SELECT ce.caso_id FROM invoke.caso c JOIN invoke.caso_estado ce ON ce.caso_id = c.id
                   WHERE c.estudio_id = %s AND c.guia_id = %s AND ce.activo AND ce.asignado_a IS NULL
                     AND ce.estado = 'pendiente' AND ce.disponible_desde <= now() AND (ce.intentos = 0) = true
                   ORDER BY c.orden_aleatorio LIMIT 1 FOR UPDATE OF ce SKIP LOCKED""", (e["id"], e["guia_id"]))
    plan = "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
    cur.execute("SET ROLE invoke_app")
    assert "caso_pool_idx" in plan and "Sort" not in plan, plan


def _pool_con(cur, esc, frescos, reintentos, **params):
    e = esc.estudio(**params)
    for _ in range(frescos + reintentos):
        esc.caso(e)
    if reintentos:
        cur.execute("RESET ROLE")
        cur.execute("""UPDATE invoke.caso_estado SET intentos = 1
                       WHERE caso_id IN (SELECT id FROM invoke.caso WHERE estudio_id = %s ORDER BY id LIMIT %s)""",
                    (e["id"], reintentos))
        cur.execute("SET ROLE invoke_app")
    return e


def test_mezcla_de_frescos_sorteada_en_cada_pedido(cur, esc):
    """H1: con 1.000 pedidos, la proporción observada de frescos cae a ±3 puntos de la configurada."""
    e = _pool_con(cur, esc, 60, 60, proporcion_frescos=0.3)
    a = esc.usuario()
    esc.sesion(a, e)
    cur.execute("SELECT setseed(0.42)")
    frescos = 0
    for _ in range(1000):
        caso = db.uno(cur, "SELECT invoke.asignar_caso(%s, %s) AS c", (a.participante_id, e["id"]))["c"]
        ev = db.uno(cur, "SELECT datos FROM invoke.evento WHERE caso_id = %s AND tipo = 'caso_asignado' ORDER BY id DESC LIMIT 1",
                    (caso,))
        fresco = ev["datos"]["poblacion"] == "fresco"
        assert fresco == (db.uno(cur, "SELECT intentos FROM invoke.caso_estado WHERE caso_id = %s", (caso,))["intentos"] == 0)
        frescos += fresco
        db.evento(cur, "caso_liberado", caso_id=caso, participante_id=a.participante_id, datos={"motivo": "test"})
    assert abs(frescos / 1000 - 0.3) <= 0.03, frescos
    r = db.uno(cur, "SELECT * FROM invoke.reparto_frescos(%s, now() - interval '1 day')", (e["id"],))
    assert r["frescos"] == frescos and r["reintentos"] == 1000 - frescos and float(r["configurada"]) == 0.3


def test_si_la_poblacion_sorteada_esta_vacia_se_usa_la_otra(cur, esc):
    """H1: con proporcion_frescos = 1 y sin frescos, el asignador devuelve reintentos, nunca NULL."""
    e = _pool_con(cur, esc, 0, 5, proporcion_frescos=1)
    usuarios = [esc.usuario() for _ in range(5)]
    for u in usuarios:
        esc.sesion(u, e)
        caso = estacion.siguiente_caso(cur, u)
        assert caso is not None and caso["intentos"] == 1
    # Sin nada en el pool, recién ahí devuelve NULL.
    otro = esc.usuario()
    esc.sesion(otro, e)
    assert estacion.siguiente_caso(cur, otro) is None


def test_pacer_mezcla_y_graba(cur, esc):
    e = _pool_con(cur, esc, 3, 3, modo_discado="power", proporcion_frescos=0.5)
    filas = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 6)", (e["id"],))
    assert len(filas) == 6
    datos = [r["datos"] for r in db.todos(cur, "SELECT datos FROM invoke.evento WHERE estudio_id = %s AND tipo = 'llamada_originada'",
                                          (e["id"],))]
    assert all(d["grabar"] is True for d in datos)
    assert sorted(d["poblacion"] for d in datos) == ["fresco"] * 3 + ["reintento"] * 3


def test_caso_pendiente_no_cruza_de_estudio(cur, esc, conn):
    """Hallado en el PBX de laboratorio: un caso conectado sin clasificar no aparece en
    la sesión de otro estudio; se pide clasificarlo primero."""
    from invoke.enrutador import ErrorHttp

    e1, e2 = esc.estudio(modo_discado="power"), esc.estudio()
    a = esc.usuario()
    for e in (e1, e2):
        cur.execute("INSERT INTO invoke.estudio_usuario VALUES (%s, %s)", (e["id"], a.uid))
    esc.sesion(a, e1)
    esc.caso(e1)
    esc.caso(e2)
    ll = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e1["id"],))[0]["llamada_id"]
    assert db.uno(cur, "SELECT invoke.tomar_participante_para(%s) AS p", (ll,))["p"] == a.participante_id
    with pytest.raises(ErrorHttp, match="sin clasificar"):
        estacion.iniciar_sesion(cur, a, e2["id"])
    db.evento(cur, "estado_participante", participante_id=a.participante_id, estudio_id=e2["id"], datos={"estado": "disponible"})
    cur.execute("RESET ROLE")
    cur.execute("UPDATE invoke.participante_estado SET estudio_id = %s WHERE participante_id = %s", (e2["id"], a.participante_id))
    cur.execute("SET ROLE invoke_app")
    with pytest.raises(psycopg.errors.RaiseException, match="sin clasificar en otro estudio"), conn.transaction():
        cur.execute("SELECT invoke.asignar_caso(%s, %s)", (a.participante_id, e2["id"]))
