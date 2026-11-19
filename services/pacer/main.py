"""Pacer del discador (Cloud Run desde la Fase 2, mínimo de instancias = 1, máximo = 1).

Cada PACER_INTERVALO_S segundos, por cada estudio activo en power o predictivo:
  1. observa (agentes disponibles con audio, llamadas en curso, tasa de contacto
     de la franja, abandono real en la ventana deslizante, ocio);
  2. decide con invoke.dominio.pacer (lógica pura y testeada);
  3. reserva casos con invoke.reservar_casos_pacer y encola 'originar' con AMD;
  4. si sobran llamadas timbrando porque la sala se vació, las descarta antes
     de que atiendan (llamada_descartada: no es intento ni abandono).
Los ajustes de N quedan en el log como 'pacer_ajuste' (como mucho uno por minuto).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from invoke import db  # noqa: E402
from invoke.config import config  # noqa: E402
from invoke.dominio import pacer as pc  # noqa: E402

log = logging.getLogger("invoke.pacer")
DIAS_CONTACTO = 14
_ultimo_ajuste: dict[int, float] = {}


def observar(cur, est: dict) -> pc.Observacion:
    ahora = dt.datetime.now(dt.timezone.utc)
    agentes = db.uno(cur, """
        SELECT count(*) AS n, COALESCE(avg(EXTRACT(epoch FROM clock_timestamp() - desde)), 0) AS ocio
        FROM invoke.participante_estado
        WHERE estudio_id = %s AND estado = 'disponible' AND canal_audio IS NOT NULL AND caso_id IS NULL""", (est["id"],))
    en_curso = db.uno(cur, """
        SELECT count(*) AS n FROM invoke.llamada
        WHERE estudio_id = %s AND participante_id IS NULL AND atendida_en IS NULL AND finalizada_en IS NULL
          AND NOT descartada AND originada_en > clock_timestamp() - interval '2 minutes'""", (est["id"],))["n"]
    franja = db.uno(cur, "SELECT invoke.franja_de(%s, clock_timestamp()) AS f", (est["id"],))["f"]
    contacto = db.uno(cur, "SELECT * FROM invoke.tasa_contacto_observada(%s, %s, %s)",
                      (est["id"], franja, ahora - dt.timedelta(days=DIAS_CONTACTO)))
    ventana = ahora - dt.timedelta(minutes=est["ventana_abandono_min"])
    ab = db.uno(cur, "SELECT * FROM invoke.tasa_abandono(%s, %s, %s)", (est["id"], ventana, ahora))
    return pc.Observacion(
        agentes_disponibles=agentes["n"], llamadas_en_curso=en_curso,
        tasa_contacto=float(contacto["tasa"]) if contacto["tasa"] is not None else None,
        intentos_observados=contacto["intentos"],
        abandonadas_ventana=ab["abandonadas"], conectadas_ventana=ab["conectadas"], fp_amd_ventana=ab["fp_amd"],
        ocio_promedio_s=float(agentes["ocio"]))


def parametros(est: dict) -> pc.ParametrosEstudio:
    n_base = float(est["n_vigente"] or est["n_inicial"]) if not est["ajuste_automatico"] else float(est["n_inicial"])
    return pc.ParametrosEstudio(
        modo=est["modo_discado"], n_inicial=n_base, n_maximo=float(est["n_maximo"]),
        abandono_objetivo=float(est["abandono_objetivo"]), ajuste_automatico=est["ajuste_automatico"],
        min_agentes_predictivo=est["min_agentes_predictivo"])


def ciclo_estudio(est: dict) -> pc.Decision:
    with db.transaccion() as cur:
        o = observar(cur, est)
        n_actual = float(est["n_vigente"]) if est["n_vigente"] is not None else None
        d = pc.decidir(parametros(est), o, n_actual)
        if est["ajuste_automatico"] and (n_actual is None or abs(d.n - n_actual) >= 0.05):
            if time.monotonic() - _ultimo_ajuste.get(est["id"], 0) > 60:
                cur.execute("UPDATE invoke.estudio SET n_vigente = %s WHERE id = %s", (d.n, est["id"]))
                db.evento(cur, "pacer_ajuste", estudio_id=est["id"], autor="pacer",
                          datos={"n_anterior": n_actual, "n_nuevo": d.n, "modo_efectivo": d.modo_efectivo,
                                 "motivo": d.motivo, "origen": "pacer"})
                _ultimo_ajuste[est["id"]] = time.monotonic()
        if d.a_originar > 0:
            for r in db.todos(cur, "SELECT * FROM invoke.reservar_casos_pacer(%s, %s)", (est["id"], d.a_originar)):
                cur.execute("""INSERT INTO invoke.comando_telefonia (tipo, llamada_id, datos, autor)
                               VALUES ('originar', %s, %s, 'pacer')""",
                            (r["llamada_id"], db.Jsonb({"numero": r["numero"], "amd": True, "timeout": 25})))
        elif o.agentes_disponibles == 0 and o.llamadas_en_curso > 0:
            # La sala se vació (pausas): se descartan las que todavía timbran.
            for r in db.todos(cur, """
                SELECT llamada_id, caso_id FROM invoke.llamada
                WHERE estudio_id = %s AND participante_id IS NULL AND atendida_en IS NULL AND finalizada_en IS NULL
                  AND NOT descartada ORDER BY originada_en DESC""", (est["id"],)):
                db.evento(cur, "llamada_descartada", caso_id=r["caso_id"], llamada_id=r["llamada_id"],
                          datos={"motivo": "sin encuestadores disponibles"})
                cur.execute("INSERT INTO invoke.comando_telefonia (tipo, llamada_id, autor) VALUES ('colgar', %s, 'pacer')",
                            (r["llamada_id"],))
        return d


def bucle() -> None:
    intervalo = config().pacer_intervalo_s
    while True:
        try:
            with db.transaccion() as cur:
                estudios = db.todos(cur, """SELECT * FROM invoke.estudio
                                            WHERE estado = 'activo' AND modo_discado IN ('power', 'predictivo')""")
            for est in estudios:
                try:
                    d = ciclo_estudio(est)
                    if d.a_originar:
                        log.info("%s: %s N=%.2f originar=%d (%s)", est["codigo"], d.modo_efectivo, d.n, d.a_originar, d.motivo)
                except Exception:  # noqa: BLE001
                    log.exception("ciclo del estudio %s falló", est["codigo"])
        except Exception:  # noqa: BLE001
            log.exception("ciclo del pacer falló")
        time.sleep(intervalo)


class _Salud(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    threading.Thread(target=HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), _Salud).serve_forever,
                     daemon=True).start()
    bucle()
