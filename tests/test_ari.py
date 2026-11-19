"""Traductor ARI con un puerto doble, y un ciclo del pacer contra la base."""
import pytest

from invoke import db
from services.ari.traductor import Traductor


class Doble:
    def __init__(self, llamadas, disponible=None):
        self.llamadas = llamadas
        self.disponible = disponible
        self.eventos, self.acciones, self.dispuestas = [], [], set()

    def evento(self, tipo, **c):
        self.eventos.append((tipo, c))
        if tipo == "disposicion_telefono":
            self.dispuestas.add(c["llamada_id"])

    def llamada(self, i):
        return self.llamadas.get(i)

    def tomar_participante(self, i):
        return self.disponible

    def canal_de(self, p):
        return f"canal-{p}"

    def tiene_disposicion(self, i):
        return i in self.dispuestas

    def responder(self, c):
        self.acciones.append(("responder", c))

    def puente_de(self, p):
        return f"puesto-{p}"

    def agregar_a_puente(self, b, c):
        self.acciones.append(("puente", b, c))

    def colgar(self, c):
        self.acciones.append(("colgar", c))

    def reproducir(self, c, s):
        self.acciones.append(("reproducir", c, s))

    def grabar(self, b, n):
        self.acciones.append(("grabar", b, n))


def tipos(d):
    return [t for t, _ in d.eventos]


def test_puesto_y_preview():
    d = Doble({"L1": {"caso_id": 5, "participante_id": 7, "grabar": True, "tipo": "encuesta"}})
    t = Traductor(d)
    t.manejar({"type": "StasisStart", "args": ["puesto", "7"], "channel": {"id": "canal-7"}})
    t.manejar({"type": "StasisStart", "args": ["cliente", "L1"], "channel": {"id": "L1"}})
    assert tipos(d) == ["audio_conectado", "llamada_atendida", "llamada_conectada"]
    assert ("puente", "puesto-7", "L1") in d.acciones and ("grabar", "puesto-7", "llamada-L1") in d.acciones
    t.manejar({"type": "ChannelDestroyed", "channel": {"id": "L1"}, "cause": 16})
    assert tipos(d)[-1] == "llamada_finalizada"           # conectada: clasifica el encuestador


def test_no_contesta_automatica():
    d = Doble({"L2": {"caso_id": 5, "participante_id": 7, "tipo": "encuesta"}})
    Traductor(d).manejar({"type": "ChannelDestroyed", "channel": {"id": "L2"}, "cause": 19})
    assert d.eventos[-1] == ("disposicion_telefono", {"llamada_id": "L2", "caso_id": 5, "ocurrido_en": None,
                                                      "datos": {"codigo": 6, "automatica": True, "causa": 19}})


def test_power_abandonada_y_maquina():
    d = Doble({"L3": {"caso_id": 9, "participante_id": None, "tipo": "encuesta"},
               "L4": {"caso_id": 10, "participante_id": None, "tipo": "encuesta"}}, disponible=None)
    t = Traductor(d)
    t.manejar({"type": "StasisStart", "args": ["cliente", "L3", "HUMAN"], "channel": {"id": "L3"}})
    assert "llamada_abandonada" in tipos(d) and ("colgar", "L3") in d.acciones
    t.manejar({"type": "ChannelDestroyed", "channel": {"id": "L3"}, "cause": 16})
    assert d.eventos[-1][1]["datos"]["codigo"] == 30
    t.manejar({"type": "StasisStart", "args": ["cliente", "L4", "MACHINE"], "channel": {"id": "L4"}})
    t.manejar({"type": "ChannelDestroyed", "channel": {"id": "L4"}, "cause": 16})
    assert d.eventos[-1][1]["datos"]["codigo"] == 31


def test_power_conecta():
    d = Doble({"L5": {"caso_id": 11, "participante_id": None, "tipo": "encuesta"}}, disponible=3)
    Traductor(d).manejar({"type": "StasisStart", "args": ["cliente", "L5", "HUMAN"], "channel": {"id": "L5"}})
    assert ("puente", "puesto-3", "L5") in d.acciones and "llamada_abandonada" not in tipos(d)


def test_canal_ajeno_se_corta():
    d = Doble({})
    Traductor(d).manejar({"type": "StasisStart", "args": ["cliente", "X"], "channel": {"id": "X"}})
    assert d.acciones == [("colgar", "X")] and d.eventos == []


@pytest.mark.usefixtures("dsn_base")
def test_ciclo_pacer(cur, esc):
    from services.pacer import main as pacer

    e = esc.estudio(modo_discado="power", n_inicial=2, ajuste_automatico=False)
    a, b = esc.usuario(), esc.usuario()
    esc.sesion(a, e)
    esc.sesion(b, e)
    for _ in range(6):
        esc.caso(e)
    est = db.uno(cur, "SELECT * FROM invoke.estudio WHERE id = %s", (e["id"],))
    d = pacer.ciclo_estudio(est)
    assert d.a_originar == 4
    assert db.uno(cur, "SELECT count(*) AS n FROM invoke.comando_telefonia WHERE autor = 'pacer' AND tipo = 'originar'")["n"] == 4
    # Con 4 en curso y los mismos 2 agentes, el próximo ciclo no origina más.
    assert pacer.ciclo_estudio(est).a_originar == 0
