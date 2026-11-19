"""Lazo del discador (Fases 2 y 3) y traducción de telefonía. Lógica pura."""
from invoke.dominio import pacer as pc
from invoke.dominio import telefonia as tel

POWER = pc.ParametrosEstudio("power", n_inicial=1.5, n_maximo=3.0, abandono_objetivo=0.03, ajuste_automatico=True)
PRED = pc.ParametrosEstudio("predictivo", n_inicial=1.5, n_maximo=3.0, abandono_objetivo=0.03, ajuste_automatico=True)


def obs(**k):
    base = dict(agentes_disponibles=10, llamadas_en_curso=0, tasa_contacto=0.41, intentos_observados=462,
                abandonadas_ventana=0, conectadas_ventana=100, fp_amd_ventana=0, ocio_promedio_s=30)
    base.update(k)
    return pc.Observacion(**base)


def test_power_deriva_n_de_la_tasa_de_contacto():
    d = pc.decidir(POWER, obs(), None)
    assert d.n == round(1 / 0.41, 2) and d.a_originar == int(10 * d.n)


def test_power_respeta_n_maximo_y_llamadas_en_curso():
    d = pc.decidir(POWER, obs(tasa_contacto=0.1, llamadas_en_curso=5), None)
    assert d.n == 3.0 and d.a_originar == 25


def test_power_sin_muestra_usa_n_inicial():
    d = pc.decidir(POWER, obs(intentos_observados=10), None)
    assert d.n == 1.5


def test_power_n_fijo():
    p = pc.ParametrosEstudio("power", 1.2, 3.0, 0.03, ajuste_automatico=False)
    assert pc.decidir(p, obs(), None).n == 1.2


def test_predictivo_baja_rapido_sube_lento():
    alto = pc.decidir(PRED, obs(abandonadas_ventana=8, conectadas_ventana=92), 2.0)
    assert alto.n == 1.5                                  # -25 %
    bajo = pc.decidir(PRED, obs(abandonadas_ventana=1, conectadas_ventana=99), 2.0)
    assert bajo.n == 2.05                                 # +0,05
    assert (2.0 - alto.n) > (bajo.n - 2.0) * 5


def test_predictivo_usa_abandono_real_con_falsos_positivos():
    # 2 % reportado, pero con FP del AMD supera el 3 %: tiene que bajar.
    d = pc.decidir(PRED, obs(abandonadas_ventana=2, conectadas_ventana=98, fp_amd_ventana=2), 2.0)
    assert d.n < 2.0


def test_predictivo_cae_a_power_con_pocos_agentes():
    d = pc.decidir(PRED, obs(agentes_disponibles=5), 2.5)
    assert d.modo_efectivo == "power" and "cae a power" in d.motivo


def test_modos_sin_pacer():
    assert pc.decidir(pc.ParametrosEstudio("preview", 1.5, 3, 0.03, True), obs(), None).a_originar == 0


def test_telefonia():
    assert tel.disposicion_por_fin(atendida=False, conectada=False, amd=None, causa=17, abandonada=False) == tel.OCUPADO
    assert tel.disposicion_por_fin(atendida=False, conectada=False, amd=None, causa=1, abandonada=False) == tel.FUERA_DE_SERVICIO
    assert tel.disposicion_por_fin(atendida=True, conectada=False, amd="MACHINE", causa=16, abandonada=False) == tel.CONTESTADOR_DETECTADO
    assert tel.disposicion_por_fin(atendida=True, conectada=False, amd="HUMAN", causa=16, abandonada=True) == tel.ABANDONADA
    assert tel.disposicion_por_fin(atendida=True, conectada=True, amd="HUMAN", causa=16, abandonada=False) is None
    assert tel.disposicion_por_fin(atendida=False, conectada=False, amd=None, causa=None, abandonada=False) == tel.NO_CONTESTA
