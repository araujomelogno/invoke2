"""Tabla de rutas de la superficie HTTP (Cloud Functions, ADR-010).

Un único punto de entrada; cada ruta declara qué roles la habilitan. Toda
escritura pasa por invoke.evento o por las funciones de la base.
"""
from __future__ import annotations

from . import db
from .auth import autenticar
from .enrutador import Enrutador, ErrorHttp, Peticion, Respuesta
from .servicios import (cuestionarios, cuotas, estacion, estudios, exportacion, muestra, reportes,
                        supervision, usuarios)

app = Enrutador()
app.autenticar = autenticar

ADMIN = ("administrador",)
GESTION = ("administrador", "supervisor", "investigador")
SUPERV = ("supervisor", "administrador")
ENC = ("encuestador", "supervisor")


@app.ruta("GET", "/api/salud", publica=True)
def salud(p: Peticion):
    with db.transaccion() as cur:
        cur.execute("SELECT 1")
    return {"ok": True}


@app.ruta("GET", "/api/yo")
def yo(p: Peticion):
    u = p.usuario
    return {"uid": u.uid, "email": u.email, "nombre": u.nombre, "roles": u.roles, "participante_id": u.participante_id}


# ───────────────────────────── administración ─────────────────────────────

@app.ruta("GET", "/api/usuarios", *ADMIN)
def usuarios_listar(p):
    with db.transaccion() as cur:
        return usuarios.listar(cur)


@app.ruta("POST", "/api/usuarios", *ADMIN)
def usuarios_guardar(p):
    with db.transaccion() as cur:
        return usuarios.guardar(cur, p.json())


@app.ruta("GET", "/api/estudios", *GESTION)
def estudios_listar(p):
    with db.transaccion() as cur:
        return estudios.listar(cur)


@app.ruta("POST", "/api/estudios", *ADMIN)
def estudios_crear(p):
    with db.transaccion() as cur:
        return estudios.crear(cur, p.json(), p.usuario.uid)


@app.ruta("GET", "/api/estudios/{id}", *GESTION)
def estudios_obtener(p):
    with db.transaccion() as cur:
        return estudios.obtener(cur, p.entero("id"))


@app.ruta("PATCH", "/api/estudios/{id}", *ADMIN)
def estudios_actualizar(p):
    with db.transaccion() as cur:
        return estudios.actualizar(cur, p.entero("id"), p.json(), p.usuario.uid)


@app.ruta("PUT", "/api/estudios/{id}/encuestadores", *ADMIN)
def estudios_encuestadores(p):
    with db.transaccion() as cur:
        estudios.asignar_encuestadores(cur, p.entero("id"), p.json().get("uids", []))
        return estudios.obtener(cur, p.entero("id"))


@app.ruta("GET", "/api/estudios/{id}/catalogo")
def estudios_catalogo(p):
    with db.transaccion() as cur:
        return estudios.catalogo(cur, p.entero("id"))


@app.ruta("POST", "/api/estudios/{id}/guias", *ADMIN)
def guias_cargar(p):
    d = p.json()
    with db.transaccion() as cur:
        return muestra.cargar_guia(cur, p.entero("id"), d["nombre"], d["csv"], p.usuario.uid,
                                   prioridad=int(d.get("prioridad", 0)), tope_fuera_marco=d.get("tope_fuera_marco"),
                                   origen=d.get("origen", "marco"))


@app.ruta("PATCH", "/api/guias/{id}", *ADMIN)
def guias_actualizar(p):
    with db.transaccion() as cur:
        return muestra.actualizar_guia(cur, p.entero("id"), p.json())


@app.ruta("GET", "/api/casos/{id}", *GESTION)
def caso_ficha(p):
    with db.transaccion() as cur:
        return muestra.ficha_caso(cur, p.entero("id"))


@app.ruta("GET", "/api/estudios/{id}/cuotas", *GESTION)
def cuotas_avance(p):
    with db.transaccion() as cur:
        return cuotas.avance(cur, p.entero("id"))


@app.ruta("POST", "/api/estudios/{id}/cuotas", *ADMIN)
def cuotas_crear(p):
    with db.transaccion() as cur:
        return cuotas.crear(cur, p.entero("id"), p.json())


@app.ruta("PATCH", "/api/cuotas/{id}", *ADMIN)
def cuotas_actualizar(p):
    with db.transaccion() as cur:
        return cuotas.actualizar(cur, p.entero("id"), p.json())


@app.ruta("DELETE", "/api/cuotas/{id}", *ADMIN)
def cuotas_borrar(p):
    with db.transaccion() as cur:
        cuotas.borrar(cur, p.entero("id"))


@app.ruta("GET", "/api/estudios/{id}/cuestionarios", "administrador", "investigador")
def cq_versiones(p):
    with db.transaccion() as cur:
        return cuestionarios.versiones(cur, p.entero("id"))


