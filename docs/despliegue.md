# Guía de despliegue — INVOKE 2

Nov 19, 2026 · @Equipos

Cómo levantar INVOKE 2 en Google Cloud y Firebase, de cero a la primera llamada. Sigue el stack de `paneles` (ADR-010): Cloud SQL, Cloud Functions, Firebase Hosting y Auth, y tres servicios en Cloud Run.

## Mapa de componentes

```mermaid
flowchart LR
  N[Navegador] -->|HTTPS| F[Firebase<br/>Hosting, Auth, Functions]
  N -->|WebSocket| S[Cloud Run: sala]
  N -->|SIP/WSS, SRTP| T[coturn]
  T --> A[Asterisk 20 en VM]
  F -->|VPC| PG[(Cloud SQL Postgres)]
  S --> PG
  ARI[Cloud Run: ari] --> PG
  ARI -->|ARI| A
  P[Cloud Run: pacer] --> PG
  A -->|realtime| PG
  A --> TR[Trunk SIP existente]
```

| Componente | Dónde corre | Escala | Origen en el repo |
| --- | --- | --- | --- |
| Web (puesto, sala, admin, editor) | Firebase Hosting | CDN | `web/` |
| API administrativa | Cloud Functions (2.ª gen.) | 0–10 instancias | `functions/` + `invoke/` |
| sala (WebSocket) | Cloud Run | mín. 1 | `services/sala` |
| ari (escucha de Asterisk) | Cloud Run, interno | **exactamente 1** | `services/ari` |
| pacer (Fase 2) | Cloud Run, interno | **exactamente 1** | `services/pacer` |
| Base | Cloud SQL Postgres 16, IP privada | 2 vCPU / 8 GB para empezar | `db/migrations` |
| PBX | Compute Engine (Docker) | e2-standard-2 | `infra/asterisk` |
| TURN | Compute Engine (Docker) | e2-small | `infra/coturn` |

**Por qué ari y pacer van con exactamente una instancia.** Los dos son consumidores únicos: ari tiene el único WebSocket de eventos contra Asterisk y pacer es un lazo de control con temporizadores. Dos instancias originarían llamadas dos veces. Se despliegan con `--min-instances 1 --max-instances 1` y CPU siempre asignada.

## Requisitos

