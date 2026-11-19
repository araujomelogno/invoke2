"""Motor de cuestionario sin interfaz (ADR-006 y ADR-007).

Ejecuta el JSON del cuestionario: visibilidad, orden aleatorio reproducible,
validaciones por tipo, saltos y sustitución de texto. No sabe nada de pantallas
HTML: el puesto del encuestador (web/js/motor.js, gemelo de este módulo) lo usa
para renderizar, y el servidor lo usa para validar una entrevista antes de
aceptarla como completa. Un agente de IA sería otro consumidor del mismo motor.

La aleatorización es reproducible: la semilla es el id del caso, así que retomar
una entrevista muestra el mismo orden. FNV-1a + mulberry32, idénticos en JS.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import expresiones as ex

ESQUEMA = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "schemas" / "cuestionario.schema.json").read_text(encoding="utf-8")
)

COMPLETA = 3
FUERA_DE_CUOTA = 6


# ───────────────────────── aleatoriedad reproducible ─────────────────────────

def fnv1a(texto: str) -> int:
    h = 0x811C9DC5
    for b in texto.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def mulberry32(semilla: int):
    estado = semilla & 0xFFFFFFFF

    def siguiente() -> float:
        nonlocal estado
        estado = (estado + 0x6D2B79F5) & 0xFFFFFFFF
        t = estado
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return siguiente


def barajar(items: list, semilla: int, clave: str, fijo=lambda x: False) -> list:
    """Fisher-Yates con semilla derivada de (semilla, clave). Los fijos no se mueven."""
    rnd = mulberry32(fnv1a(f"{semilla}:{clave}"))
    movibles = [i for i, x in enumerate(items) if not fijo(x)]
    valores = [items[i] for i in movibles]
    for i in range(len(valores) - 1, 0, -1):
        j = int(rnd() * (i + 1))
        valores[i], valores[j] = valores[j], valores[i]
    resultado = list(items)
    for pos, v in zip(movibles, valores):
        resultado[pos] = v
    return resultado


# ───────────────────────────── validación de esquema ─────────────────────────────

def validar_definicion(definicion: Mapping[str, Any]) -> list[str]:
    """Errores del documento: esquema JSON más reglas que el esquema no expresa."""
    import jsonschema

    errores = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
        for e in jsonschema.Draft202012Validator(ESQUEMA).iter_errors(definicion)
    ]
    if errores:
        return errores
    ids_pantalla: set[str] = set()
    ids_variable: set[str] = set()
    for b in definicion["bloques"]:
        for p in b["pantallas"]:
            if p["id"] in ids_pantalla:
                errores.append(f"pantalla duplicada: {p['id']}")
            ids_pantalla.add(p["id"])
            for q in p["preguntas"]:
                if q["id"] in ids_variable:
                    errores.append(f"pregunta duplicada: {q['id']}")
                ids_variable.add(q["id"])
                codigos = [o["codigo"] for o in q.get("opciones", [])]
                if len(codigos) != len(set(codigos)):
                    errores.append(f"{q['id']}: códigos de opción repetidos")
                if q["tipo"] == "texto" and q.get("patron"):
                    try:
                        re.compile(q["patron"])
                    except re.error as e:
                        errores.append(f"{q['id']}: patrón inválido ({e})")
    for b in definicion["bloques"]:
        for p in b["pantallas"]:
            destinos = [r.get("ir_a") for r in p.get("siguiente", [])]
            destinos += [o.get("ir_a") for q in p["preguntas"] for o in q.get("opciones", [])]
            for d in destinos:
                if d and d not in ids_pantalla:
                    errores.append(f"{p['id']}: salto a pantalla inexistente {d}")
    return errores


# ──────────────────────────────────── motor ────────────────────────────────────

def _vacio(v: Any) -> bool:
    return v is None or v == "" or v == []


_PIPE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)?)\}")
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ENTERO = re.compile(r"^-?\d+$")


@dataclass
class Resultado:
    pantalla: str | None = None
    fin: int | None = None


@dataclass
class Motor:
    definicion: Mapping[str, Any]
    ficha: Mapping[str, Any] = field(default_factory=dict)
    semilla: int = 0

    def __post_init__(self) -> None:
        self._pantallas: dict[str, dict] = {}
        self._bloque_de: dict[str, dict] = {}
        self._secuencia: list[str] = []
        for b in self.definicion["bloques"]:
            pantallas = list(b["pantallas"])
            if b.get("aleatorizar"):
                pantallas = barajar(pantallas, self.semilla, b["id"])
            for p in pantallas:
                self._pantallas[p["id"]] = p
                self._bloque_de[p["id"]] = b
                self._secuencia.append(p["id"])

    # ── orden y visibilidad ──

    @property
    def secuencia(self) -> list[str]:
        return list(self._secuencia)

    def opciones(self, pregunta: Mapping[str, Any], respuestas: Mapping[str, Any]) -> list[dict]:
        ops = list(pregunta.get("opciones", []))
        if pregunta.get("aleatorizar_opciones"):
            ops = barajar(ops, self.semilla, pregunta["id"] + ":opciones", fijo=lambda o: o.get("fija", False))
        return [o for o in ops if self._visible(o, respuestas)]

    def filas(self, pregunta: Mapping[str, Any], respuestas: Mapping[str, Any]) -> list[dict]:
        fs = list(pregunta.get("filas", []))
        if pregunta.get("aleatorizar_filas"):
            fs = barajar(fs, self.semilla, pregunta["id"] + ":filas", fijo=lambda o: o.get("fija", False))
        return [f for f in fs if self._visible(f, respuestas)]

    def _visible(self, nodo: Mapping[str, Any], respuestas: Mapping[str, Any]) -> bool:
        if "mostrar_si" not in nodo:
            return True
        return ex.verdadero(ex.evaluar(nodo["mostrar_si"], respuestas, self.ficha))

    def pantalla_visible(self, pid: str, respuestas: Mapping[str, Any]) -> bool:
        return self._visible(self._bloque_de[pid], respuestas) and self._visible(self._pantallas[pid], respuestas)

    def preguntas(self, pid: str, respuestas: Mapping[str, Any]) -> list[dict]:
        return [q for q in self._pantallas[pid]["preguntas"] if self._visible(q, respuestas)]

    def primera(self, respuestas: Mapping[str, Any] | None = None) -> Resultado:
        return self._desde_indice(0, respuestas or {})

    def _desde_indice(self, i: int, respuestas: Mapping[str, Any]) -> Resultado:
        while i < len(self._secuencia):
            pid = self._secuencia[i]
            if self.pantalla_visible(pid, respuestas):
                return Resultado(pantalla=pid)
            i += 1
        return Resultado(fin=COMPLETA)

    # ── texto ──

    def texto(self, plantilla: str | None, respuestas: Mapping[str, Any]) -> str:
        if not plantilla:
            return ""

        def reemplazo(m: re.Match) -> str:
            clave = m.group(1)
            if clave.startswith("ficha."):
                v = self.ficha.get(clave[6:])
                return "" if v is None else str(v)
            var, _, sufijo = clave.partition(".")
            if sufijo == "texto":
                return self._etiqueta(var, respuestas)
            v = respuestas.get(clave)
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return "" if v is None else str(v)

        return _PIPE.sub(reemplazo, plantilla)

    def _etiqueta(self, var: str, respuestas: Mapping[str, Any]) -> str:
        v = respuestas.get(var)
        for p in self._pantallas.values():
            for q in p["preguntas"]:
                if q["id"] == var:
                    textos = {o["codigo"]: o["texto"] for o in q.get("opciones", [])}
                    vals = v if isinstance(v, list) else ([] if v is None else [v])
                    return ", ".join(textos.get(str(x), str(x)) for x in vals)
        return ""

    # ── fórmulas ──

    def calcular_formulas(self, pid: str, respuestas: dict[str, Any]) -> dict[str, Any]:
        r = dict(respuestas)
        for q in self.preguntas(pid, r):
            if q["tipo"] == "formula":
                r[q["id"]] = ex.evaluar(q["expresion"], r, self.ficha)
        return r

    # ── validación ──

    def validar_pantalla(self, pid: str, respuestas: Mapping[str, Any]) -> dict[str, str]:
        """Errores por variable ('*' para validaciones de pantalla). Vacío = válida."""
        errores: dict[str, str] = {}
        for q in self.preguntas(pid, respuestas):
            error = self._validar_pregunta(q, respuestas)
            if error:
                errores[q["id"]] = error
        if not errores:
            for v in self._pantallas[pid].get("validaciones", []):
                if ex.verdadero(ex.evaluar(v["si"], respuestas, self.ficha)):
                    errores["*"] = v["error"]
                    break
        return errores

    def _validar_pregunta(self, q: Mapping[str, Any], r: Mapping[str, Any]) -> str | None:
        tipo = q["tipo"]
        obligatoria = q.get("obligatoria", True)
        msg = q.get("error")
        if tipo == "formula":
            return None
        if tipo == "grilla":
            celdas = {o["codigo"] for o in self.opciones(q, r)}
            for f in self.filas(q, r):
                var = f"{q['id']}.{f['codigo']}"
                v = r.get(var)
                if _vacio(v):
                    if obligatoria:
                        return msg or f"Falta responder la fila «{f['texto']}»"
                    continue
                if q.get("tipo_celda", "simple") == "simple" and str(v) not in celdas:
                    return msg or f"Respuesta inválida en «{f['texto']}»"
                if q.get("tipo_celda") == "numero" and self._numero_invalido(q, v):
                    return msg or f"Valor fuera de rango en «{f['texto']}»"
            return None

        v = r.get(q["id"])
        if _vacio(v):
            return (msg or "Respuesta obligatoria") if obligatoria else None

        if tipo == "simple":
            ops = {o["codigo"]: o for o in self.opciones(q, r)}
            if isinstance(v, list) or str(v) not in ops:
                return msg or "Opción inválida"
            if ops[str(v)].get("otro") and _vacio(r.get(f"{q['id']}.otro")):
                return "Especificá la opción «otro»"
        elif tipo == "multiple":
            ops = {o["codigo"]: o for o in self.opciones(q, r)}
            if not isinstance(v, list):
                return msg or "Se esperaba una lista de opciones"
            vals = [str(x) for x in v]
            if len(set(vals)) != len(vals) or any(x not in ops for x in vals):
                return msg or "Opción inválida"
            minimo = q.get("min_respuestas", 1 if obligatoria else 0)
            maximo = q.get("max_respuestas", len(ops))
            if not (minimo <= len(vals) <= maximo):
                return msg or f"Elegí entre {minimo} y {maximo} opciones"
            if len(vals) > 1 and any(ops[x].get("exclusiva") for x in vals):
                return "Una opción exclusiva no se combina con otras"
            if any(ops[x].get("otro") for x in vals) and _vacio(r.get(f"{q['id']}.otro")):
                return "Especificá la opción «otro»"
        elif tipo == "texto":
            if not isinstance(v, str):
                return msg or "Se esperaba texto"
            if q.get("largo_maximo") and len(v) > q["largo_maximo"]:
                return msg or f"Máximo {q['largo_maximo']} caracteres"
            if q.get("patron") and not re.fullmatch(q["patron"], v):
                return msg or "Formato inválido"
        elif tipo in ("numero", "codigo"):
            if tipo == "codigo" and not _ENTERO.match(str(v).strip()):
                return msg or "Se esperaba un código entero"
            if self._numero_invalido(q, v):
                return msg or self._rango(q)
        elif tipo == "fecha":
            if not isinstance(v, str) or not _FECHA.match(v):
                return msg or "Fecha inválida (AAAA-MM-DD)"
            try:
                _dt.date.fromisoformat(v)
            except ValueError:
                return msg or "Fecha inválida"
        elif tipo == "hora":
            if not isinstance(v, str) or not _HORA.match(v):
                return msg or "Hora inválida (HH:MM)"
        return None

    @staticmethod
    def _rango(q: Mapping[str, Any]) -> str:
        if "minimo" in q and "maximo" in q:
            return f"Ingresá un valor entre {ex._normalizar(q['minimo'])} y {ex._normalizar(q['maximo'])}"
        return "Valor numérico inválido"

    @staticmethod
    def _numero_invalido(q: Mapping[str, Any], v: Any) -> bool:
        n = ex._numero(v)
        if n is None:
            return True
        if "minimo" in q and n < q["minimo"]:
            return True
        if "maximo" in q and n > q["maximo"]:
            return True
        decimales = q.get("decimales", 0)
        texto = str(v).strip()
        if "." in texto and len(texto.split(".", 1)[1].rstrip("0")) > decimales:
            return True
        return False

    # ── flujo ──

    def siguiente(self, pid: str, respuestas: Mapping[str, Any]) -> Resultado:
        p = self._pantallas[pid]
        for q in self.preguntas(pid, respuestas):
            if q["tipo"] == "simple":
                elegida = respuestas.get(q["id"])
                for o in q.get("opciones", []):
                    if o["codigo"] == str(elegida) and o.get("ir_a"):
                        return Resultado(pantalla=o["ir_a"])
        for regla in p.get("siguiente", []):
            if "si" in regla and not ex.verdadero(ex.evaluar(regla["si"], respuestas, self.ficha)):
                continue
            if "fin" in regla:
                return Resultado(fin=int(regla["fin"]))
            destino = regla["ir_a"]
            if self.pantalla_visible(destino, respuestas):
                return Resultado(pantalla=destino)
            return self._desde_indice(self._secuencia.index(destino) + 1, respuestas)
        return self._desde_indice(self._secuencia.index(pid) + 1, respuestas)

    def recorrer(self, respuestas: Mapping[str, Any]) -> dict[str, Any]:
        """Valida una entrevista entera desde el principio con sus respuestas finales.

        Devuelve {"visitadas": [...], "fin": código | None, "errores": {pantalla: {...}}}.
        Lo usa el servidor antes de aceptar una completa: el navegador ya validó,
        pero el servidor no confía en eso.
        """
        r = dict(respuestas)
        visitadas: list[str] = []
        errores: dict[str, dict[str, str]] = {}
        paso = self.primera(r)
        while paso.pantalla is not None:
            pid = paso.pantalla
            if pid in visitadas:
                errores[pid] = {"*": "el flujo vuelve a una pantalla ya visitada (ciclo)"}
                return {"visitadas": visitadas, "fin": None, "errores": errores}
            visitadas.append(pid)
            r = self.calcular_formulas(pid, r)
            e = self.validar_pantalla(pid, r)
            if e:
                errores[pid] = e
                return {"visitadas": visitadas, "fin": None, "errores": errores}
            paso = self.siguiente(pid, r)
        return {"visitadas": visitadas, "fin": paso.fin, "errores": errores, "respuestas": r}