@app.ruta("GET", "/api/cuestionarios/{id}", "administrador", "investigador")
def cq_obtener(p):
    with db.transaccion() as cur:
        return cuestionarios.obtener(cur, p.entero("id"))


@app.ruta("PUT", "/api/estudios/{id}/cuestionario", "administrador", "investigador")
def cq_guardar(p):
    with db.transaccion() as cur:
        return cuestionarios.guardar(cur, p.entero("id"), p.json(), p.usuario.uid)


@app.ruta("POST", "/api/cuestionarios/{id}/publicar", "administrador", "investigador")
def cq_publicar(p):
    with db.transaccion() as cur:
        return cuestionarios.publicar(cur, p.entero("id"))


# ───────────────────────────── reportes y exportación ─────────────────────────────

@app.ruta("GET", "/api/estudios/{id}/reportes/ficha", *GESTION)
def rep_ficha(p):
    with db.transaccion() as cur:
        return reportes.ficha_tecnica(cur, p.entero("id"), p.query.get("corte", "parcial"), p.query.get("hasta"))


@app.ruta("GET", "/api/estudios/{id}/reportes/productividad", *GESTION)
def rep_productividad(p):
    with db.transaccion() as cur:
        return reportes.productividad(cur, p.entero("id"), p.query.get("desde"), p.query.get("hasta"))


@app.ruta("GET", "/api/estudios/{id}/reportes/fuera-de-marco", *GESTION)
def rep_fuera(p):
    with db.transaccion() as cur:
        return reportes.fuera_de_marco(cur, p.entero("id"))


@app.ruta("GET", "/api/estudios/{id}/reportes/amd", *GESTION)
def rep_amd(p):
    with db.transaccion() as cur:
        return reportes.calibracion_amd(cur, p.entero("id"))


@app.ruta("GET", "/api/cdr", *GESTION)
def rep_cdr(p):
    estudio = p.query.get("estudio")
    with db.transaccion() as cur:
        return reportes.cdr(cur, int(estudio) if estudio else None, p.query.get("desde"), p.query.get("hasta"))


@app.ruta("GET", "/api/estudios/{id}/exportar", "administrador", "investigador")
def exportar(p):
    with db.transaccion() as cur:
        contenido, tipo, nombre = exportacion.exportar(
            cur, p.entero("id"), p.query.get("formato", "csv"), p.query.get("todas") != "1")
    return Respuesta(200, contenido, tipo, {"Content-Disposition": f'attachment; filename="{nombre}"'})


# ───────────────────────────── supervisión ─────────────────────────────

@app.ruta("GET", "/api/supervision/sala", *SUPERV)
def sup_sala(p):
    e = p.query.get("estudio")
    with db.transaccion() as cur:
        return supervision.sala(cur, int(e) if e else None)


@app.ruta("GET", "/api/supervision/estudios/{id}/discador", *SUPERV)
def sup_discador(p):
    with db.transaccion() as cur:
        return supervision.indicadores_discador(cur, p.entero("id"))


@app.ruta("POST", "/api/supervision/estudios/{id}/n", *SUPERV)
def sup_n(p):
    d = p.json()
    with db.transaccion() as cur:
        return supervision.fijar_n(cur, p.usuario, p.entero("id"), d["n"], d.get("ajuste_automatico"))


@app.ruta("POST", "/api/supervision/escuchar/{participante}", *SUPERV)
def sup_escuchar(p):
    with db.transaccion() as cur:
        return supervision.escuchar(cur, p.usuario, p.entero("participante"), bool(p.json().get("susurro")))


@app.ruta("POST", "/api/supervision/dejar-de-escuchar/{participante}", *SUPERV)
def sup_dejar(p):
    with db.transaccion() as cur:
        supervision.dejar_de_escuchar(cur, p.usuario, p.entero("participante"))


@app.ruta("GET", "/api/supervision/estudios/{id}/parciales", *SUPERV)
def sup_parciales(p):
    with db.transaccion() as cur:
        return supervision.parciales(cur, p.entero("id"))


@app.ruta("POST", "/api/supervision/casos/{id}/promover", *SUPERV)
def sup_promover(p):
    with db.transaccion() as cur:
        return supervision.promover_parcial(cur, p.usuario, p.entero("id"), p.json().get("motivo", ""))


@app.ruta("POST", "/api/supervision/casos/{id}/estado-extendido", *SUPERV)
def sup_extendido(p):
    d = p.json()
    with db.transaccion() as cur:
        return supervision.aplicar_estado_extendido(cur, p.usuario, p.entero("id"), d["codigo"], d.get("motivo", ""))


@app.ruta("GET", "/api/supervision/estudios/{id}/amd", *SUPERV)
def sup_amd(p):
    with db.transaccion() as cur:
        return supervision.revision_amd(cur, p.entero("id"))


@app.ruta("POST", "/api/supervision/llamadas/{id}/falso-positivo", *SUPERV)
def sup_fp(p):
    with db.transaccion() as cur:
        return supervision.reclasificar_amd(cur, p.usuario, p.params["id"])


