"""Baterías compartidas con el navegador (tests/js/compartidos.test.mjs)."""
import json
import pathlib

import pytest

from invoke.dominio import expresiones as ex
from invoke.dominio.cuestionario import Motor, barajar, validar_definicion

RAIZ = pathlib.Path(__file__).resolve().parent.parent
EXPR = json.loads((RAIZ / "tests/compartidos/expresiones.json").read_text())
CQ = json.loads((RAIZ / "tests/compartidos/cuestionario.json").read_text())
DEF = json.loads((RAIZ / CQ["definicion"]).read_text())


@pytest.mark.parametrize("caso", EXPR["casos"], ids=lambda c: c.get("nota") or json.dumps(c["expr"])[:50])
def test_expresiones(caso):
    ctx = EXPR["contexto"]
    if caso.get("error"):
        with pytest.raises(ex.ErrorExpresion):
            ex.evaluar(caso["expr"], ctx["respuestas"], ctx["ficha"])
    else:
        assert ex.evaluar(caso["expr"], ctx["respuestas"], ctx["ficha"]) == caso["esperado"]


def test_definicion_de_ejemplo_valida():
    assert validar_definicion(DEF) == []


@pytest.mark.parametrize("caso", CQ["casos"], ids=lambda c: c["nombre"])
def test_motor(caso):
    m = Motor(DEF, CQ["ficha"], caso["semilla"])
    res = m.recorrer(caso["respuestas"])
    esp = caso["esperado"]
    assert res["visitadas"] == esp["visitadas"]
    assert res["fin"] == esp["fin"]
    assert res["errores"] == esp["errores"]
    assert m.secuencia == esp["secuencia"]


@pytest.mark.parametrize("b", CQ["barajar"], ids=lambda b: f"{b['semilla']}-{b['clave']}")
def test_barajar(b):
    assert barajar(list(range(10)), b["semilla"], b["clave"]) == b["orden"]


def test_semantica_motor():
    m = Motor(DEF, CQ["ficha"], 7)
    assert m.recorrer({"F1": "2"})["fin"] == 20  # no titular → Filtrada
    assert "1998" not in json.dumps(DEF)


def test_validar_definicion_detecta_saltos_y_duplicados():
    mala = json.loads(json.dumps(DEF))
    mala["bloques"][0]["pantallas"][0]["siguiente"] = [{"ir_a": "NO_EXISTE"}]
    mala["bloques"][2]["pantallas"][0]["preguntas"].append(dict(mala["bloques"][2]["pantallas"][0]["preguntas"][0]))
    errores = validar_definicion(mala)
    assert any("NO_EXISTE" in e for e in errores)
    assert any("duplicada" in e for e in errores)


def test_codigo_reservado_rechazado():
    mala = json.loads(json.dumps(DEF))
    mala["bloques"][0]["pantallas"][0]["preguntas"][0]["opciones"][0]["codigo"] = "1998"
    assert validar_definicion(mala)


def test_definicion_anidada_de_mas_da_error_y_no_revienta():
    """M1: una expresión o un documento anidado de más se rechaza con mensaje, no con un 500."""
    profunda = True
    for _ in range(80):
        profunda = {"op": "not", "args": [profunda]}
    d = json.loads(json.dumps(DEF))
    d["bloques"][0]["pantallas"][0]["mostrar_si"] = profunda
    assert any("anidada" in e for e in validar_definicion(d))
    patologica = {}
    for _ in range(5000):
        patologica = {"op": "not", "args": [patologica]}
    d["bloques"][0]["pantallas"][0]["mostrar_si"] = patologica
    assert validar_definicion(d) == ["el documento está anidado de más (máximo 200 niveles)"]
