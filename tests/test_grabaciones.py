"""Encargo de cierre H2: se graba todo y cada grabación termina en el bucket."""
import datetime as dt

import pytest

from invoke import db
from invoke.servicios import estacion
from services.ari.grabaciones import Pendiente, objeto_de, subir_pendientes


class PbxDoble:
    def __init__(self, archivos):
        self.archivos = dict(archivos)
        self.borrados = []

    def bajar(self, nombre):
        return self.archivos.get(nombre)

    def borrar(self, nombre):
        self.borrados.append(nombre)
        self.archivos.pop(nombre, None)


class BucketDoble:
    nombre = "invoke-grabaciones"

    def __init__(self, falla=False):
        self.objetos = {}
        self.falla = falla

    def subir(self, objeto, datos, md5):
        if self.falla:
            raise RuntimeError("Cloud Storage no disponible")
        self.objetos[objeto] = (datos, md5)


T = dt.datetime(2026, 11, 20, 15, 30, tzinfo=dt.timezone.utc)


def test_sube_registra_y_recien_despues_borra():
    pbx = PbxDoble({"llamada-a": b"RIFF...a", "llamada-b": b"RIFF...b"})
    bucket = BucketDoble()
    orden = []

    def registrar(p, datos):
        orden.append(("registrado", p.nombre, p.nombre in pbx.archivos))
        assert datos["uri"] == f"gs://invoke-grabaciones/{objeto_de(p)}" and datos["bytes"] == 8

    pend = [Pendiente("a", "PIL01", "llamada-a", T), Pendiente("b", "PIL01", "llamada-b", T)]
    r = subir_pendientes(pend, pbx, bucket, registrar)
    assert r == {"subidas": 2, "faltantes": 0, "errores": 0}
    assert set(bucket.objetos) == {"PIL01/2026/11/20/a.wav", "PIL01/2026/11/20/b.wav"}
    # El archivo seguía en el PBX cuando se registró la subida: nunca se borra antes.
    assert orden == [("registrado", "llamada-a", True), ("registrado", "llamada-b", True)]
    assert pbx.borrados == ["llamada-a", "llamada-b"]


def test_si_falla_la_subida_no_se_borra_nada():
    pbx = PbxDoble({"llamada-a": b"x"})
    r = subir_pendientes([Pendiente("a", "E", "llamada-a", T)], pbx, BucketDoble(falla=True), lambda p, d: None)
    assert r["errores"] == 1 and pbx.borrados == [] and "llamada-a" in pbx.archivos


def test_archivo_faltante_se_informa():
    r = subir_pendientes([Pendiente("a", "E", "llamada-a", T)], PbxDoble({}), BucketDoble(), lambda p, d: None)
    assert r == {"subidas": 0, "faltantes": 1, "errores": 0}


@pytest.mark.usefixtures("dsn_base")
def test_todas_las_llamadas_de_encuesta_se_graban(cur, esc):
    e = esc.estudio(muestreo_grabacion=0)          # el campo viejo ya no tiene efecto
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    estacion.siguiente_caso(cur, a)
    r = estacion.llamar(cur, a, {"caso_id": c["id"], "telefono_id": c["telefono_id"]})
    o = db.uno(cur, "SELECT datos FROM invoke.evento WHERE llamada_id = %s AND tipo = 'llamada_originada'", (r["llamada_id"],))
    assert o["datos"]["grabar"] is True


@pytest.mark.usefixtures("dsn_base")
def test_ciclo_completo_contra_la_base(cur, esc):
    from services.ari.main import ciclo_grabaciones

    e = esc.estudio()
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    estacion.siguiente_caso(cur, a)
    ll = estacion.llamar(cur, a, {"caso_id": c["id"], "telefono_id": c["telefono_id"]})["llamada_id"]
    db.evento(cur, "grabacion", llamada_id=ll, caso_id=c["id"], datos={"archivo": f"llamada-{ll}.wav"})
    assert db.uno(cur, "SELECT count(*) AS n FROM invoke.grabacion_pendiente WHERE llamada_id = %s", (ll,))["n"] == 1
    pbx, bucket = PbxDoble({f"llamada-{ll}": b"RIFF-audio"}), BucketDoble()
    assert ciclo_grabaciones(pbx, bucket)["subidas"] == 1
    g = db.uno(cur, "SELECT * FROM invoke.grabacion_en_bucket WHERE llamada_id = %s", (ll,))
    assert g["uri"].startswith(f"gs://invoke-grabaciones/{e['codigo']}/") and g["bytes"] == 10
    assert db.uno(cur, "SELECT count(*) AS n FROM invoke.grabacion_pendiente")["n"] == 0
    assert pbx.borrados == [f"llamada-{ll}"]
    # Un segundo ciclo no vuelve a subirla.
    assert ciclo_grabaciones(pbx, bucket)["subidas"] == 0