@app.ruta("GET", "/api/supervision/estudios/{id}/fuera-de-marco", *SUPERV)
def sup_fuera(p):
    with db.transaccion() as cur:
        return supervision.fuera_de_marco_por_encuestador(cur, p.entero("id"))


# ───────────────────────────── puesto del encuestador ─────────────────────────────

@app.ruta("GET", "/api/estacion/estudios", *ENC)
def est_estudios(p):
    with db.transaccion() as cur:
        return estacion.estudios_disponibles(cur, p.usuario)


@app.ruta("POST", "/api/estacion/sesion", *ENC)
def est_sesion(p):
    with db.transaccion() as cur:
        return estacion.iniciar_sesion(cur, p.usuario, int(p.json()["estudio_id"]))


@app.ruta("DELETE", "/api/estacion/sesion", *ENC)
def est_cerrar(p):
    with db.transaccion() as cur:
        estacion.cerrar_sesion(cur, p.usuario)


@app.ruta("POST", "/api/estacion/estado", *ENC)
def est_estado(p):
    with db.transaccion() as cur:
        return estacion.cambiar_estado(cur, p.usuario, p.json().get("estado"))


@app.ruta("POST", "/api/estacion/siguiente", *ENC)
def est_siguiente(p):
    with db.transaccion() as cur:
        return {"caso": estacion.siguiente_caso(cur, p.usuario)}


@app.ruta("GET", "/api/estacion/caso", *ENC)
def est_caso(p):
    with db.transaccion() as cur:
        return {"caso": estacion.caso_actual(cur, p.usuario),
                "respuestas": None}


@app.ruta("GET", "/api/estacion/casos/{id}/respuestas", *ENC)
def est_respuestas(p):
    with db.transaccion() as cur:
        return estacion.respuestas_vigentes(cur, p.entero("id"))


@app.ruta("POST", "/api/estacion/casos/{id}/saltar", *ENC)
def est_saltar(p):
    with db.transaccion() as cur:
        estacion.saltar_caso(cur, p.usuario, p.entero("id"), p.json().get("motivo", ""))


@app.ruta("POST", "/api/estacion/numero/clasificar", *ENC)
def est_clasificar(p):
    d = p.json()
    with db.transaccion() as cur:
        return estacion.clasificar_numero(cur, p.usuario, d["numero"], d.get("caso_actual"))


@app.ruta("POST", "/api/estacion/numero/resolver", *ENC)
def est_resolver(p):
    with db.transaccion() as cur:
        return estacion.resolver_numero(cur, p.usuario, p.json())


@app.ruta("POST", "/api/estacion/llamar", *ENC)
def est_llamar(p):
    with db.transaccion() as cur:
        return estacion.llamar(cur, p.usuario, p.json())


@app.ruta("POST", "/api/estacion/llamada/{accion}", *ENC)
def est_comando(p):
    with db.transaccion() as cur:
        estacion.comando(cur, p.usuario, p.params["accion"], p.json())


@app.ruta("POST", "/api/estacion/casos/{id}/iniciar", *ENC)
def est_iniciar(p):
    with db.transaccion() as cur:
        estacion.iniciar_encuesta(cur, p.usuario, p.entero("id"), p.json().get("llamada_id"))


@app.ruta("POST", "/api/estacion/casos/{id}/respuestas", *ENC)
def est_guardar(p):
    d = p.json()
    with db.transaccion() as cur:
        return estacion.guardar_respuestas(cur, p.usuario, p.entero("id"), d.get("respuestas", {}),
                                           d.get("llamada_id"), d.get("pantalla"))


@app.ruta("POST", "/api/estacion/casos/{id}/cuotas", *ENC)
def est_cuotas(p):
    with db.transaccion() as cur:
        return estacion.evaluar_cuotas(cur, p.usuario, p.entero("id"), p.json().get("respuestas", {}))


@app.ruta("POST", "/api/estacion/finalizar", *ENC)
def est_finalizar(p):
    with db.transaccion() as cur:
        return estacion.finalizar(cur, p.usuario, p.json())


@app.ruta("POST", "/api/estacion/calidad", *ENC)
def est_calidad(p):
    with db.transaccion() as cur:
        estacion.calidad_red(cur, p.usuario, p.json().get("muestras", []))


@app.ruta("POST", "/api/estacion/prueba-audio", *ENC)
def est_prueba(p):
    with db.transaccion() as cur:
        estacion.prueba_audio(cur, p.usuario, p.json())


def despachar(p: Peticion) -> Respuesta:
    try:
        return app.despachar(p)
    except KeyError as e:
        return Respuesta.json({"error": f"falta el campo {e.args[0]}"}, 400)
    except ErrorHttp as e:  # pragma: no cover - el enrutador ya las atrapa
        return Respuesta.json({"error": e.mensaje}, e.estado)
