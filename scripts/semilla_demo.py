"""Datos de demostración para desarrollo local (NUNCA en producción).

    DATABASE_URL=postgresql://... python scripts/semilla_demo.py

Crea usuarios con uid fijo para el modo de desarrollo (?dev_uid=dev-admin,
dev-sup, dev-enc1...), un estudio activo con el cuestionario de ejemplo, una
guía de 40 casos e intentos simulados para que los reportes tengan datos.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import sys
import uuid

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from invoke import db  # noqa: E402

NOMBRES = ["Ana Pérez", "Luis Gómez", "María Rodríguez", "Jorge Fernández", "Lucía Martínez", "Pablo Sosa", "Carmen Silva",
           "Diego Pereira", "Valentina Núñez", "Martín Castro"]


def main() -> None:
    random.seed(7)
    with db.transaccion() as cur:
        usuarios = [("dev-admin", "Coordinación", ["administrador", "investigador"]), ("dev-sup", "Supervisión", ["supervisor"]),
                    ("dev-enc1", "Florencia N.", ["encuestador"]), ("dev-enc2", "Valeria G.", ["encuestador"]),
                    ("dev-enc3", "Emilia B.", ["encuestador"])]
        for uid, nombre, roles in usuarios:
            cur.execute("""INSERT INTO invoke.usuario (uid, email, nombre, roles) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (uid) DO NOTHING""", (uid, f"{uid}@equipos.test", nombre, roles))
            cur.execute("""INSERT INTO invoke.participante (tipo, usuario_uid, nombre) VALUES ('encuestador_webrtc', %s, %s)
                           ON CONFLICT (usuario_uid) DO NOTHING""", (uid, nombre))
        if db.uno(cur, "SELECT 1 FROM invoke.estudio WHERE codigo = 'DEMO-BCO'"):
            print("la semilla ya estaba cargada")
            return
        e = db.uno(cur, """INSERT INTO invoke.estudio (codigo, nombre, cliente, tipo_marco, objetivo_completas, modo_discado,
                                                     fuera_marco_habilitado, umbral_alerta_derivados)
                           VALUES ('DEMO-BCO', 'Satisfacción clientes alto valor', 'Banco Ejemplo', 'base_cliente', 120, 'preview', true, 0.2)
                           RETURNING *""")
        definicion = json.loads((RAIZ / "schemas/ejemplos/satisfaccion_clientes.json").read_text())
        cur.execute("INSERT INTO invoke.cuestionario (estudio_id, version, definicion, publicado_en) VALUES (%s, 1, %s, now())",
                    (e["id"], db.Jsonb(definicion)))
        cur.execute("UPDATE invoke.estudio SET estado = 'activo' WHERE id = %s", (e["id"],))
        for uid in ("dev-enc1", "dev-enc2", "dev-enc3"):
            cur.execute("INSERT INTO invoke.estudio_usuario VALUES (%s, %s)", (e["id"], uid))
        g = db.uno(cur, "INSERT INTO invoke.guia (estudio_id, nombre, semilla) VALUES (%s, 'Base cliente oct-2026', 20261029) RETURNING id", (e["id"],))
        casos = []
        for i in range(40):
            n = NOMBRES[i % len(NOMBRES)]
            c = db.uno(cur, """INSERT INTO invoke.caso (estudio_id, guia_id, identificador, propiedades, estrato)
                               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                       (e["id"], g["id"], f"CLI-{1000 + i}", db.Jsonb({"nombre": n, "saludo": "tardes", "empresa": "Banco Ejemplo",
                                                                      "segmento": random.choice(["Premium", "Privado"])}),
                        random.choice(["Montevideo", "Interior"])))
            cur.execute("INSERT INTO invoke.telefono (caso_id, estudio_id, numero) VALUES (%s, %s, %s)",
                        (c["id"], e["id"], f"09{random.randint(1000000, 9999999)}"))
            casos.append(c["id"])
        cur.execute("""INSERT INTO invoke.cuota (estudio_id, nombre, tipo, propiedad, valores, objetivo)
                       VALUES (%s, 'Segmento Privado', 'propiedad', 'segmento', '{Privado}', 40)""", (e["id"],))
        cur.execute("""INSERT INTO invoke.cuota (estudio_id, nombre, tipo, pregunta, valores, objetivo)
                       VALUES (%s, 'Usa sucursal', 'pregunta', 'CANALES', '{1}', 60)""", (e["id"],))
        # Intentos simulados para los reportes.
        pids = [r["id"] for r in db.todos(cur, "SELECT id FROM invoke.participante WHERE usuario_uid LIKE 'dev-enc%%' ORDER BY id")]
        for pid in pids:
            db.evento(cur, "sesion_iniciada", estudio_id=e["id"], participante_id=pid, datos={"sesion_id": str(uuid.uuid4())})
        for cid in casos[:24]:
            pid = random.choice(pids)
            ll = str(uuid.uuid4())
            db.evento(cur, "caso_asignado", caso_id=cid, participante_id=pid)
            db.evento(cur, "llamada_originada", caso_id=cid, llamada_id=ll, participante_id=pid,
                      datos={"numero": "099", "modo": "preview", "tipo": "encuesta"})
            r = random.random()
            if r < 0.45:
                db.evento(cur, "disposicion_telefono", caso_id=cid, llamada_id=ll, participante_id=pid, datos={"codigo": 6})
                continue
            db.evento(cur, "llamada_conectada", caso_id=cid, llamada_id=ll, participante_id=pid)
            db.evento(cur, "disposicion_telefono", caso_id=cid, llamada_id=ll, participante_id=pid, datos={"codigo": 4})
            if r < 0.7:
                cur.execute("""INSERT INTO invoke.respuesta (caso_id, cuestionario_id, participante_id, variable, valor)
                               SELECT %s, id, %s, 'CANALES', '["1"]' FROM invoke.cuestionario WHERE estudio_id = %s""", (cid, pid, e["id"]))
                db.evento(cur, "disposicion_encuesta", caso_id=cid, llamada_id=ll, participante_id=pid, datos={"codigo": 3})
            elif r < 0.85:
                db.evento(cur, "disposicion_encuesta", caso_id=cid, llamada_id=ll, participante_id=pid,
                          datos={"codigo": 5, "agenda_desde": "2030-01-01T21:00:00Z", "agenda_hasta": "2030-01-01T22:00:00Z"})
            else:
                db.evento(cur, "disposicion_encuesta", caso_id=cid, llamada_id=ll, participante_id=pid,
                          datos={"codigo": 7, "motivo": "Tenía que salir"})
        print(f"estudio DEMO-BCO ({e['id']}) con {len(casos)} casos")


if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Falta DATABASE_URL")
    main()
