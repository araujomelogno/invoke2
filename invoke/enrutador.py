"""Enrutador HTTP propio, independiente del framework (ADR-010: Cloud Functions
con un único punto de entrada y ruteo propio, como paneles).

functions/main.py lo adapta a firebase_functions; scripts/servidor_local.py lo
adapta a Starlette para desarrollo. Los handlers reciben una Peticion y
devuelven un dict (JSON) o una Respuesta.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg


class ErrorHttp(Exception):
    def __init__(self, estado: int, mensaje: str):
        super().__init__(mensaje)
        self.estado = estado
        self.mensaje = mensaje


@dataclass
class Usuario:
    uid: str
    email: str
    nombre: str
    roles: list[str]
    participante_id: int | None = None

    def tiene(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


@dataclass
class Peticion:
    metodo: str
    ruta: str
    query: dict[str, str] = field(default_factory=dict)
    cuerpo: bytes = b""
    cabeceras: dict[str, str] = field(default_factory=dict)
    usuario: Usuario | None = None
    params: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        if not self.cuerpo:
            return {}
        try:
            return json.loads(self.cuerpo)
        except ValueError as e:
            raise ErrorHttp(400, f"JSON inválido: {e}") from e

    def cabecera(self, nombre: str) -> str | None:
        nombre = nombre.lower()
        for k, v in self.cabeceras.items():
            if k.lower() == nombre:
                return v
        return None

    def entero(self, nombre: str) -> int:
        try:
            return int(self.params[nombre])
        except (KeyError, ValueError) as e:
            raise ErrorHttp(400, f"parámetro {nombre} inválido") from e


@dataclass
class Respuesta:
    estado: int = 200
    cuerpo: bytes = b""
    tipo: str = "application/json"
    cabeceras: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, datos: Any, estado: int = 200) -> "Respuesta":
        return cls(estado, json.dumps(datos, ensure_ascii=False, default=_serializar).encode("utf-8"))


def _serializar(v: Any) -> Any:
    import datetime
    import decimal
    import uuid

    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (set, tuple)):
        return list(v)
    raise TypeError(f"no serializable: {type(v)}")


@dataclass
class _Ruta:
    metodo: str
    patron: re.Pattern
    roles: tuple[str, ...]
    handler: Callable[[Peticion], Any]
    publica: bool


class Enrutador:
    def __init__(self) -> None:
        self.rutas: list[_Ruta] = []
        self.autenticar: Callable[[Peticion], Usuario] | None = None

    def ruta(self, metodo: str, plantilla: str, *roles: str, publica: bool = False):
        regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", plantilla) + "$"

        def deco(fn):
            self.rutas.append(_Ruta(metodo.upper(), re.compile(regex), roles, fn, publica))
            return fn

        return deco

    def despachar(self, p: Peticion) -> Respuesta:
        ruta = p.ruta.rstrip("/") or "/"
        coincide_ruta = False
        for r in self.rutas:
            m = r.patron.match(ruta)
            if not m:
                continue
            coincide_ruta = True
            if r.metodo != p.metodo.upper():
                continue
            p.params = m.groupdict()
            try:
                if not r.publica:
                    if self.autenticar is None:
                        raise ErrorHttp(500, "autenticación no configurada")
                    p.usuario = self.autenticar(p)
                    if r.roles and not p.usuario.tiene(*r.roles):
                        raise ErrorHttp(403, "tu rol no habilita esta acción")
                resultado = r.handler(p)
                if isinstance(resultado, Respuesta):
                    return resultado
                return Respuesta.json(resultado if resultado is not None else {"ok": True})
            except ErrorHttp as e:
                return Respuesta.json({"error": e.mensaje}, e.estado)
            except psycopg.errors.RaiseException as e:
                # Invariante de dominio violada en la base: el mensaje es para el usuario.
                return Respuesta.json({"error": e.diag.message_primary}, 409)
            except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation,
                    psycopg.errors.ForeignKeyViolation, psycopg.errors.NotNullViolation) as e:
                return Respuesta.json({"error": e.diag.message_primary, "restriccion": e.diag.constraint_name}, 422)
        if coincide_ruta:
            return Respuesta.json({"error": "método no permitido"}, 405)
        return Respuesta.json({"error": "no encontrado"}, 404)
