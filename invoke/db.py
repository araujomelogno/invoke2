"""Acceso a Postgres (ADR-001): psycopg 3 con pool.

En Cloud Run y Cloud Functions DATABASE_URL apunta al socket del conector de
Cloud SQL (host=/cloudsql/PROYECTO:REGION:INSTANCIA). Toda conexión trabaja
con el rol invoke_app, que es el que tiene los privilegios verificados por
scripts/verificar_accesos.py.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import threading
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import config

_pool = None
_lock = threading.Lock()


def _configurar(conn: psycopg.Connection) -> None:
    rol = config().rol_db
    if rol:
        conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(rol)))
    conn.execute("SET TIME ZONE 'UTC'")
    conn.commit()


def pool():
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool

                # Encargo M3: tamaño y espera explícitos. Una petición que no consigue
                # conexión en INVOKE_DB_ESPERA_S segundos falla con PoolTimeout, que la API
                # responde como 503, en lugar de quedar encolada sin señal.
                _pool = ConnectionPool(
                    config().database_url, min_size=1, max_size=int(os.environ.get("INVOKE_DB_POOL", "10")),
                    timeout=float(os.environ.get("INVOKE_DB_ESPERA_S", "8")), configure=_configurar,
                    kwargs={"row_factory": dict_row}, open=True,
                )
                atexit.register(_pool.close)
    return _pool


def usar_conexion(conn: psycopg.Connection | None) -> None:
    """Para tests: fija una conexión única en lugar del pool."""
    global _pool
    _pool = _PoolFijo(conn) if conn is not None else None


class _PoolFijo:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.contextmanager
    def connection(self):
        yield self.conn


@contextlib.contextmanager
def transaccion() -> Iterator[psycopg.Cursor]:
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                yield cur


def uno(cur: psycopg.Cursor, sql: str, params: Any = None) -> dict | None:
    cur.execute(sql, params)
    return cur.fetchone()


def todos(cur: psycopg.Cursor, sql: str, params: Any = None) -> list[dict]:
    cur.execute(sql, params)
    return cur.fetchall()


def evento(cur: psycopg.Cursor, tipo: str, *, estudio_id=None, caso_id=None, llamada_id=None,
           participante_id=None, autor=None, datos: dict | None = None, ocurrido_en=None) -> int:
    """Registra un hecho en el log inmutable. Única forma de cambiar el estado operativo."""
    cur.execute(
        """INSERT INTO invoke.evento (tipo, estudio_id, caso_id, llamada_id, participante_id, autor, datos, ocurrido_en)
           VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, clock_timestamp())) RETURNING id""",
        (tipo, estudio_id, caso_id, llamada_id, participante_id, autor, Jsonb(datos or {}), ocurrido_en),
    )
    return cur.fetchone()["id"]


__all__ = ["Jsonb", "evento", "pool", "todos", "transaccion", "uno", "usar_conexion"]
