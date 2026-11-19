"""La superficie HTTP de punta a punta, con autenticación de desarrollo."""
import json

import pytest

from invoke import api
from invoke.enrutador import Peticion
from tests.datos import CUESTIONARIO_MINIMO


@pytest.fixture(autouse=True)
def auth_dev(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_DEV", "1")


def pedir(metodo, ruta, uid=None, cuerpo=None, query=None):
    cab = {"X-Dev-Uid": uid} if uid else {}
    r = api.despachar(Peticion(metodo, ruta, query or {}, json.dumps(cuerpo).encode() if cuerpo is not None else b"", cab))
    datos = json.loads(r.cuerpo) if r.tipo == "application/json" else r.cuerpo
    return r.estado, datos


def test_salud_y_auth(conn):
    assert pedir("GET", "/api/salud")[0] == 200
    assert pedir("GET", "/api/yo")[0] == 401
    assert pedir("GET", "/api/nada")[0] == 404


def test_flujo_completo(conn, esc):
    adm = esc.usuario("Adm", roles=("administrador",))
    enc = esc.usuario("Enc", roles=("encuestador",))
    assert pedir("GET", "/api/estudios", enc.uid)[0] == 403

    st, e = pedir("POST", "/api/estudios", adm.uid, {"codigo": "PIL01", "nombre": "Piloto", "modo_discado": "preview"})
    assert st == 200, e
    eid = e["id"]
    st, r = pedir("PATCH", f"/api/estudios/{eid}", adm.uid, {"estado": "activo"})
    assert st == 409 and "cuestionario" in r["error"]
    st, cq = pedir("PUT", f"/api/estudios/{eid}/cuestionario", adm.uid, CUESTIONARIO_MINIMO)
    assert st == 200, cq
    assert pedir("POST", f"/api/cuestionarios/{cq['id']}/publicar", adm.uid)[0] == 200
    st, r = pedir("PATCH", f"/api/estudios/{eid}", adm.uid, {
        "estado": "activo", "franjas": [{"nombre": "todo", "desde": "00:00", "hasta": "23:59:59"}]})
    assert st == 200, r
    st, g = pedir("POST", f"/api/estudios/{eid}/guias", adm.uid, {
        "nombre": "base", "csv": "telefono;identificador;nombre;sexo\n099111222;A1;Ana;F\n;A2;Sin tel;M\n098333444;A3;Luis;M\n"})
    assert st == 200 and g["casos"] == 2 and g["total_rechazadas"] == 1
    pedir("PUT", f"/api/estudios/{eid}/encuestadores", adm.uid, {"uids": [enc.uid]})

    st, s = pedir("POST", "/api/estacion/sesion", enc.uid, {"estudio_id": eid})
    assert st == 200, s
    assert s["sip"]["clave"] and s["cuestionario"]["definicion"]["id"] == "minimo"
    esc.cur.execute("INSERT INTO invoke.evento (tipo, participante_id, datos) VALUES ('audio_conectado', %s, '{\"canal\": \"c1\"}')",
                    (enc.participante_id,))
    pedir("POST", "/api/estacion/estado", enc.uid, {"estado": "disponible"})
    st, r = pedir("POST", "/api/estacion/siguiente", enc.uid)
    caso = r["caso"]
    assert caso["propiedades"]["nombre"] in ("Ana", "Luis")
    st, ll = pedir("POST", "/api/estacion/llamar", enc.uid, {"caso_id": caso["id"], "telefono_id": caso["telefonos"][0]["id"]})
    assert st == 200, ll
    esc.cur.execute("INSERT INTO invoke.evento (tipo, caso_id, llamada_id, participante_id) VALUES ('llamada_conectada', %s, %s, %s)",
                    (caso["id"], ll["llamada_id"], enc.participante_id))
    pedir("POST", f"/api/estacion/casos/{caso['id']}/respuestas", enc.uid, {"respuestas": {"SEXO": "1", "NOTA": 7},
                                                                            "llamada_id": ll["llamada_id"]})
    st, r = pedir("POST", "/api/estacion/finalizar", enc.uid, {"caso_id": caso["id"], "llamada_id": ll["llamada_id"],
                                                               "disposicion_telefono": 4, "disposicion_encuesta": 3})
    assert st == 200, r
    st, ficha = pedir("GET", f"/api/estudios/{eid}/reportes/ficha", adm.uid)
    assert st == 200 and ficha["conteos_por_grupo"]["C"] == 1
    st, cdr = pedir("GET", "/api/cdr", adm.uid, query={"estudio": str(eid)})
    assert st == 200 and cdr[0]["disposicion"] == "Contesta|Completa"
    for fmt in ("csv", "xlsx", "sav"):
        st, contenido = pedir("GET", f"/api/estudios/{eid}/exportar", adm.uid, query={"formato": fmt})
        assert st == 200 and len(contenido) > 50, fmt
    st, contenido = pedir("GET", f"/api/estudios/{eid}/exportar", adm.uid, query={"formato": "csv"})
    texto = contenido.decode("utf-8-sig")
    assert "origen" in texto.splitlines()[0] and ",marco," in texto
    st, sala = pedir("GET", "/api/supervision/sala", adm.uid)
    assert st == 200 and sala[0]["nombre"] == "Enc"


def test_el_puesto_no_toca_casos_ajenos(conn, esc):
    """Encargo de cierre D4: leer respuestas, iniciar y clasificar exigen que el caso sea del encuestador."""
    e = esc.estudio()
    a, b = esc.usuario("Ana"), esc.usuario("Beto")
    esc.sesion(a, e)
    esc.sesion(b, e)
    c = esc.caso(e)
    from invoke.servicios import estacion
    estacion.siguiente_caso(esc.cur, a)                      # el caso es de Ana
    ll = estacion.llamar(esc.cur, a, {"caso_id": c["id"], "telefono_id": c["telefono_id"]})["llamada_id"]
    assert pedir("GET", f"/api/estacion/casos/{c['id']}/respuestas", a.uid)[0] == 200
    assert pedir("GET", f"/api/estacion/casos/{c['id']}/respuestas", b.uid)[0] == 409
    assert pedir("POST", f"/api/estacion/casos/{c['id']}/iniciar", b.uid, {"llamada_id": ll})[0] == 409
    st, r = pedir("POST", "/api/estacion/finalizar", b.uid, {"caso_id": c["id"], "llamada_id": ll,
                                                             "disposicion_telefono": 6})
    assert st == 409 and "no está asignado" in r["error"]
    assert pedir("POST", f"/api/estacion/casos/{c['id']}/cuotas", b.uid, {"respuestas": {}})[0] == 409
    # Ana, que tiene el caso, sí puede clasificarlo.
    assert pedir("POST", "/api/estacion/finalizar", a.uid, {"caso_id": c["id"], "llamada_id": ll,
                                                            "disposicion_telefono": 6})[0] == 200


def test_errores_inesperados_y_pool_agotado_son_respuestas(conn, monkeypatch):
    """Encargo M2 y M3: lo inesperado es un 500 con JSON y el pool agotado, un 503."""
    from psycopg_pool import PoolTimeout

    def revienta(p):
        raise RuntimeError("bug")

    def agotado(p):
        raise PoolTimeout("sin conexiones")

    monkeypatch.setattr(api.app, "rutas", list(api.app.rutas))
    api.app.ruta("GET", "/api/prueba/revienta", publica=True)(revienta)
    api.app.ruta("GET", "/api/prueba/agotado", publica=True)(agotado)
    st, r = pedir("GET", "/api/prueba/revienta")
    assert st == 500 and "error interno" in r["error"]
    st, r = pedir("GET", "/api/prueba/agotado")
    assert st == 503


def test_sala_solo_recalcula_los_estudios_que_cambiaron():
    """Encargo M4."""
    from services.sala.main import Sala

    s = Sala()
    s.supervisores = {"ws1": 1, "ws2": 2, "ws3": None, "ws4": 1}
    assert dict(s.destinatarios({1})) == {1: ["ws1", "ws4"], None: ["ws3"]}
    assert dict(s.destinatarios(set())) == {None: ["ws3"]}
