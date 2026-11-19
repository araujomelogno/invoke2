"""Fixtures de prueba.

Los tests de base corren contra un Postgres real (INVOKE_TEST_DSN, por ejemplo
postgresql://postgres@127.0.0.1:5432/postgres). Se crea una base nueva por
corrida, se aplican las migraciones y cada test trabaja como invoke_app dentro
de una transacción que se descarta al final. Sin INVOKE_TEST_DSN, esos tests
se saltean y corren solo los de lógica pura.
"""
from __future__ import annotations

import os
import pathlib
import sys
import uuid

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

DSN_ADMIN = os.environ.get("INVOKE_TEST_DSN")


@pytest.fixture(scope="session")
def dsn_base():
    if not DSN_ADMIN:
        pytest.skip("INVOKE_TEST_DSN no definido: se saltean los tests de base")
    import psycopg

    nombre = f"invoke_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN_ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{nombre}"')
    dsn = DSN_ADMIN.rsplit("/", 1)[0] + "/" + nombre
    from migrar import aplicar

    aplicar(dsn, salida=lambda *_: None)
    yield dsn
    with psycopg.connect(DSN_ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')


@pytest.fixture
def conn(dsn_base):
    """Conexión como invoke_app dentro de una transacción que se descarta."""
    import psycopg
    from psycopg.rows import dict_row

    from invoke import db

    c = psycopg.connect(dsn_base, row_factory=dict_row)
    c.execute("SET ROLE invoke_app")
    tx = c.transaction()
    tx.__enter__()
    db.usar_conexion(c)
    try:
        yield c
    finally:
        db.usar_conexion(None)
        try:
            tx.__exit__(psycopg.Rollback, psycopg.Rollback(), None)
        finally:
            c.close()


@pytest.fixture
def cur(conn):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as k:
        yield k


class Escenario:
    """Arma estudios, usuarios y casos con lo mínimo, como lo haría la administración."""

    def __init__(self, cur):
        self.cur = cur
        self.n = 0

    def usuario(self, nombre="Enc", roles=("encuestador",)):
        from invoke.enrutador import Usuario

        self.n += 1
        uid = f"uid-{uuid.uuid4().hex[:10]}"
        self.cur.execute("INSERT INTO invoke.usuario (uid, email, nombre, roles) VALUES (%s, %s, %s, %s)",
                         (uid, f"{uid}@equipos.test", f"{nombre} {self.n}", list(roles)))
        self.cur.execute("INSERT INTO invoke.participante (tipo, usuario_uid, nombre) VALUES ('encuestador_webrtc', %s, %s) RETURNING id",
                         (uid, nombre))
        pid = self.cur.fetchone()["id"]
        return Usuario(uid, f"{uid}@equipos.test", f"{nombre} {self.n}", list(roles), pid)

    def estudio(self, cuestionario=None, franjas=(("todo", "00:00", "23:59:59"),), **params):
        from invoke import db
        from tests.datos import CUESTIONARIO_MINIMO

        codigo = params.pop("codigo", f"E{uuid.uuid4().hex[:6]}")
        cols = {"codigo": codigo, "nombre": f"Estudio {codigo}", **params}
        e = db.uno(self.cur, f"INSERT INTO invoke.estudio ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",
                   tuple(cols.values()))
        self.cur.execute("DELETE FROM invoke.franja WHERE estudio_id = %s", (e["id"],))
        for nombre, desde, hasta in franjas:
            self.cur.execute("INSERT INTO invoke.franja VALUES (%s, %s, %s, %s)", (e["id"], nombre, desde, hasta))
        self.cur.execute("""INSERT INTO invoke.cuestionario (estudio_id, version, definicion, publicado_en)
                            VALUES (%s, 1, %s, now())""", (e["id"], db.Jsonb(cuestionario or CUESTIONARIO_MINIMO)))
        self.cur.execute("UPDATE invoke.estudio SET estado = 'activo' WHERE id = %s", (e["id"],))
        e["guia_id"] = db.uno(self.cur, "INSERT INTO invoke.guia (estudio_id, nombre, semilla) VALUES (%s, 'base', 7) RETURNING id",
                              (e["id"],))["id"]
        return e

    def caso(self, estudio, telefono=None, **campos):
        from invoke import db

        self.n += 1
        props = campos.pop("propiedades", {})
        c = db.uno(self.cur, f"""INSERT INTO invoke.caso (estudio_id, guia_id, propiedades{''.join(', ' + k for k in campos)})
                                 VALUES (%s, %s, %s{', %s' * len(campos)}) RETURNING *""",
                   (estudio["id"], campos.pop("guia_id", None) or estudio["guia_id"], db.Jsonb(props), *campos.values()))
        tel = telefono or f"09{self.n:07d}"
        c["telefono_id"] = db.uno(self.cur, "INSERT INTO invoke.telefono (caso_id, estudio_id, numero) VALUES (%s, %s, %s) RETURNING id",
                                  (c["id"], estudio["id"], tel))["id"]
        c["numero"] = tel
        return c

    def sesion(self, usuario, estudio, audio=True):
        from invoke import db
        from invoke.servicios import estacion

        estacion.cerrar_sesion(self.cur, usuario) if self.estado(usuario)["estado"] != "desconectado" else None
        db.evento(self.cur, "sesion_iniciada", estudio_id=estudio["id"], participante_id=usuario.participante_id,
                  datos={"sesion_id": str(uuid.uuid4())})
        if audio:
            db.evento(self.cur, "audio_conectado", participante_id=usuario.participante_id, datos={"canal": f"canal-{usuario.participante_id}"})
        db.evento(self.cur, "estado_participante", estudio_id=estudio["id"], participante_id=usuario.participante_id,
                  datos={"estado": "disponible"})

    def estado(self, usuario):
        from invoke import db

        return db.uno(self.cur, "SELECT * FROM invoke.participante_estado WHERE participante_id = %s",
                      (usuario.participante_id,)) or {"estado": "desconectado"}

    def caso_estado(self, caso_id):
        from invoke import db

        return db.uno(self.cur, "SELECT * FROM invoke.caso_estado WHERE caso_id = %s", (caso_id,))

    def intento(self, usuario, caso, disp_tel, disp_enc=None, motivo=None, agenda=None, conectar=None):
        """Un intento completo desde el puesto: llamar, (conectar), finalizar."""
        from invoke import db
        from invoke.servicios import estacion

        tel = caso.get("telefono_id") or caso["telefonos"][0]["id"]
        r = estacion.llamar(self.cur, usuario, {"caso_id": caso["id"], "telefono_id": tel})
        if conectar or disp_tel == 4:
            db.evento(self.cur, "llamada_conectada", caso_id=caso["id"], llamada_id=r["llamada_id"],
                      participante_id=usuario.participante_id)
        db.evento(self.cur, "llamada_finalizada", caso_id=caso["id"], llamada_id=r["llamada_id"],
                  participante_id=usuario.participante_id, datos={"causa": 16})
        datos = {"caso_id": caso["id"], "llamada_id": r["llamada_id"], "disposicion_telefono": disp_tel}
        if disp_enc is not None:
            datos["disposicion_encuesta"] = disp_enc
        if motivo:
            datos["motivo"] = motivo
        if agenda:
            datos["agenda"] = agenda
        estacion.finalizar(self.cur, usuario, datos)
        return r["llamada_id"]


@pytest.fixture
def esc(cur):
    return Escenario(cur)
