"""Genera tests/compartidos/cuestionario.json: casos del motor con su salida esperada.

La salida la calcula la implementación Python; el test JS (tests/js/motor.test.mjs)
exige la misma salida exacta. Si cambia la semántica a propósito, se regenera con
    python tests/compartidos/generar_motor.py
y el diff del JSON se revisa en el PR.
"""
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
from invoke.dominio.cuestionario import Motor, barajar  # noqa: E402

DEF = json.loads((RAIZ / "schemas/ejemplos/satisfaccion_clientes.json").read_text())
FICHA = {"nombre": "Ana Pérez", "saludo": "tardes", "empresa": "Banco Ejemplo"}

ENTRADAS = [
    {"nombre": "filtrado por no titular", "semilla": 7, "respuestas": {"F1": "2"}},
    {"nombre": "falta edad", "semilla": 7, "respuestas": {"F1": "1"}},
    {"nombre": "edad fuera de rango", "semilla": 7, "respuestas": {"F1": "1", "EDAD": "15"}},
    {"nombre": "no usa web salta a canales", "semilla": 11,
     "respuestas": {"F1": "1", "EDAD": 40, "P_SUC": "2", "P_WEB": "3", "P_APP": "1", "CANALES": ["2"]}},
    {"nombre": "completa con grilla", "semilla": 12345,
     "respuestas": {"F1": "1", "EDAD": "30", "P_SUC": "1", "P_WEB": "1", "P_APP": "2",
                    "CANALES": ["1", "3"], "G.a": "1", "G.b": "2", "COMENT": "ok", "FNAC": "1994-02-28"}},
    {"nombre": "exclusiva combinada", "semilla": 3,
     "respuestas": {"F1": "1", "EDAD": 50, "P_SUC": "1", "P_WEB": "1", "P_APP": "1", "CANALES": ["1", "9"]}},
    {"nombre": "otro sin especificar", "semilla": 3,
     "respuestas": {"F1": "1", "EDAD": 50, "P_SUC": "1", "P_WEB": "1", "P_APP": "1", "CANALES": ["4"]}},
    {"nombre": "validación de pantalla", "semilla": 99,
     "respuestas": {"F1": "1", "EDAD": 20, "P_SUC": "3", "P_WEB": "1", "P_APP": "1", "CANALES": ["1", "2"], "G.a": "1", "G.b": "1"}},
    {"nombre": "fecha imposible", "semilla": 99,
     "respuestas": {"F1": "1", "EDAD": 60, "P_SUC": "3", "P_WEB": "1", "P_APP": "1", "CANALES": ["2"], "FNAC": "2023-02-30"}},
    {"nombre": "patrón de cédula", "semilla": 99,
     "respuestas": {"F1": "1", "EDAD": 60, "P_SUC": "3", "P_WEB": "1", "P_APP": "1", "CANALES": ["2"], "CI": "12ab"}},
    {"nombre": "decimales no admitidos", "semilla": 1, "respuestas": {"F1": "1", "EDAD": "30.5"}},
]


def main():
    casos = []
    for e in ENTRADAS:
        m = Motor(DEF, FICHA, e["semilla"])
        res = m.recorrer(e["respuestas"])
        res.pop("respuestas", None)
        primera = m.primera(e["respuestas"]).pantalla
        casos.append({**e, "esperado": {
            **res,
            "secuencia": m.secuencia,
            "opciones_P_SUC": [o["codigo"] for o in m.opciones(DEF["bloques"][1]["pantallas"][0]["preguntas"][0], {})],
            "texto_canales": m.texto("Sucursal: {P_SUC.texto}; canales {CANALES}; {ficha.nombre}", e["respuestas"]),
            "primera": primera,
        }})
    barajados = [{"semilla": s, "clave": k, "orden": barajar(list(range(10)), s, k)}
                 for s in (0, 1, 42, 2**31 + 5) for k in ("B1", "P1:opciones", "ñandú")]
    salida = {"ficha": FICHA, "definicion": "schemas/ejemplos/satisfaccion_clientes.json",
              "casos": casos, "barajar": barajados}
    (RAIZ / "tests/compartidos/cuestionario.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(casos)} casos")


if __name__ == "__main__":
    main()
