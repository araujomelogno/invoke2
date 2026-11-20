"""Traductor ARI con un puerto doble, y un ciclo del pacer contra la base."""
import pytest

from invoke import db
from services.ari.traductor import Traductor


class Doble:
    """Puerto doble. `base` simula lo que ya está persistido en Postgres (para el reinicio)."""

    def __init__(self, llamadas, disponible=None, base=None):
        self.llamadas = llamadas
        self.disponible = disponible
        self.base = base or {}
        self.eventos, self.acciones, self.dispuestas = [], [], set()

    def estado_llamada(self, i):
        e = dict(self.base.get(i, {}))
        for tipo, c in self.eventos:
            if c.get("llamada_id") != i:
                continue
            if tipo == "llamada_atendida":
                e["atendida"] = True
            elif tipo == "llamada_conectada":
                e["conectada"], e["participante_id"] = True, c.get("participante_id")
            elif tipo == "amd_resultado":
                e["amd"] = c["datos"]["resultado"]
            elif tipo == "llamada_abandonada":
                e["abandonada"] = True
        return e

    def evento(self, tipo, **c):
        self.eventos.append((tipo, c))
        if tipo == "disposicion_telefono":
            self.dispuestas.add(c["llamada_id"])

    def llamada(self, i):
        return self.llamadas.get(i)

    def tomar_participante(self, i):
        # Como invoke.tomar_participante_para: si hay alguien, registra la conexión.
        if self.disponible is not None:
            self.eventos.append(("llamada_conectada", {"llamada_id": i, "participante_id": self.disponible}))
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

    def reproducir(self, c, s, rid):
        self.acciones.append(("reproducir", c, s))
        return True

    def grabar(self, b, n):
        self.acciones.append(("grabar", b, n))

    def continuar_amd(self, c):
        self.acciones.append(("amd", c))


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
    # Se corta cuando termina el mensaje, no al empezarlo.
    assert "llamada_abandonada" in tipos(d) and ("colgar", "L3") not in d.acciones
    t.manejar({"type": "PlaybackFinished", "playback": {"id": "disculpas-L3"}})
    assert ("colgar", "L3") in d.acciones
    t.manejar({"type": "ChannelDestroyed", "channel": {"id": "L3"}, "cause": 16})
    assert d.eventos[-1][1]["datos"]["codigo"] == 30
    t.manejar({"type": "StasisStart", "args": ["cliente", "L4", "MACHINE"], "channel": {"id": "L4"}})
    t.manejar({"type": "ChannelDestroyed", "channel": {"id": "L4"}, "cause": 16})
    assert d.eventos[-1][1]["datos"]["codigo"] == 31


def test_power_pasa_por_amd_y_registra_la_atencion_una_vez():
    """La llamada de power entra a la aplicación como "amd", va al dialplan y vuelve como "cliente"."""
    d = Doble({"L7": {"caso_id": 11, "participante_id": None, "tipo": "encuesta"}}, disponible=3)
    t = Traductor(d)
    t.manejar({"type": "StasisStart", "args": ["amd", "L7"], "channel": {"id": "L7"}})
    assert ("amd", "L7") in d.acciones and tipos(d) == ["llamada_atendida"]
    t.manejar({"type": "StasisStart", "args": ["cliente", "L7", "HUMAN"], "channel": {"id": "L7"}})
    assert tipos(d).count("llamada_atendida") == 1
    assert ("puente", "puesto-3", "L7") in d.acciones


def test_power_conecta():
    d = Doble({"L5": {"caso_id": 11, "participante_id": None, "tipo": "encuesta"}}, disponible=3)
    Traductor(d).manejar({"type": "StasisStart", "args": ["cliente", "L5", "HUMAN"], "channel": {"id": "L5"}})
    assert ("puente", "puesto-3", "L5") in d.acciones and "llamada_abandonada" not in tipos(d)


def test_canal_ajeno_se_corta():
    d = Doble({})
    Traductor(d).manejar({"type": "StasisStart", "args": ["cliente", "X"], "channel": {"id": "X"}})
    assert d.acciones == [("colgar", "X")] and d.eventos == []


def test_reinicio_con_llamada_conectada_no_inventa_un_no_contacto():
    """Encargo D2: el proceso reinicia con la llamada conectada; al cortar, la memoria
    está vacía pero la base sabe que fue atendida y conectada. No hay disposición automática."""
    d = Doble({"L9": {"caso_id": 5, "participante_id": None, "tipo": "encuesta"}},
              base={"L9": {"atendida": True, "conectada": True, "participante_id": 7, "amd": "HUMAN"}})
    t = Traductor(d)                      # recién arrancado: self.llamadas vacío
    t.manejar({"type": "ChannelDestroyed", "channel": {"id": "L9"}, "cause": 16})
    assert tipos(d) == ["llamada_finalizada"]
    assert d.eventos[0][1]["participante_id"] == 7     # el encuestador pasa a clasificar
    assert "disposicion_telefono" not in tipos(d)


def test_reinicio_con_llamada_atendida_por_maquina():
    d = Doble({"L10": {"caso_id": 5, "participante_id": None, "tipo": "encuesta"}},
              base={"L10": {"atendida": True, "amd": "MACHINE"}})
    Traductor(d).manejar({"type": "ChannelDestroyed", "channel": {"id": "L10"}, "cause": 16})
    assert d.eventos[-1][1]["datos"]["codigo"] == 31


def test_timbre_se_emite():
    """Encargo D3: Ringing llega antes de StasisStart; la llamada se registra al originar."""
    d = Doble({"L11": {"caso_id": 5, "participante_id": 7, "tipo": "encuesta"}})
    t = Traductor(d)
    t.registrar_originada("L11")
    t.manejar({"type": "ChannelStateChange", "channel": {"id": "L11", "state": "Ringing"}})
    assert tipos(d) == ["llamada_timbrando"]


def test_timbre_despues_de_reiniciar_y_canales_ajenos():
    d = Doble({"L12": {"caso_id": 5, "participante_id": 7, "tipo": "encuesta"}})
    t = Traductor(d)                      # sin registrar_originada: reinició entre originar y timbrar
    t.manejar({"type": "ChannelStateChange", "channel": {"id": "L12", "state": "Ringing"}})
    t.manejar({"type": "ChannelStateChange", "channel": {"id": "X", "state": "Ringing"}})
    t.manejar({"type": "ChannelStateChange", "channel": {"id": "L12", "state": "Up"}})
    assert tipos(d) == ["llamada_timbrando"]


@pytest.mark.usefixtures("dsn_base")
def test_estado_llamada_desde_la_base(cur, esc):
    """La consulta real del puerto (services/ari/main.py) contra Postgres."""
    from services.ari.main import PuertoReal

    e = esc.estudio(modo_discado="power")
    a = esc.usuario()
    esc.sesion(a, e)
    c = esc.caso(e)
    ll = db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, 1)", (e["id"],))[0]["llamada_id"]
    db.evento(cur, "llamada_atendida", caso_id=c["id"], llamada_id=ll)
    db.evento(cur, "amd_resultado", caso_id=c["id"], llamada_id=ll, datos={"resultado": "HUMAN"})
    cur.execute("SELECT invoke.tomar_participante_para(%s)", (ll,))
    est = PuertoReal(None).estado_llamada(str(ll))
    assert est["atendida"] and est["conectada"] and est["participante_id"] == a.participante_id
    assert est["amd"] == "HUMAN" and not est["abandonada"]


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
