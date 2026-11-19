"""Fórmulas de reporte, versión 1 (docs/Fórmulas de reporte y mapeo AAPOR — INVOKE 2.md).

Este módulo es la lectura literal del documento. Recibe los conteos por símbolo
que produce invoke.conteo_tasas() (SQL, sobre el log de eventos) y devuelve las
tasas con su numerador y denominador. Cada decisión del documento tiene su test
en tests/test_formulas.py.

    TR       = C / (C + I + R + NC + AB + e·UD)
    TC_coop  = C / (C + I + R)
    TCT      = (C + I + R + NE_contactados) / (C + I + R + NC + AB + NE_contactados + e·UD)
    TRECH    = R / (C + I + R + NC + AB + e·UD)
    TAB_rep  = AB / (AB + conectadas)
    TAB_real = (AB + FP_amd) / (AB + conectadas + FP_amd)

Reglas de presentación: un decimal; nunca una tasa sin su denominador; un
denominador menor a 30 se muestra como cifra absoluta, no como porcentaje.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

VERSION = 1
# Decisión 1: Equipos usa e = 1 siempre (variante conservadora).
E_ELEGIBILIDAD = 1
DENOMINADOR_MINIMO = 30


@dataclass(frozen=True)
class Tasa:
    nombre: str
    numerador: int
    denominador: int

    @property
    def valor(self) -> float | None:
        if self.denominador == 0:
            return None
        return self.numerador / self.denominador

    @property
    def presentable_como_porcentaje(self) -> bool:
        return self.denominador >= DENOMINADOR_MINIMO

    def presentar(self) -> str:
        """Texto para un informe. Siempre con su denominador al lado."""
        if self.denominador == 0:
            return f"— (0 casos)"
        if not self.presentable_como_porcentaje:
            return f"{self.numerador} de {self.denominador}"
        pct = Decimal(self.numerador * 100) / Decimal(self.denominador)
        pct = pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{str(pct).replace('.', ',')}% ({self.numerador}/{self.denominador})"

    def como_dict(self) -> dict:
        d = asdict(self)
        d["valor"] = None if self.valor is None else round(self.valor, 6)
        d["texto"] = self.presentar()
        d["porcentaje"] = self.presentable_como_porcentaje
        return d


def _n(conteos: Mapping[str, int], simbolo: str) -> int:
    return int(conteos.get(simbolo, 0) or 0)


def tasas_marco(conteos: Mapping[str, int], e: int = E_ELEGIBILIDAD) -> dict[str, Tasa]:
    """Tasas sobre casos del marco. Los fuera de marco no entran (ADR-008)."""
    if e != 1:
        # El documento fija e = 1; otra variante sería una versión nueva de las fórmulas.
        raise ValueError("las fórmulas v1 solo admiten e = 1")
    C, I, R = _n(conteos, "C"), _n(conteos, "I"), _n(conteos, "R")
    NC, AB, NE, UD = _n(conteos, "NC"), _n(conteos, "AB"), _n(conteos, "NE"), _n(conteos, "UD")
    ne_contactados = NE  # en v1 todo no elegible se determina hablando con alguien
    base = C + I + R + NC + AB + e * UD
    return {
        "respuesta": Tasa("Tasa de respuesta", C, base),
        "cooperacion": Tasa("Tasa de cooperación", C, C + I + R),
        "contacto": Tasa("Tasa de contacto", C + I + R + ne_contactados, base + ne_contactados),
        "rechazo": Tasa("Tasa de rechazo", R, base),
    }


def tasas_abandono(abandonadas: int, conectadas: int, fp_amd: int) -> dict[str, Tasa]:
    """Sobre llamadas, en ventana deslizante. La que controla el discador es la real."""
    return {
        "abandono_reportada": Tasa("Tasa de abandono reportada", abandonadas, abandonadas + conectadas),
        "abandono_real": Tasa("Tasa de abandono real", abandonadas + fp_amd, abandonadas + conectadas + fp_amd),
    }


def productividad(completas: int, horas_sesion: float, horas_llamada: float) -> dict[str, float | None]:
    return {
        "completas_por_hora": None if horas_sesion <= 0 else round(completas / horas_sesion, 2),
        "ocupacion": None if horas_sesion <= 0 else round(horas_llamada / horas_sesion, 4),
    }


def ficha_tecnica(conteo: Mapping, estudio: Mapping, abandono: Mapping | None = None) -> dict:
    """Ficha técnica mínima que acompaña todo informe, generada y no escrita a mano."""
    marco = conteo["marco"]
    tasas = tasas_marco(marco)
    fuera = conteo.get("fuera_marco") or {}
    completas_fuera = sum(int(v.get("completas", 0)) for v in fuera.values())
    completas_total = _n(marco, "C") + completas_fuera
    ficha = {
        "estudio": estudio.get("codigo"),
        "tipo_marco": estudio.get("tipo_marco"),
        "advertencia_comparabilidad": (
            None if estudio.get("tipo_marco") == "probabilistico" else
            "El marco no es una muestra probabilística de población general: sus tasas no son "
            "comparables con las de un estudio de opinión pública."
        ),
        "corte": conteo["corte"],
        "hasta": conteo["hasta"],
        "variante": "Conservadora, e = 1 (AAPOR: toda la elegibilidad desconocida entra al denominador)",
        "tasa_respuesta": tasas["respuesta"].como_dict(),
        "tasas": {k: v.como_dict() for k, v in tasas.items()},
        "tamano_marco": conteo["tamano_marco"],
        "casos_trabajados": conteo["casos_trabajados"],
        "distribucion_disposiciones_finales": conteo["disposiciones_finales"],
        "conteos_por_grupo": marco,
        "completas_entregadas": completas_total,
        "completas_fuera_de_marco": completas_fuera,
        "proporcion_fuera_de_marco": Tasa("Completas de origen fuera de marco", completas_fuera, completas_total).como_dict(),
        "fuera_de_marco_por_origen": fuera,
        "version_diccionario": conteo["version_diccionario"],
        "version_formulas": conteo["version_formulas"],
    }
    if estudio.get("modo_discado") in ("power", "predictivo") and abandono is not None:
        ficha["abandono"] = {k: v.como_dict() for k, v in tasas_abandono(
            abandono["abandonadas"], abandono["conectadas"], abandono["fp_amd"]).items()}
    if conteo["corte"] == "parcial":
        ficha["aviso_corte"] = "Reporte parcial: no se compara con el de cierre, que es la cifra definitiva."
    return ficha