- Un proyecto de Google Cloud con facturación, asociado a un proyecto de Firebase (el mismo id).
- `gcloud`, `firebase-tools` 13+, Docker, Python 3.12 y Node 22 en la máquina de quien despliega.
- Dos nombres DNS con IP fija, `pbx.<dominio>` y `turn.<dominio>`, con certificados TLS (Let's Encrypt). El navegador exige WSS con certificado válido.
- Las credenciales del trunk SIP actual: host, usuario y clave (no se reevalúa el proveedor).

```bash
export PROYECTO=invoke-equipos
export REGION=southamerica-east1
gcloud config set project $PROYECTO
gcloud services enable run.googleapis.com cloudfunctions.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com vpcaccess.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com compute.googleapis.com cloudtasks.googleapis.com
```

## 1. Red

Una VPC con un conector serverless: las funciones y los servicios llegan a la base por IP privada.

```bash
gcloud compute networks create invoke-vpc --subnet-mode=custom
gcloud compute networks subnets create invoke-sub --network=invoke-vpc --region=$REGION --range=10.10.0.0/24
gcloud compute networks vpc-access connectors create invoke-vpc --region=$REGION \
  --network=invoke-vpc --range=10.8.0.0/28
gcloud compute addresses create pbx-ip turn-ip --region=$REGION

# PBX: SIP del trunk, WSS de los navegadores y RTP
gcloud compute firewall-rules create invoke-pbx --network=invoke-vpc --target-tags=pbx \
  --allow=udp:5060,tcp:8089,udp:10000-20000
# TURN: señalización y rango de relay
gcloud compute firewall-rules create invoke-turn --network=invoke-vpc --target-tags=turn \
  --allow=udp:3478,tcp:3478,tcp:5349,udp:49152-65535
# ARI (8088) solo desde la VPC: lo usa el servicio ari a través del conector
gcloud compute firewall-rules create invoke-ari --network=invoke-vpc --target-tags=pbx \
  --source-ranges=10.8.0.0/28 --allow=tcp:8088
```

En producción, limitá `udp:5060` con `--source-ranges` a las IP del proveedor del trunk.

## 2. Base de datos

```bash
gcloud sql instances create invoke --database-version=POSTGRES_16 --region=$REGION \
  --tier=db-custom-2-8192 --network=invoke-vpc --no-assign-ip \
  --backup --enable-point-in-time-recovery
gcloud sql databases create invoke --instance=invoke
gcloud sql users set-password postgres --instance=invoke --prompt-for-password
```

Las migraciones se aplican desde una máquina con acceso a la IP privada, ya sea la VM del PBX o el Cloud SQL Auth Proxy:

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://postgres:CLAVE@IP_PRIVADA:5432/invoke"
python scripts/migrar.py --verificar        # aplica en orden y verifica accesos
```

Después se crean los usuarios con login, una sola vez, con claves generadas y guardadas en Secret Manager:

```bash
psql "$DATABASE_URL" -v clave_api="'$(openssl rand -base64 24)'" \
  -v clave_ast="'...'" -v clave_tab="'...'" -f db/roles_produccion.sql
```

| Usuario | Rol | Lo usa |
| --- | --- | --- |
| `invoke_api` | `invoke_app` | Functions y los tres servicios de Cloud Run |
| `asterisk_rt_login` | `asterisk_rt` | Asterisk realtime: solo lee los endpoints de los puestos |
| `invoke_tablero` | `invoke_lectura` | Script del tablero de productividad (vista `cdr_compat`) |

**Nunca con el usuario `postgres`.** La aplicación se conecta siempre como `invoke_api`. Las invariantes dependen de que `invoke_app` no pueda escribir proyecciones ni modificar hechos, y el verificador de accesos lo controla en cada migración.

## 3. Secretos

Ningún secreto vive en el repositorio (`CLAUDE.md`, regla 11).

```bash
printf 'postgresql://invoke_api:CLAVE@IP_PRIVADA:5432/invoke' | gcloud secrets create DATABASE_URL --data-file=-
openssl rand -base64 32 | tr -d '\n' | gcloud secrets create TURN_SECRETO --data-file=-
openssl rand -base64 24 | tr -d '\n' | gcloud secrets create ARI_CLAVE --data-file=-
printf 'CLAVE_TRUNK' | gcloud secrets create TRUNK_CLAVE --data-file=-
printf 'CLAVE_ASTERISK_RT' | gcloud secrets create DB_CLAVE_ASTERISK --data-file=-
```

Dale `roles/secretmanager.secretAccessor` a la cuenta de servicio de Functions, de Cloud Run y de las VM.

## 4. PBX Asterisk

Una VM con Docker. Los `.conf` de `infra/asterisk` son plantillas que el contenedor completa con variables de entorno.

```bash
gcloud compute instances create pbx --zone=${REGION}-a --machine-type=e2-standard-2 \
  --network-interface=subnet=invoke-sub,address=pbx-ip --tags=pbx \
  --image-family=cos-stable --image-project=cos-cloud
```

En la VM, con el repositorio clonado y los certificados en `/etc/asterisk/keys/pbx.crt` y `pbx.key`:

```bash
docker build -t invoke-pbx infra/asterisk
docker run -d --name pbx --network host --restart always \
  -v /etc/asterisk/keys:/etc/asterisk/keys:ro \
  -e PBX_IP_PUBLICA=... -e TRUNK_HOST=... -e TRUNK_DOMINIO=... -e TRUNK_USUARIO=... \
  -e TRUNK_CLAVE="$(gcloud secrets versions access latest --secret=TRUNK_CLAVE)" \
  -e ARI_CLAVE="$(gcloud secrets versions access latest --secret=ARI_CLAVE)" \
  -e DB_HOST=IP_PRIVADA_SQL -e DB_CLAVE_ASTERISK="$(gcloud secrets versions access latest --secret=DB_CLAVE_ASTERISK)" \
  -e STUN_HOST=turn.<dominio> invoke-pbx
```

- Copiá el audio `invoke-disculpas.wav` a `/var/lib/asterisk/sounds/es/` (ver `infra/asterisk/sounds/LEEME.txt`).
- Comprobá el trunk con `docker exec pbx asterisk -rx "pjsip show registrations"` y ARI con `curl -u invoke:CLAVE http://IP_PRIVADA:8088/ari/asterisk/info`.
- AMI está deshabilitado a propósito (ADR-003).

## 5. TURN (coturn)

```bash
gcloud compute instances create turn --zone=${REGION}-a --machine-type=e2-small \
  --network-interface=subnet=invoke-sub,address=turn-ip --tags=turn \
  --image-family=cos-stable --image-project=cos-cloud
# en la VM:
docker run -d --name turn --network host --restart always \
  -v $PWD/turnserver.conf:/etc/coturn/turnserver.conf:ro -v /etc/coturn/certs:/etc/coturn/certs:ro \
  coturn/coturn:4.6 -c /etc/coturn/turnserver.conf
```

Antes de copiar `infra/coturn/turnserver.conf`, reemplazá `${TURN_SECRETO}` y `${PBX_IP_PUBLICA}` (con `envsubst`). El relay solo acepta como destino al PBX.

## 6. Servicios de Cloud Run

```bash
gcloud artifacts repositories create invoke --repository-format=docker --location=$REGION
gcloud auth configure-docker $REGION-docker.pkg.dev
for s in ari sala pacer; do
  docker build -t $REGION-docker.pkg.dev/$PROYECTO/invoke/$s -f infra/cloudrun/Dockerfile.$s .
  docker push $REGION-docker.pkg.dev/$PROYECTO/invoke/$s
done

COMUN="--region=$REGION --vpc-connector=invoke-vpc --vpc-egress=private-ranges-only \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest"

gcloud run deploy ari --image=$REGION-docker.pkg.dev/$PROYECTO/invoke/ari $COMUN \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --ingress=internal --no-allow-unauthenticated \
  --set-secrets=ARI_CLAVE=ARI_CLAVE:latest \
  --set-env-vars=ARI_URL=http://IP_PRIVADA_PBX:8088/ari,INVOKE_TRUNK='PJSIP/{numero}@trunk'

gcloud run deploy pacer --image=$REGION-docker.pkg.dev/$PROYECTO/invoke/pacer $COMUN \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --ingress=internal --no-allow-unauthenticated

gcloud run deploy sala --image=$REGION-docker.pkg.dev/$PROYECTO/invoke/sala $COMUN \
  --min-instances=1 --max-instances=3 --timeout=3600 --session-affinity --allow-unauthenticated \
  --set-env-vars=FIREBASE_PROJECT=$PROYECTO
```

La sala es pública pero autenticada: el primer mensaje de cada WebSocket trae el token de Firebase, que se verifica antes de suscribir. Sin token válido, la sala cierra la conexión con el código 4401.

## 7. Firebase: Auth, Functions y Hosting

1. **Auth.** En la consola de Firebase, habilitá el proveedor «Correo y contraseña» y agregá el dominio de Hosting a los dominios autorizados.
2. **Configuración de la web.** Editá `web/js/config.js` con la configuración web del proyecto (no es secreta) y con `salaWs: 'wss://<url-de-sala>/sala'`.
3. **Variables de la función.** Creá `functions/.env.<proyecto>` con `SIP_WSS=wss://pbx.<dominio>:8089/ws`, `SIP_DOMINIO=pbx.<dominio>`, `TURN_URLS=turn:turn.<dominio>:3478?transport=udp,turns:turn.<dominio>:5349?transport=tcp` y `STUN_URLS=stun:turn.<dominio>:3478`. `INVOKE_AUTH_DEV` no se define nunca.
4. **Functions.** En `functions/main.py` ya están la región, el conector VPC y los secretos que usa. El predeploy copia `invoke/` y `schemas/` adentro de `functions/`.
   ```bash
   echo '{"projects":{"default":"'$PROYECTO'"}}' > .firebaserc
   firebase deploy --only functions:invoke
   ```
5. **Hosting.** Reescribe `/api/**` a la función `api` (ver `firebase.json`).
   ```bash
   firebase deploy --only hosting
   ```

## 8. Primer administrador

Creá la cuenta en Firebase Auth (consola o CLI) y dala de alta en la base con su `uid`. Desde ahí, los demás usuarios se crean desde **Administración → Usuarios**.

```sql
INSERT INTO invoke.usuario (uid, email, nombre, roles)
VALUES ('UID_DE_FIREBASE', 'coordinacion@equipos.com.uy', 'Coordinación', ARRAY['administrador','investigador','supervisor']);
```

## 9. Verificación después de desplegar

1. `GET https://<hosting>/api/salud` responde `{"ok": true}`.
2. Entrar como administrador, crear un estudio de prueba marcado como **prueba o capacitación**, publicar el cuestionario de ejemplo (`schemas/ejemplos/satisfaccion_clientes.json`, desde «Importar JSON» del editor) y cargar una guía con un número propio.
3. Entrar como encuestador, hacer la prueba de audio y verificar que la barra superior pase a **Audio OK**.
4. Pedir el caso, llamar al número propio, atender y comprobar audio en las dos direcciones.
5. Desde la consola de supervisión, conectar el audio de escucha y escuchar la llamada. La calidad de red del puesto tiene que aparecer en la tarjeta.
6. Clasificar, abrir **Reportes** y ver el intento. El estudio de prueba no da ficha técnica, porque no es un estudio.
7. Revisar los logs de `ari`: tiene que figurar «conectado a ARI» y ningún comando en error (`SELECT * FROM invoke.comando_telefonia WHERE estado = 'error'`).

## Actualizar y operar

### Orden de una actualización

1. **Migraciones** con `scripts/migrar.py --verificar`. Son aditivas: el código viejo tiene que seguir funcionando con el esquema nuevo.
2. **Functions** (`firebase deploy --only functions:invoke`).
3. **Cloud Run**: `sala`, después `pacer` y por último `ari`, fuera del horario de campo. Al reiniciar, `ari` recupera los puestos desde la base y los navegadores reconectan solos.
4. **Hosting**.

### Copias y recuperación

Cloud SQL con backup diario y recuperación a un punto en el tiempo. Como el log de eventos es inmutable, una recuperación a un instante reproduce exactamente los reportes de ese instante.

### Rotación de secretos

Para rotar `TURN_SECRETO` o `ARI_CLAVE`, se crea una versión nueva y se redespliega el consumidor (coturn y Functions, o PBX y ari). Las credenciales SIP de los puestos se rotan solas en cada inicio de sesión.

### Qué mirar en el día a día

| Indicador | Cómo | Para qué |
| --- | --- | --- |
| Comandos en error | `invoke.comando_telefonia` con `estado = 'error'` | Si suben, revisar el trunk o ARI |
| Proporción por TURN | `avg(relay::int)` sobre `invoke.calidad_red` del día | Insumo de ADR-005 |
| Abandono real | Consola → indicadores del discador | Por encima del objetivo, bajar N |
| Proyección íntegra | `SELECT invoke.reconstruir_caso_estado(id)` sobre los casos afectados, como dueño de la base | Después de un incidente |

## Desarrollo local

```bash
pip install -r requirements-dev.txt
docker compose up -d postgres
export DATABASE_URL=postgresql://postgres:invoke@127.0.0.1:5432/invoke
python scripts/migrar.py --verificar
python scripts/semilla_demo.py
INVOKE_AUTH_DEV=1 python scripts/servidor_local.py   # http://127.0.0.1:8000/?dev_uid=dev-admin
```

Usuarios de la semilla: `dev-admin`, `dev-sup`, `dev-enc1`, `dev-enc2` y `dev-enc3`. Sin Asterisk funciona todo salvo el audio, y el puesto lo avisa. Para el entorno completo (PBX, TURN y servicios) está `docker compose up` con un `.env` armado a partir de `.env.ejemplo`.

### Integración continua

`.github/workflows/ci.yml` corre en cada push: pytest contra un Postgres real (invariantes, fórmulas, API, exportación y verificador de accesos), las baterías JS compartidas con Python y `tsc` sobre el frontend.
