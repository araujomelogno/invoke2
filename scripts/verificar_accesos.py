"""Verifica que las migraciones no abran accesos de más.

Falla (código 1) si:
  * algún privilegio de tabla o de función en los esquemas invoke/asterisk no
    figura en db/accesos_esperados.txt (o figura y ya no existe),
  * PUBLIC tiene cualquier privilegio en esos esquemas,
  * algún rol puede modificar una tabla de hechos (evento, respuesta, calidad_red)
    o escribir una proyección (caso_estado, participante_estado, lista_negra).

Uso:
    python scripts/verificar_accesos.py --dsn postgresql://...
    python scripts/verificar_accesos.py --dsn ... --regenerar   # reescribe el archivo esperado
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

import psycopg

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ESPERADOS = RAIZ / "db" / "accesos_esperados.txt"
ROLES = ("invoke_app", "invoke_lectura", "asterisk_rt")
ESQUEMAS = ("invoke", "asterisk")

HECHOS = {"evento", "respuesta", "calidad_red"}
PROYECCIONES = {"caso_estado", "participante_estado", "lista_negra"}
ESCRITURA = {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}

SQL_TABLAS = """
SELECT grantee, table_schema, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = ANY(%s) AND (grantee = ANY(%s) OR grantee = 'PUBLIC')
"""

SQL_SECUENCIAS = """
SELECT r.rolname, n.nspname, c.relname, p.privilege_type
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL aclexplode(c.relacl) p
JOIN pg_roles r ON r.oid = p.grantee
WHERE c.relkind = 'S' AND n.nspname = ANY(%s) AND r.rolname = ANY(%s)
"""

SQL_FUNCIONES = """
SELECT CASE WHEN p.grantee = 0 THEN 'PUBLIC' ELSE r.rolname END, n.nspname,
       f.oid::regprocedure::text, p.privilege_type
FROM pg_proc f
JOIN pg_namespace n ON n.oid = f.pronamespace
CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl, acldefault('f', f.proowner))) p
LEFT JOIN pg_roles r ON r.oid = p.grantee
WHERE n.nspname = ANY(%s) AND (p.grantee = 0 OR r.rolname = ANY(%s))
"""


def privilegios(dsn: str) -> set[str]:
    filas: set[str] = set()
    with psycopg.connect(dsn) as conn:
        for sql, clase in ((SQL_TABLAS, "tabla"), (SQL_SECUENCIAS, "secuencia"), (SQL_FUNCIONES, "funcion")):
            for rol, esquema, objeto, priv in conn.execute(sql, (list(ESQUEMAS), list(ROLES))):
                filas.add(f"{clase} {rol} {priv} {esquema}.{objeto}".replace(f"{esquema}.{esquema}.", f"{esquema}."))
    return filas


def verificar(dsn: str) -> list[str]:
    reales = privilegios(dsn)
    problemas: list[str] = []
    for fila in sorted(reales):
        _, rol, priv, objeto = fila.split(" ", 3)
        nombre = objeto.split(".", 1)[1].split("(")[0]
        if rol == "PUBLIC":
            problemas.append(f"PROHIBIDO privilegio a PUBLIC: {fila}")
        if nombre in HECHOS and priv in ESCRITURA - {"INSERT"}:
            problemas.append(f"PROHIBIDO modificar un hecho: {fila}")
        if nombre in PROYECCIONES and priv in ESCRITURA:
            problemas.append(f"PROHIBIDO escribir una proyección: {fila}")
    esperados = set()
    if ESPERADOS.exists():
        esperados = {l.strip() for l in ESPERADOS.read_text().splitlines() if l.strip() and not l.startswith("#")}
    for fila in sorted(reales - esperados):
        problemas.append(f"privilegio NO esperado (agregalo a db/accesos_esperados.txt si es intencional): {fila}")
    for fila in sorted(esperados - reales):
        problemas.append(f"privilegio esperado que no existe: {fila}")
    return problemas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--regenerar", action="store_true")
    args = parser.parse_args()
    if args.regenerar:
        filas = sorted(privilegios(args.dsn))
        ESPERADOS.write_text(
            "# Privilegios esperados. Generado por scripts/verificar_accesos.py --regenerar\n"
            "# y revisado a mano. Un privilegio nuevo se agrega acá en el mismo PR que la migración.\n"
            + "\n".join(filas) + "\n"
        )
        print(f"{len(filas)} privilegios escritos en {ESPERADOS.relative_to(RAIZ)}")
        return
    problemas = verificar(args.dsn)
    if problemas:
        print("\n".join(problemas))
        sys.exit(1)
    print("accesos verificados")


if __name__ == "__main__":
    main()
