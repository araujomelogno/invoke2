"""Traducción de hechos de telefonía a disposiciones de teléfono (diccionario v1).

El escucha de ARI llama a disposicion_por_fin() cuando termina un intento que
nadie clasificó: no atendido, ocupado, fuera de servicio, contestador detectado
o abandonada. Si la llamada se conectó a un encuestador, la disposición la pone
el encuestador al clasificar (4 Contesta, 9 Contestador verificado, 5 No volver
a llamar, ...), no este módulo.

Causas Q.850 que reporta Asterisk en ChannelDestroyed / ChannelHangupRequest.
"""
from __future__ import annotations

OCUPADO = 3
CONTESTA = 4
NO_VOLVER_A_LLAMAR = 5
NO_CONTESTA = 6
FUERA_DE_SERVICIO = 7
DESCONECTADO = 8
CONTESTADOR_VERIFICADO = 9
ABANDONADA = 30
CONTESTADOR_DETECTADO = 31
FALSO_POSITIVO_AMD = 32

# Q.850
_CAUSAS = {
    1: FUERA_DE_SERVICIO,     # unallocated number
    2: FUERA_DE_SERVICIO,     # no route to network
    3: FUERA_DE_SERVICIO,     # no route to destination
    17: OCUPADO,              # user busy
    18: NO_CONTESTA,          # no user responding
    19: NO_CONTESTA,          # no answer
    20: DESCONECTADO,         # subscriber absent
    21: NO_CONTESTA,          # call rejected
    22: FUERA_DE_SERVICIO,    # number changed
    27: DESCONECTADO,         # destination out of order
    28: FUERA_DE_SERVICIO,    # invalid number format
    34: OCUPADO,              # no circuit available (congestión)
    38: DESCONECTADO,         # network out of order
    41: DESCONECTADO,         # temporary failure
    42: OCUPADO,              # switching equipment congestion
}


def disposicion_por_fin(
    *,
    atendida: bool,
    conectada: bool,
    amd: str | None,
    causa: int | None,
    abandonada: bool,
) -> int | None:
    """Disposición automática de un intento. None = la pone el encuestador."""
    if conectada:
        return None
    if abandonada:
        return ABANDONADA
    if atendida:
        if (amd or "").upper() == "MACHINE":
            return CONTESTADOR_DETECTADO
        # Atendió y cortó antes de llegar al encuestador (pacer sin agentes ya cubierto arriba)
        return ABANDONADA
    if causa is None:
        return NO_CONTESTA
    return _CAUSAS.get(int(causa), NO_CONTESTA)
