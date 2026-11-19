"""Superficie administrativa en Cloud Functions for Firebase (ADR-010).

Único punto de entrada HTTP con ruteo propio (invoke.enrutador), igual que paneles.
Firebase Hosting reescribe /api/** a esta función (ver firebase.json).
El paquete invoke/ y schemas/ se copian acá antes de desplegar
(scripts/preparar_functions.sh, predeploy de firebase.json).
"""
from firebase_functions import https_fn, options

from invoke.api import despachar
from invoke.enrutador import Peticion

# Encargo M3: la concurrencia por instancia coincide con el pool de conexiones
# (INVOKE_DB_POOL, 10 por defecto). Más peticiones simultáneas solo esperarían una
# conexión; así Cloud Functions abre otra instancia en lugar de encolarlas.
options.set_global_options(region="southamerica-east1", memory=options.MemoryOption.MB_512,
                           max_instances=10, concurrency=10, cpu=1)


@https_fn.on_request(secrets=["DATABASE_URL", "TURN_SECRETO"], vpc_connector="invoke-vpc",
                     vpc_connector_egress_settings=options.VpcEgressSetting.PRIVATE_RANGES_ONLY)
def api(req: https_fn.Request) -> https_fn.Response:
    p = Peticion(
        metodo=req.method,
        ruta=req.path,
        query={k: v for k, v in req.args.items()},
        cuerpo=req.get_data(),
        cabeceras={k: v for k, v in req.headers.items()},
    )
    r = despachar(p)
    return https_fn.Response(r.cuerpo, status=r.estado, content_type=r.tipo, headers=r.cabeceras)
