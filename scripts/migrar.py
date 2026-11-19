"""Aplica las migraciones SQL numeradas de db/migrations en orden.

Uso:
    python scripts/migrar.py                # usa DATABASE_URL
    python scripts/migrar.py --verificar    # además corre el verificador de accesos

Cada migración se aplica en su propia transacción y queda registrada con su
checksum en invoke_meta.migracion. Si una migración ya aplicada cambió en disco,
el script falla: las migraciones aplicadas no se editan, se agrega una nueva.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys

import psycopg

RAIZ = pathlib.Path(__file__).resolve().parent.parent
DIR_MIGRACIONES = RAIZ / "db" / "migrations"


def migraciones() -> list[pathlib.Path]:
    return sorted(p for p in DIR_MIGRACIONES.glob("*.sql") if p.name[:4].isdigit())


def aplicar(dsn: str, salida=print) -> list[str]:
    aplicadas: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS invoke_meta")
        conn.execute("REVOKE ALL ON SCHEMA invoke_meta FROM PUBLIC")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS invoke_meta.migracion (
                 nombre text PRIMARY KEY, checksum text NOT NULL,
                 aplicada_en timestamptz NOT NULL DEFAULT now())"""
        )
        previas = dict(conn.execute("SELECT nombre, checksum FROM invoke_meta.migracion").fetchall())
        for ruta in migraciones():
            sql = ruta.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if ruta.name in previas:
                if previas[ruta.name] != checksum:
                    raise SystemExit(
                        f"La migración {ruta.name} ya fue aplicada y cambió en disco. "
                        "No se editan migraciones aplicadas: agregá una nueva."
                    )
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO invoke_meta.migracion (nombre, checksum) VALUES (%s, %s)",
                    (ruta.name, checksum),
                )
            aplicadas.append(ruta.name)
            salida(f"aplicada {ruta.name}")
    return aplicadas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--verificar", action="store_true")
    args = parser.parse_args()
    if not args.dsn:
        sys.exit("Falta DATABASE_URL o --dsn")
    aplicar(args.dsn)
    if args.verificar:
        sys.path.insert(0, str(RAIZ / "scripts"))
        from verificar_accesos import verificar

        problemas = verificar(args.dsn)
        if problemas:
            print("\n".join(problemas))
            sys.exit(1)
        print("accesos verificados")


if __name__ == "__main__":
    main()
