"""Evaluador del lenguaje de expresiones del cuestionario (PRD, "Frontend y expresiones").

La lógica de saltos se evalúa dos veces: en el navegador para resolver en vivo
(web/js/expresiones.js) y en el servidor para validar. Para que las dos
implementaciones no diverjan, el lenguaje es un AST restringido en JSON, no
JavaScript arbitrario, y una batería de casos compartida
(tests/compartidos/expresiones.json) corre contra ambas.

Gramática:
    expr := null | true | false | número | texto
          | {"var": "P1"}               respuesta vigente (None si no hay)
          | {"ficha": "nombre"}         propiedad de la ficha del contacto
          | {"op": OP, "args": [expr, ...]}

Operadores y semántica (idéntica en JS):
    ==  !=           igualdad; número y texto numérico se comparan como número
    <  <=  >  >=     solo números; con null o no numérico devuelven false
    and  or  not     cortocircuito, devuelven booleano
    +  -  *  /       aritmética; null si algún operando es null o no numérico, o si /0
    in               args[0] está en la lista args[1:] (con la igualdad de ==)
    contiene         respuesta múltiple args[0] (lista) contiene args[1]
    vacio            null, "", [] → true
    cantidad         largo de una lista (0 si null)
    si               si(cond, a, b)
    min  max         sobre números, ignorando null
    redondear        redondear(x, decimales) mitad lejos de cero
    concat           texto
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Mapping

MAX_NODOS = 500
# Solo decimales simples: ni "0x10" ni "1_000" ni "inf", para coincidir con JS.
_NUMERICO = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class ErrorExpresion(ValueError):
    pass


def _numero(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        t = v.strip()
        if not _NUMERICO.match(t):
            return None
        n = float(t)
        return n if math.isfinite(n) else None
    return None


def _igual(a: Any, b: Any) -> bool:
    na, nb = _numero(a), _numero(b)
    if na is not None and nb is not None:
        return na == nb
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(
            _igual(x, y) for x, y in zip(a, b)
        )
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return a == b


def verdadero(v: Any) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0
    if isinstance(v, (str, list)):
        return len(v) > 0
    return bool(v)


def _normalizar(n: float | None) -> Any:
    """Enteros como int, para que JSON y JS coincidan (3.0 → 3)."""
    if n is None or not math.isfinite(n):
        return None
    return int(n) if n == int(n) and abs(n) < 2**53 else n


def _redondear(x: float, decimales: int) -> float:
    f = 10 ** decimales
    return math.copysign(math.floor(abs(x) * f + 0.5) / f, x)


def evaluar(expr: Any, respuestas: Mapping[str, Any] | None = None, ficha: Mapping[str, Any] | None = None) -> Any:
    respuestas = respuestas or {}
    ficha = ficha or {}
    contador = [0]

    def ev(e: Any) -> Any:
        contador[0] += 1
        if contador[0] > MAX_NODOS:
            raise ErrorExpresion("expresión demasiado grande")
        if e is None or isinstance(e, (bool, int, float, str)):
            return e
        if isinstance(e, list):
            raise ErrorExpresion("las listas solo van como argumentos de un operador")
        if not isinstance(e, dict):
            raise ErrorExpresion(f"nodo inválido: {e!r}")
        if "var" in e:
            return respuestas.get(e["var"])
        if "ficha" in e:
            return ficha.get(e["ficha"])
        op = e.get("op")
        args = e.get("args", [])
        if not isinstance(args, list):
            raise ErrorExpresion("args debe ser una lista")
        fn = _OPERADORES.get(op)
        if fn is None:
            raise ErrorExpresion(f"operador desconocido: {op!r}")
        return fn(ev, args)

    return ev(expr)


def _aridad(args: list, n: int, op: str) -> None:
    if len(args) != n:
        raise ErrorExpresion(f"{op} espera {n} argumentos")


def _cmp(fn: Callable[[float, float], bool], op: str):
    def f(ev, args):
        _aridad(args, 2, op)
        a, b = _numero(ev(args[0])), _numero(ev(args[1]))
        return a is not None and b is not None and fn(a, b)

    return f


def _arit(fn: Callable[[float, float], float | None], op: str):
    def f(ev, args):
        _aridad(args, 2, op)
        a, b = _numero(ev(args[0])), _numero(ev(args[1]))
        if a is None or b is None:
            return None
        return _normalizar(fn(a, b))

    return f


def _y(ev, args):
    return all(verdadero(ev(a)) for a in args)


def _o(ev, args):
    return any(verdadero(ev(a)) for a in args)


def _no(ev, args):
    _aridad(args, 1, "not")
    return not verdadero(ev(args[0]))


def _en(ev, args):
    if len(args) < 2:
        raise ErrorExpresion("in espera al menos 2 argumentos")
    v = ev(args[0])
    return any(_igual(v, ev(x)) for x in args[1:])


def _contiene(ev, args):
    _aridad(args, 2, "contiene")
    lista, v = ev(args[0]), ev(args[1])
    if not isinstance(lista, list):
        return lista is not None and _igual(lista, v)
    return any(_igual(x, v) for x in lista)


def _vacio(ev, args):
    _aridad(args, 1, "vacio")
    v = ev(args[0])
    return v is None or v == "" or v == []


def _cantidad(ev, args):
    _aridad(args, 1, "cantidad")
    v = ev(args[0])
    if v is None:
        return 0
    return len(v) if isinstance(v, list) else 1


def _si(ev, args):
    _aridad(args, 3, "si")
    return ev(args[1]) if verdadero(ev(args[0])) else ev(args[2])


def _extremo(fn):
    def f(ev, args):
        nums = [n for n in (_numero(ev(a)) for a in args) if n is not None]
        return _normalizar(fn(nums)) if nums else None

    return f


def _redondear_op(ev, args):
    if len(args) not in (1, 2):
        raise ErrorExpresion("redondear espera 1 o 2 argumentos")
    x = _numero(ev(args[0]))
    d = _numero(ev(args[1])) if len(args) == 2 else 0
    if x is None or d is None:
        return None
    return _normalizar(_redondear(x, int(d)))


def _texto(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(_normalizar(v))
    if isinstance(v, list):
        return ",".join(_texto(x) for x in v)
    return str(v)


def _concat(ev, args):
    return "".join(_texto(ev(a)) for a in args)


_OPERADORES: dict[str, Callable] = {
    "==": lambda ev, a: (_aridad(a, 2, "==") or _igual(ev(a[0]), ev(a[1]))),
    "!=": lambda ev, a: (_aridad(a, 2, "!=") or not _igual(ev(a[0]), ev(a[1]))),
    "<": _cmp(lambda x, y: x < y, "<"),
    "<=": _cmp(lambda x, y: x <= y, "<="),
    ">": _cmp(lambda x, y: x > y, ">"),
    ">=": _cmp(lambda x, y: x >= y, ">="),
    "and": _y,
    "or": _o,
    "not": _no,
    "+": _arit(lambda x, y: x + y, "+"),
    "-": _arit(lambda x, y: x - y, "-"),
    "*": _arit(lambda x, y: x * y, "*"),
    "/": _arit(lambda x, y: None if y == 0 else x / y, "/"),
    "in": _en,
    "contiene": _contiene,
    "vacio": _vacio,
    "cantidad": _cantidad,
    "si": _si,
    "min": _extremo(min),
    "max": _extremo(max),
    "redondear": _redondear_op,
    "concat": _concat,
}

OPERADORES = tuple(sorted(_OPERADORES))


def variables(expr: Any) -> set[str]:
    """Variables de respuesta que lee una expresión (para validar el cuestionario)."""
    encontradas: set[str] = set()

    def rec(e: Any) -> None:
        if isinstance(e, dict):
            if "var" in e:
                encontradas.add(e["var"])
            for a in e.get("args", []) or []:
                rec(a)

    rec(expr)
    return encontradas


def validar(expr: Any) -> list[str]:
    """Errores estructurales de una expresión, sin evaluarla."""
    errores: list[str] = []

    def rec(e: Any, ruta: str) -> None:
        if e is None or isinstance(e, (bool, int, float, str)):
            return
        if not isinstance(e, dict):
            errores.append(f"{ruta}: nodo inválido")
            return
        if "var" in e or "ficha" in e:
            clave = "var" if "var" in e else "ficha"
            if not isinstance(e[clave], str) or len(e) != 1:
                errores.append(f"{ruta}: {clave} debe ser un texto y único campo")
            return
        if e.get("op") not in _OPERADORES:
            errores.append(f"{ruta}: operador desconocido {e.get('op')!r}")
        if not isinstance(e.get("args", []), list):
            errores.append(f"{ruta}: args debe ser lista")
            return
        for i, a in enumerate(e.get("args", [])):
            rec(a, f"{ruta}.args[{i}]")

    rec(expr, "$")
    return errores
