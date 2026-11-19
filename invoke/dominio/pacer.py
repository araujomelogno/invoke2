"""Cálculo del sobre-discado: power (Fase 2) y lazo de control predictivo (Fase 3).

Lógica pura, sin E/S, para poder testearla con series sintéticas. El servicio
services/pacer la llama cada pocos segundos con lo que lee de la base.

Power: por cada encuestador disponible se originan N llamadas. Si el ajuste
automático está prendido, N se deriva de la tasa de contacto observada en la
franja (N ≈ 1 / tasa_contacto), acotado por N máximo.

Predictivo: el mismo power más un lazo de control sobre la tasa de abandono
REAL en ventana deslizante (30–60 min, nunca acumulado). El ajuste es asimétrico:
baja rápido, sube lento. Con menos de min_agentes disponibles cae a power.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Escalones del lazo: asimetría deliberada (el abandono quema muestra de forma
# irreversible; el tiempo ocioso es un costo recuperable).
BAJADA = 0.25      # proporción de R que se quita si el abandono supera el umbral
SUBIDA = 0.05      # escalón absoluto que se suma si hay agentes ociosos
N_MINIMO = 1.0
MUESTRA_MINIMA_CONTACTO = 30   # intentos mínimos para confiar en la tasa observada
CONEXIONES_MINIMAS_LAZO = 20   # llamadas atendidas mínimas para que el lazo actúe


@dataclass(frozen=True)
class ParametrosEstudio:
    modo: str                    # manual | preview | power | predictivo
    n_inicial: float
    n_maximo: float
    abandono_objetivo: float
    ajuste_automatico: bool
    min_agentes_predictivo: int = 8


@dataclass(frozen=True)
class Observacion:
    agentes_disponibles: int         # con audio conectado y sin caso
    llamadas_en_curso: int           # originadas por el pacer, aún sin atender ni terminar
    tasa_contacto: float | None      # observada en la franja (insumo de N)
    intentos_observados: int
    abandonadas_ventana: int
    conectadas_ventana: int
    fp_amd_ventana: int
    ocio_promedio_s: float           # segundos que los disponibles llevan esperando


@dataclass(frozen=True)
class Decision:
    modo_efectivo: str
    n: float
    a_originar: int
    motivo: str


def tasa_abandono_real(o: Observacion) -> float | None:
    d = o.abandonadas_ventana + o.conectadas_ventana + o.fp_amd_ventana
    return None if d == 0 else (o.abandonadas_ventana + o.fp_amd_ventana) / d


def acotar(n: float, p: ParametrosEstudio) -> float:
    return round(min(max(n, N_MINIMO), p.n_maximo), 2)


def n_power(p: ParametrosEstudio, o: Observacion) -> tuple[float, str]:
    if not p.ajuste_automatico:
        return acotar(p.n_inicial, p), "N fijo (ajuste automático apagado)"
    if o.tasa_contacto is None or o.tasa_contacto <= 0 or o.intentos_observados < MUESTRA_MINIMA_CONTACTO:
        return acotar(p.n_inicial, p), "N inicial (sin muestra suficiente de contacto en la franja)"
    return acotar(1.0 / o.tasa_contacto, p), f"N = 1 / contacto ({o.tasa_contacto:.1%}) en la franja"


def ajustar_predictivo(n_actual: float, p: ParametrosEstudio, o: Observacion) -> tuple[float, str]:
    """Un paso del lazo. Devuelve el nuevo R (ratio de sobre-discado)."""
    tab = tasa_abandono_real(o)
    conexiones = o.abandonadas_ventana + o.conectadas_ventana + o.fp_amd_ventana
    if tab is None or conexiones < CONEXIONES_MINIMAS_LAZO:
        return acotar(n_actual, p), "mantiene R (ventana sin conexiones suficientes)"
    if tab > p.abandono_objetivo:
        return acotar(n_actual * (1 - BAJADA), p), f"baja R: abandono real {tab:.1%} > objetivo {p.abandono_objetivo:.1%}"
    if o.agentes_disponibles > 0 and o.ocio_promedio_s > 15:
        return acotar(n_actual + SUBIDA, p), f"sube R: agentes ociosos {o.ocio_promedio_s:.0f}s, abandono {tab:.1%}"
    return acotar(n_actual, p), "mantiene R"


def decidir(p: ParametrosEstudio, o: Observacion, n_actual: float | None) -> Decision:
    """Cuántas llamadas originar ahora y con qué N."""
    if p.modo not in ("power", "predictivo"):
        return Decision(p.modo, 1.0, 0, "el modo no usa pacer")
    modo = p.modo
    if modo == "predictivo" and o.agentes_disponibles < p.min_agentes_predictivo:
        modo = "power"
        motivo_modo = f"cae a power: {o.agentes_disponibles} disponibles < {p.min_agentes_predictivo}"
    else:
        motivo_modo = ""

    if modo == "power":
        n, motivo = n_power(p, o)
    else:
        n, motivo = ajustar_predictivo(n_actual if n_actual is not None else n_power(p, o)[0], p, o)

    objetivo = math.floor(o.agentes_disponibles * n + 1e-9)
    a_originar = max(0, objetivo - o.llamadas_en_curso)
    return Decision(modo, n, a_originar, "; ".join(x for x in (motivo_modo, motivo) if x))
