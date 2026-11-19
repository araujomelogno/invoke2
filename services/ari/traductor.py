"""Traductor de eventos ARI a hechos de INVOKE 2 y a acciones sobre Asterisk.

ADR-003: ARI trata la llamada como un recurso. Cada llamada saliente se origina
con channelId = llamada_id, así que no hace falta correlacionar a mano por
actionId/uniqueId como el CallManagerImpl legacy. Regla de acoplamiento: el
estado de la llamada se persiste en Postgres desde estos eventos; la aplicación
nunca le pregunta a Asterisk en qué estado está una llamada.

Este módulo no hace E/S: recibe un evento ARI y un "puerto" con las operaciones
de base y de ARI, para poder probarlo con dobles (tests/test_ari.py).

Canales que llegan a la aplicación Stasis "invoke":
    puesto,<participante_id>          pata permanente del navegador (ADR-011)
    cliente,<llamada_id>[,<AMD>]      pata del respondente, ya atendida
    snoop,<participante_supervisor>   escucha del supervisor
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from invoke.dominio import telefonia

ESTADOS_TIMBRE = {"Ringing", "Ring"}


class Puerto(Protocol):
    # Base de datos
    def evento(self, tipo: str, **campos: Any) -> None: ...
    def llamada(self, llamada_id: str) -> dict | None: ...
    def tomar_participante(self, llamada_id: str) -> int | None: ...
    def canal_de(self, participante_id: int) -> str | None: ...
    def tiene_disposicion(self, llamada_id: str) -> bool: ...
    def estado_llamada(self, llamada_id: str) -> dict: ...
    # ARI
    def responder(self, canal: str) -> None: ...
    def puente_de(self, participante_id: int) -> str: ...
    def agregar_a_puente(self, puente: str, canal: str) -> None: ...
    def colgar(self, canal: str) -> None: ...
    def reproducir(self, canal: str, sonido: str) -> None: ...
    def grabar(self, puente: str, nombre: str) -> None: ...


@dataclass
class EstadoLlamada:
    """Caché en memoria de una llamada en curso. La fuente es la base (encargo de
    cierre D2): ante un reinicio, el estado se lee de invoke.llamada."""

    llamada_id: str
    atendida: bool = False
    conectada: bool = False
    abandonada: bool = False
    amd: str | None = None


@dataclass
class Traductor:
    puerto: Puerto
    llamadas: dict[str, EstadoLlamada] = field(default_factory=dict)
    puestos: dict[str, int] = field(default_factory=dict)        # canal → participante
    snoops: dict[str, int] = field(default_factory=dict)         # canal snoop → supervisor

    def registrar_originada(self, llamada_id: str) -> None:
        """La llamada existe desde que se origina, no desde que atienden (encargo D3):
        así el estado Ringing, que llega antes de StasisStart, se reconoce."""
        self.llamadas.setdefault(llamada_id, EstadoLlamada(llamada_id))

    def manejar(self, ev: dict) -> None:
        tipo = ev.get("type")
        canal = (ev.get("channel") or {}).get("id")
        if tipo == "StasisStart":
            self._stasis(ev["args"], canal, ev.get("timestamp"))
        elif tipo == "ChannelStateChange" and (ev.get("channel") or {}).get("state") in ESTADOS_TIMBRE:
            # Si el proceso reinició después de originar, la memoria no la tiene: se pregunta a la base.
            if canal in self.llamadas or (canal not in self.puestos and self.puerto.llamada(canal) is not None):
                self.llamadas.setdefault(canal, EstadoLlamada(canal))
                self.puerto.evento("llamada_timbrando", llamada_id=canal, ocurrido_en=ev.get("timestamp"))
        elif tipo == "ChannelDestroyed":
            self._destruido(canal, ev.get("cause"), ev.get("timestamp"))
        elif tipo == "RecordingFinished":
            nombre = (ev.get("recording") or {}).get("name", "")
            if nombre.startswith("llamada-"):
                info = self.puerto.llamada(nombre[8:]) or {}
                self.puerto.evento("grabacion", llamada_id=nombre[8:], caso_id=info.get("caso_id"),
                                   estudio_id=info.get("estudio_id"),
                                   datos={"archivo": nombre + ".wav", "nombre": nombre}, ocurrido_en=ev.get("timestamp"))

    # ── StasisStart ──

    def _stasis(self, args: list[str], canal: str, t: str | None) -> None:
        if not args:
            self.puerto.colgar(canal)
            return
        rol = args[0]
        if rol == "puesto":
            pid = int(args[1])
            self.puertos_alta(canal, pid, t)
        elif rol == "cliente":
            self._cliente(args[1], canal, args[2] if len(args) > 2 else None, t)
        elif rol == "snoop":
            sup = int(args[1])
            self.snoops[canal] = sup
            self.puerto.agregar_a_puente(self.puerto.puente_de(sup), canal)
        else:
            self.puerto.colgar(canal)

    def puertos_alta(self, canal: str, pid: int, t: str | None) -> None:
        self.puerto.responder(canal)
        self.puestos[canal] = pid
        self.puerto.agregar_a_puente(self.puerto.puente_de(pid), canal)
        self.puerto.evento("audio_conectado", participante_id=pid, datos={"canal": canal}, ocurrido_en=t)

    def _cliente(self, llamada_id: str, canal: str, amd: str | None, t: str | None) -> None:
        info = self.puerto.llamada(llamada_id)
        if info is None:
            # Una llamada que INVOKE no originó: no hay llamadas huérfanas.
            self.puerto.colgar(canal)
            return
        est = self.llamadas.setdefault(llamada_id, EstadoLlamada(llamada_id))
        est.atendida = True
        self.puerto.evento("llamada_atendida", llamada_id=llamada_id, caso_id=info.get("caso_id"), ocurrido_en=t)
        if amd:
            est.amd = amd
            self.puerto.evento("amd_resultado", llamada_id=llamada_id, caso_id=info.get("caso_id"),
                               datos={"resultado": amd}, ocurrido_en=t)
            if amd.upper() == "MACHINE":
                self.puerto.colgar(canal)
                return
        pid = info.get("participante_id")
        if pid is None:
            # Power/predictivo: el caso se asigna recién ahora (camino H del PRD).
            pid = self.puerto.tomar_participante(llamada_id)
            if pid is None:
                # Camino I: nadie disponible. Abandonada: registra, avisa y corta.
                est.abandonada = True
                self.puerto.evento("llamada_abandonada", llamada_id=llamada_id, caso_id=info.get("caso_id"), ocurrido_en=t)
                self.puerto.reproducir(canal, "invoke-disculpas")
                self.puerto.colgar(canal)
                return
        else:
            self.puerto.evento("llamada_conectada", llamada_id=llamada_id, caso_id=info.get("caso_id"),
                               participante_id=pid, ocurrido_en=t)
        est.conectada = True
        puente = self.puerto.puente_de(pid)
        self.puerto.agregar_a_puente(puente, canal)
        if info.get("grabar"):
            self.puerto.grabar(puente, f"llamada-{llamada_id}")

    # ── fin ──

    def _destruido(self, canal: str, causa: int | None, t: str | None) -> None:
        if canal in self.puestos:
            pid = self.puestos.pop(canal)
            self.puerto.evento("audio_desconectado", participante_id=pid, datos={"canal": canal}, ocurrido_en=t)
            return
        if canal in self.snoops:
            self.snoops.pop(canal)
            return
        info = self.puerto.llamada(canal)
        if info is None:
            return
        cache = self.llamadas.pop(canal, EstadoLlamada(canal))
        # Encargo de cierre D2: el estado sale de la base (atendida_en, conectada_en,
        # amd_resultado, llamada_abandonada), no de la memoria, que se pierde al reiniciar.
        base = self.puerto.estado_llamada(canal) or {}
        atendida = bool(base.get("atendida")) or cache.atendida
        conectada = bool(base.get("conectada")) or cache.conectada
        abandonada = bool(base.get("abandonada")) or cache.abandonada
        amd = base.get("amd") or cache.amd
        participante = base.get("participante_id") or info.get("participante_id")
        self.puerto.evento("llamada_finalizada", llamada_id=canal, caso_id=info.get("caso_id"),
                           participante_id=participante, datos={"causa": causa}, ocurrido_en=t)
        if info.get("tipo") == "administrativa" or info.get("caso_id") is None:
            return
        if self.puerto.tiene_disposicion(canal):
            return
        codigo = telefonia.disposicion_por_fin(atendida=atendida, conectada=conectada, amd=amd,
                                               causa=causa, abandonada=abandonada)
        if codigo is not None:
            self.puerto.evento("disposicion_telefono", llamada_id=canal, caso_id=info["caso_id"],
                               datos={"codigo": codigo, "automatica": True, "causa": causa}, ocurrido_en=t)
