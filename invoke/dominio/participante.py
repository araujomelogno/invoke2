"""Frontera de participante (ADR-007).

El núcleo no modela "encuestador humano en un navegador": modela un
participante que recibe un caso, recorre el cuestionario y emite respuestas y
una disposición. El asignador, el pacer y el motor de cuestionario hablan con
esta interfaz; nunca con el navegador.

Hoy existe una sola implementación real (EncuestadorWebRTC). AgenteIA está
declarado para no cerrar la puerta de la Fase 4: no se construye, pero el tipo
existe en la base (invoke.participante.tipo = 'agente_ia') y el asignador ya
discrimina por tipo de participante.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class CasoAsignado:
    caso_id: int
    estudio_id: int
    ficha: Mapping[str, Any]
    cuestionario: Mapping[str, Any]
    llamada_id: str | None


@dataclass(frozen=True)
class ResultadoEntrevista:
    respuestas: Mapping[str, Any]
    disposicion_telefono: int
    disposicion_encuesta: int | None
    motivo: str | None = None
    agenda: Mapping[str, Any] | None = None


@runtime_checkable
class Participante(Protocol):
    """Lo que el núcleo espera de cualquier participante."""

    tipo: str
    participante_id: int

    def puede_recibir(self) -> bool:
        """¿Está disponible y con el canal de audio listo?"""
        ...

    def entregar(self, caso: CasoAsignado) -> None:
        """Le pone el caso enfrente (pantalla, o contexto de conversación)."""
        ...

    def conectar_audio(self, canal_cliente: str) -> None:
        """Puentea la pata del respondente con la del participante (ARI)."""
        ...


@dataclass
class EncuestadorWebRTC:
    """Encuestador humano: el caso viaja por WebSocket a la sala y el audio por
    la pata permanente SIP.js ↔ Asterisk (ver ADR-011 en docs/decisiones.html)."""

    participante_id: int
    estado: str = "desconectado"
    canal_audio: str | None = None
    tipo: str = "encuestador_webrtc"

    def puede_recibir(self) -> bool:
        return self.estado == "disponible" and self.canal_audio is not None

    def entregar(self, caso: CasoAsignado) -> None:  # lo hace el gateway de sala
        raise NotImplementedError("la entrega la hace services/sala vía NOTIFY")

    def conectar_audio(self, canal_cliente: str) -> None:  # lo hace services/ari
        raise NotImplementedError("el puente lo arma services/ari")


TIPOS = ("encuestador_webrtc", "agente_ia")
