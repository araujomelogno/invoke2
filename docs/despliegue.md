# Guía de despliegue — INVOKE 2

Nov 23, 2026 · @Equipos

Instrucciones paso a paso para desplegar INVOKE 2 de cero en Google Cloud y Firebase, hasta la primera llamada. Se siguen en orden: cada paso usa lo que dejó el anterior. Las razones de diseño de cada pieza están en `docs/decisiones.md`; acá solo está qué hacer.

**Cómo leer cada paso.** Cada uno dice **dónde** se ejecuta (tu máquina, la VM del PBX, la VM del TURN o la consola web), los comandos para copiar y pegar, y un bloque **Comprobá** con lo que tiene que dar. No sigas al paso siguiente si la comprobación falla: al final hay una tabla de problemas frecuentes.

**Tiempo estimado:** entre 3 y 4 horas, más lo que tarde en propagarse el DNS y lo que demore el proveedor del trunk en habilitar la IP.

## Índice

| Paso | Qué | Dónde |
| --- | --- | --- |
| 0 | Antes de empezar: herramientas, datos y archivo de valores | Tu máquina |
| 1 | Proyecto de Google Cloud y Firebase | Tu máquina |
| 2 | Red, IP fijas, firewall y DNS | Tu máquina y el DNS |
| 3 | Secretos | Tu máquina |
| 4 | Base de datos (Cloud SQL) | Tu máquina |
| 5 | Bucket de grabaciones y cuentas de servicio | Tu máquina |
| 6 | VM del PBX: crearla y prepararla | Tu máquina y la VM `pbx` |
| 7 | Esquema y usuarios de la base | VM `pbx` |
| 8 | PBX Asterisk | Tu máquina y la VM `pbx` |
| 9 | TURN | Tu máquina y la VM `turn` |
| 10 | Firebase: autenticación y app web | Consola de Firebase y tu máquina |
| 11 | Servicios de Cloud Run (`ari`, `sala`, `pacer`) | Tu máquina |
| 12 | Functions y Hosting | Tu máquina |
| 13 | Primer administrador | Consola de Firebase y VM `pbx` |
| 14 | Verificación de punta a punta | Navegador y teléfono |

Después: actualizar, operar, problemas frecuentes, PBX de laboratorio (apéndice A), qué se despliega y por qué (apéndice B) y desarrollo local.

## Qué se despliega

```mermaid
flowchart LR
  N[Navegador] -->|HTTPS| F[Firebase<br/>Hosting, Auth, Functions]
  N -->|WebSocket| S[Cloud Run: sala]
  N -->|SIP/WSS, SRTP| A[Asterisk 22 en VM pbx]
  N -.->|si la red lo exige| T[coturn en VM turn]
  T --> A
  F -->|conector VPC| PG[(Cloud SQL Postgres)]
  S --> PG
  ARI[Cloud Run: ari] --> PG
  ARI -->|ARI, IP privada| A
  P[Cloud Run: pacer] --> PG
  A -->|realtime, solo lectura| PG
  A --> TR[Trunk SIP del proveedor]
  ARI --> B[(Bucket de grabaciones)]
```

| Componente | Dónde corre | Tamaño | Origen en el repositorio |
| --- | --- | --- | --- |
| Web (puesto, sala, administración, editor) | Firebase Hosting | CDN | `web/` |
| API | Cloud Functions (2.ª gen.) | 0 a 10 instancias | `functions/` + `invoke/` |
| `sala` (WebSocket de la consola y el puesto) | Cloud Run | 1 a 3 instancias | `services/sala` |
| `ari` (único que le habla a Asterisk) | Cloud Run, interno | **exactamente 1** | `services/ari` |
| `pacer` (discador power y predictivo) | Cloud Run, interno | **exactamente 1** | `services/pacer` |
| Base | Cloud SQL Postgres 16, solo IP privada | 2 vCPU, 8 GB | `db/migrations` |
| PBX | VM Ubuntu 26.04 con Docker: Asterisk 22 | e2-standard-2, 50 GB | `infra/asterisk` |
| TURN | VM Ubuntu 26.04 con Docker: coturn | e2-small | `infra/coturn` |
| Grabaciones | Cloud Storage | ~25 GB por mes a sala llena | — |

`ari` y `pacer` van con una sola instancia porque son consumidores únicos: dos instancias originarían cada llamada dos veces.

## Paso 0 — Antes de empezar

**Dónde:** tu máquina.

### 0.1 Permisos

- Rol **Propietario** (Owner) en el proyecto de Google Cloud, o en la organización si el proyecto todavía no existe.
- Una **cuenta de facturación** activa. Firebase tiene que quedar en el plan **Blaze**: Functions no funciona en el plan gratuito.
- Acceso para crear registros en el **DNS** del dominio.
- Acceso de administración al repositorio en GitHub, para agregar una *deploy key* (paso 6).

### 0.2 Herramientas en tu máquina

| Herramienta | Versión | Cómo comprobarla |
| --- | --- | --- |
| Google Cloud CLI (`gcloud`) | reciente | `gcloud version` |
| Firebase CLI | 13 o superior | `firebase --version` (se instala con `npm install -g firebase-tools`) |
| Node.js | 22 | `node --version` |
| Python | 3.12 | `python3.12 --version` |
| git, openssl, dig | cualquiera | `git --version`, `openssl version`, `dig -v` |

No hace falta Docker en tu máquina: las imágenes de Cloud Run se construyen en Cloud Build, y la del PBX en la propia VM.

### 0.3 Datos que tenés que tener a mano

**Del proveedor del trunk SIP** (no se reevalúa el proveedor, pero sin estos datos no sale ninguna llamada):

| Dato | Para qué |
| --- | --- |
| Host (o IP) y puerto de su proxy SIP | `TRUNK_HOST`. Si el puerto no es 5060: `host:puerto` |
| IP o red desde la que manda el tráfico | `TRUNK_IP`: firewall y reconocimiento de sus llamadas |
| Dominio para el `From` | `TRUNK_DOMINIO` (muchas veces es el mismo host) |
| Usuario y clave | `TRUNK_USUARIO` y el secreto `TRUNK_CLAVE` |
| ¿Autentica por IP o exige registro? | Por IP: hay que darle la IP pública del PBX (paso 2). Con registro: paso 8.2 |
| Formato del número a discar | `INVOKE_TRUNK`. El sistema disca el número tal como está en la muestra; si el proveedor exige un prefijo, va en la plantilla: `PJSIP/0{numero}@trunk` |
| Número que se presenta (caller ID) | `INVOKE_CALLER_ID`, si el proveedor lo permite |
| Códecs | Tiene que aceptar G.711 A-law (PCMA) |
| Canales simultáneos contratados | En predictivo se originan varias llamadas por encuestador libre |

**Además:**

- Dos nombres DNS para crear: uno para el PBX (por ejemplo `pbx.invoke.equipos.com.uy`) y otro para el TURN (`turn.invoke.equipos.com.uy`).
- Un correo de sistemas para los avisos de Let's Encrypt.
- El **mensaje de disculpas** grabado (`invoke-disculpas.wav`): es lo que oye una persona cuando el discador abandona su llamada (texto: P-06 de `docs/decisiones.md`). Sin este archivo el PBX de producción no arranca.

### 0.4 Repositorio y archivo de valores

```bash
git clone git@github.com:araujomelogno/invoke2.git
cd invoke2
git checkout <etiqueta o commit de la versión a desplegar>
```

Todos los comandos de tu máquina se corren **desde la raíz del repositorio** y leen los valores de un archivo que armás una sola vez. Editá los valores de ejemplo antes de guardarlo:

```bash
cat > ~/invoke-despliegue.env <<'EOF'
# Valores del despliegue de INVOKE 2. NO van claves acá: las claves viven en Secret Manager (paso 3).
export PROYECTO=invoke-equipos                  # id del proyecto de Google Cloud (es también el de Firebase)
export REGION=southamerica-east1
export ZONA=southamerica-east1-a
export PBX_NOMBRE=pbx.invoke.equipos.com.uy     # nombre DNS del PBX
export TURN_NOMBRE=turn.invoke.equipos.com.uy   # nombre DNS del TURN
export CORREO_SISTEMAS=sistemas@equipos.com.uy  # avisos de vencimiento de Let's Encrypt
export PBX_IP_PRIVADA=10.10.0.10                # IP interna fija del PBX: no la cambies
# Datos del proveedor del trunk (0.3)
export TRUNK_HOST=sip.proveedor.example
export TRUNK_IP=198.51.100.20
export TRUNK_DOMINIO=sip.proveedor.example
export TRUNK_USUARIO=usuario_del_trunk
export INVOKE_TRUNK='PJSIP/{numero}@trunk'
export INVOKE_CALLER_ID=''
EOF
source ~/invoke-despliegue.env
```

A medida que avances, algunos pasos agregan valores a este archivo (IP, URL). **Al abrir una terminal nueva, corré siempre `source ~/invoke-despliegue.env` primero.**

## Paso 1 — Proyecto de Google Cloud y Firebase

**Dónde:** tu máquina.

```bash
source ~/invoke-despliegue.env
gcloud auth login

# Si el proyecto ya existe, saltá estas dos líneas.
gcloud projects create $PROYECTO --name="INVOKE 2"
gcloud billing accounts list                                   # anotá el ID de la cuenta
gcloud billing projects link $PROYECTO --billing-account=<ID DE LA CUENTA>

gcloud config set project $PROYECTO
gcloud config set compute/region $REGION
gcloud config set compute/zone $ZONA

gcloud services enable compute.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  vpcaccess.googleapis.com servicenetworking.googleapis.com run.googleapis.com \
  cloudfunctions.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  iap.googleapis.com storage.googleapis.com firebase.googleapis.com identitytoolkit.googleapis.com \
  eventarc.googleapis.com

firebase login
firebase projects:addfirebase $PROYECTO
```

En la consola de Firebase (<https://console.firebase.google.com>), abrí el proyecto, andá a **Uso y facturación → Detalles y configuración** y confirmá que el plan es **Blaze**.

**Comprobá:**

- `gcloud billing projects describe $PROYECTO --format='value(billingEnabled)'` responde `True`.
- `firebase projects:list` muestra el proyecto.

## Paso 2 — Red, IP fijas, firewall y DNS

**Dónde:** tu máquina, y después el panel de tu proveedor de DNS.

### 2.1 Red

Una VPC propia con tres piezas: acceso privado a servicios (para que Cloud SQL tenga IP privada), un conector serverless (para que Functions y Cloud Run lleguen a la base y a ARI) y las direcciones fijas.

```bash
source ~/invoke-despliegue.env

gcloud compute networks create invoke-vpc --subnet-mode=custom
gcloud compute networks subnets create invoke-sub --network=invoke-vpc --region=$REGION --range=10.10.0.0/24

# Cloud SQL con IP privada dentro de la VPC
gcloud compute addresses create invoke-servicios --global --purpose=VPC_PEERING --prefix-length=16 --network=invoke-vpc
gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com \
  --ranges=invoke-servicios --network=invoke-vpc

# Conector serverless: Functions y Cloud Run salen a la VPC por acá (10.8.0.0/28)
gcloud compute networks vpc-access connectors create invoke-vpc --region=$REGION \
  --network=invoke-vpc --range=10.8.0.0/28

# IP públicas fijas del PBX y del TURN, e IP interna fija del PBX
gcloud compute addresses create pbx-ip turn-ip --region=$REGION
gcloud compute addresses create pbx-ip-interna --region=$REGION --subnet=invoke-sub --addresses=$PBX_IP_PRIVADA

# Guardar las IP públicas en el archivo de valores
echo "export PBX_IP_PUBLICA=$(gcloud compute addresses describe pbx-ip --region=$REGION --format='value(address)')" >> ~/invoke-despliegue.env
echo "export TURN_IP_PUBLICA=$(gcloud compute addresses describe turn-ip --region=$REGION --format='value(address)')" >> ~/invoke-despliegue.env
source ~/invoke-despliegue.env
echo "PBX: $PBX_IP_PUBLICA  TURN: $TURN_IP_PUBLICA"
```

### 2.2 Firewall

La VPC es nueva, así que todo lo que no está en estas reglas queda cerrado:

```bash
# PBX: SIP solo desde el proveedor del trunk
gcloud compute firewall-rules create invoke-pbx-sip --network=invoke-vpc --target-tags=pbx \
  --source-ranges=$TRUNK_IP --allow=udp:5060
# PBX: WSS de los navegadores y RTP (navegadores, TURN y proveedor)
gcloud compute firewall-rules create invoke-pbx-medios --network=invoke-vpc --target-tags=pbx \
  --allow=tcp:8089,udp:10000-20000
# PBX: ARI (8088) solo desde el conector, para el servicio ari
gcloud compute firewall-rules create invoke-ari --network=invoke-vpc --target-tags=pbx \
  --source-ranges=10.8.0.0/28 --allow=tcp:8088
# TURN: señalización y rango de relay
gcloud compute firewall-rules create invoke-turn --network=invoke-vpc --target-tags=turn \
  --allow=udp:3478,tcp:3478,tcp:5349,udp:49152-65535
# Let's Encrypt (certbot usa el puerto 80) en las dos VM
gcloud compute firewall-rules create invoke-acme --network=invoke-vpc --target-tags=pbx,turn --allow=tcp:80
# SSH solo a través de IAP (nunca abierto a internet)
gcloud compute firewall-rules create invoke-ssh-iap --network=invoke-vpc \
  --source-ranges=35.235.240.0/20 --allow=tcp:22
```

### 2.3 DNS

En el panel de tu proveedor de DNS, creá dos registros `A`. El TTL puede ser de 300 segundos.

| Nombre | Tipo | Valor |
| --- | --- | --- |
| el valor de `$PBX_NOMBRE` | A | el valor de `$PBX_IP_PUBLICA` |
| el valor de `$TURN_NOMBRE` | A | el valor de `$TURN_IP_PUBLICA` |

Si el proveedor del trunk autentica por IP, mandale ahora `$PBX_IP_PUBLICA` para que la habilite.

**Comprobá:**

- `gcloud compute networks vpc-access connectors describe invoke-vpc --region=$REGION --format='value(state)'` responde `READY`.
- `gcloud compute firewall-rules list --filter=network:invoke-vpc` muestra las 6 reglas.
- `dig +short $PBX_NOMBRE` y `dig +short $TURN_NOMBRE` devuelven las IP públicas. Puede tardar unos minutos; hace falta para los pasos 8 y 9.

## Paso 3 — Secretos

**Dónde:** tu máquina.

Ninguna clave vive en el repositorio ni en una VM: todas están en Secret Manager. Las claves se generan en hexadecimal porque algunas van dentro de una URL de conexión, donde `/` o `+` la romperían.

```bash
source ~/invoke-despliegue.env
nuevo_secreto() { openssl rand -hex 24 | tr -d '\n' | gcloud secrets create "$1" --data-file=-; }

nuevo_secreto DB_CLAVE_POSTGRES    # usuario postgres de Cloud SQL (solo para administrar)
nuevo_secreto DB_CLAVE_API         # usuario invoke_api (Functions y Cloud Run)
nuevo_secreto DB_CLAVE_ASTERISK    # usuario asterisk_rt_login (PBX, solo lectura)
nuevo_secreto DB_CLAVE_TABLERO     # usuario invoke_tablero (tablero de productividad)
nuevo_secreto ARI_CLAVE            # usuario invoke de ARI (servicio ari ↔ PBX)
nuevo_secreto TURN_SECRETO         # secreto compartido entre la API y coturn

# La clave del trunk la da el proveedor: se escribe sin que quede en el historial
read -rsp 'Clave del trunk: ' T && printf '%s' "$T" | gcloud secrets create TRUNK_CLAVE --data-file=- ; unset T; echo
```

El secreto `DATABASE_URL` se crea en el paso 4, cuando se conoce la IP de la base.

**Comprobá:** `gcloud secrets list --format='value(name)'` muestra los 7 secretos.

## Paso 4 — Base de datos (Cloud SQL)

**Dónde:** tu máquina. La creación de la instancia tarda unos 10 minutos.

```bash
source ~/invoke-despliegue.env

# --edition=ENTERPRISE es obligatorio: con Postgres 16 el valor por defecto es Enterprise Plus,
# que no acepta el tipo de máquina db-custom-2-8192.
gcloud sql instances create invoke --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --tier=db-custom-2-8192 --region=$REGION \
  --network=projects/$PROYECTO/global/networks/invoke-vpc --no-assign-ip \
  --backup-start-time=07:00 --enable-point-in-time-recovery --ssl-mode=ENCRYPTED_ONLY \
  --root-password="$(gcloud secrets versions access latest --secret=DB_CLAVE_POSTGRES)"

gcloud sql databases create invoke --instance=invoke

# Guardar la IP privada de la base
echo "export DB_IP=$(gcloud sql instances describe invoke --format='value(ipAddresses[0].ipAddress)')" >> ~/invoke-despliegue.env
source ~/invoke-despliegue.env

# Cadena de conexión de la aplicación (usuario invoke_api, que se crea en el paso 7)
printf 'postgresql://invoke_api:%s@%s:5432/invoke' \
  "$(gcloud secrets versions access latest --secret=DB_CLAVE_API)" "$DB_IP" \
  | gcloud secrets create DATABASE_URL --data-file=-
```

El backup diario corre a las 07:00 UTC (04:00 en Montevideo), y además queda activa la recuperación a un punto en el tiempo.

**Comprobá:**

- `gcloud sql instances describe invoke --format='value(state)'` responde `RUNNABLE`.
- `echo $DB_IP` muestra una IP privada (empieza con `10.`).

## Paso 5 — Bucket de grabaciones y cuentas de servicio

**Dónde:** tu máquina.

### 5.1 Bucket

Se graban todas las llamadas de encuesta. El servicio `ari` baja cada grabación del PBX, la sube a este bucket y recién después la borra del PBX.

```bash
source ~/invoke-despliegue.env
gcloud storage buckets create gs://$PROYECTO-grabaciones --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
```

**Retención.** El plazo está pendiente de decisión (P-11 en `docs/decisiones.md`). Cuando se decida, se aplica como regla de ciclo de vida. Por ejemplo, para borrar a los 365 días:

```bash
echo '{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 365}}]}' > /tmp/retencion.json
gcloud storage buckets update gs://$PROYECTO-grabaciones --lifecycle-file=/tmp/retencion.json
```

### 5.2 Cuentas de servicio

| Cuenta | La usa | Puede |
| --- | --- | --- |
| `invoke-pbx` | VM del PBX | Leer `TRUNK_CLAVE`, `ARI_CLAVE` y `DB_CLAVE_ASTERISK` |
| `invoke-turn` | VM del TURN | Leer `TURN_SECRETO` |
| `invoke-run` | `ari`, `sala` y `pacer` en Cloud Run | Leer `DATABASE_URL` y `ARI_CLAVE`; subir grabaciones |
| Cuenta por defecto de Compute | Functions y Cloud Build | Leer `DATABASE_URL` y `TURN_SECRETO`; firmar URL de grabaciones; crear usuarios en Firebase Auth; construir imágenes |

```bash
source ~/invoke-despliegue.env
NUM=$(gcloud projects describe $PROYECTO --format='value(projectNumber)')
echo "export SA_COMPUTE=$NUM-compute@developer.gserviceaccount.com" >> ~/invoke-despliegue.env
source ~/invoke-despliegue.env

gcloud iam service-accounts create invoke-pbx --display-name="PBX de INVOKE 2"
gcloud iam service-accounts create invoke-turn --display-name="TURN de INVOKE 2"
gcloud iam service-accounts create invoke-run --display-name="Servicios de Cloud Run de INVOKE 2"
SA_PBX=invoke-pbx@$PROYECTO.iam.gserviceaccount.com
SA_TURN=invoke-turn@$PROYECTO.iam.gserviceaccount.com
SA_RUN=invoke-run@$PROYECTO.iam.gserviceaccount.com

leer_secreto() { gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:$2" \
                   --role=roles/secretmanager.secretAccessor --condition=None >/dev/null && echo "$2 lee $1"; }
for s in TRUNK_CLAVE ARI_CLAVE DB_CLAVE_ASTERISK; do leer_secreto $s $SA_PBX; done
leer_secreto TURN_SECRETO $SA_TURN
for s in DATABASE_URL ARI_CLAVE; do leer_secreto $s $SA_RUN; done
for s in DATABASE_URL TURN_SECRETO; do leer_secreto $s $SA_COMPUTE; done

# Grabaciones: ari sube; Functions lee para firmar la URL de escucha de la consola
gcloud storage buckets add-iam-policy-binding gs://$PROYECTO-grabaciones \
  --member=serviceAccount:$SA_RUN --role=roles/storage.objectCreator
gcloud storage buckets add-iam-policy-binding gs://$PROYECTO-grabaciones \
  --member=serviceAccount:$SA_COMPUTE --role=roles/storage.objectViewer
# Functions firma URL con su propia identidad
gcloud iam service-accounts add-iam-policy-binding $SA_COMPUTE \
  --member=serviceAccount:$SA_COMPUTE --role=roles/iam.serviceAccountTokenCreator
# Functions crea los usuarios en Firebase Auth (Administración → Usuarios); Cloud Build construye imágenes
for r in roles/firebaseauth.admin roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding $PROYECTO --member=serviceAccount:$SA_COMPUTE --role=$r --condition=None >/dev/null && echo "$r"
done
```

**Comprobá:** `gcloud iam service-accounts list --format='value(email)'` muestra `invoke-pbx`, `invoke-turn` e `invoke-run`, y cada comando `leer_secreto` imprimió su línea.

## Paso 6 — VM del PBX: crearla y prepararla

### 6.1 Crear la VM

**Dónde:** tu máquina.

```bash
source ~/invoke-despliegue.env
gcloud compute instances create pbx --zone=$ZONA --machine-type=e2-standard-2 \
  --network-interface=subnet=invoke-sub,private-network-ip=$PBX_IP_PRIVADA,address=pbx-ip \
  --tags=pbx --image-family=ubuntu-2604-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB --boot-disk-type=pd-balanced \
  --service-account=invoke-pbx@$PROYECTO.iam.gserviceaccount.com --scopes=cloud-platform
```

Si la familia de imagen no existe con ese nombre, `gcloud compute images list --project=ubuntu-os-cloud --filter="family~2604"` muestra la vigente.

### 6.2 Entrar y preparar el sistema

**Dónde:** tu máquina para entrar; el resto, en la VM `pbx`.

```bash
gcloud compute ssh pbx --zone=$ZONA --tunnel-through-iap
```

Ya en la VM:

```bash
sudo apt-get update && sudo apt-get install -y docker.io git certbot
sudo install -d -m 0750 /etc/invoke /etc/invoke/keys /etc/invoke/sonidos
sudo install -d /var/lib/invoke/grabaciones
```

### 6.3 Traer el repositorio

El repositorio es privado: la VM lo clona con una *deploy key* de solo lectura.

```bash
sudo ssh-keygen -t ed25519 -N '' -C invoke-pbx -f /root/.ssh/invoke_despliegue
sudo cat /root/.ssh/invoke_despliegue.pub
```

Copiá la línea que imprime y, en GitHub, agregala en el repositorio: **Settings → Deploy keys → Add deploy key**, con el título `pbx` y **sin** marcar *Allow write access*. Después, en la VM:

```bash
sudo GIT_SSH_COMMAND='ssh -i /root/.ssh/invoke_despliegue -o StrictHostKeyChecking=accept-new' \
  git clone git@github.com:araujomelogno/invoke2.git /opt/invoke
sudo git -C /opt/invoke config core.sshCommand 'ssh -i /root/.ssh/invoke_despliegue'
sudo git -C /opt/invoke checkout <la misma etiqueta o commit del paso 0.4>
```

**Comprobá (en la VM):**

- `sudo docker run --rm hello-world` imprime `Hello from Docker!`.
- `sudo gcloud secrets versions access latest --secret=ARI_CLAVE >/dev/null && echo "la VM lee sus secretos"`. Si falla, revisá la cuenta de servicio y el alcance `cloud-platform` de 6.1.
- `ls /opt/invoke/infra/asterisk` muestra los archivos del PBX.

## Paso 7 — Esquema y usuarios de la base

**Dónde:** la VM `pbx`. La base solo tiene IP privada, y la VM está dentro de la VPC.

Para leer las claves de administración, iniciá sesión en `gcloud` **con tu usuario** (no con el de la VM). La sesión queda en tu usuario de Linux y no la usa el servicio del PBX, que corre como root:

```bash
gcloud auth login --no-launch-browser      # seguí el enlace y pegá el código
DB_IP=<la IP del paso 4: echo $DB_IP en tu máquina>
cd /opt/invoke
```

### 7.1 Aplicar las migraciones

Se corren dentro de un contenedor de Python 3.12, para no instalar nada en la VM:

```bash
ADMIN_DSN="postgresql://postgres:$(gcloud secrets versions access latest --secret=DB_CLAVE_POSTGRES)@$DB_IP:5432/invoke"
sudo docker run --rm --network host -v /opt/invoke:/app:ro -w /app \
  -e DATABASE_URL="$ADMIN_DSN" -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
  sh -c 'pip install -q -r requirements.txt && python scripts/migrar.py --verificar'
```

**Comprobá:** la salida lista `aplicada 0001_…` hasta la última migración y termina con `accesos verificados`.

### 7.2 Crear los usuarios con login

Se crean una sola vez, con las claves del paso 3:

```bash
sudo docker run --rm -i --network host postgres:16 psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 \
  -v clave_api="'$(gcloud secrets versions access latest --secret=DB_CLAVE_API)'" \
  -v clave_ast="'$(gcloud secrets versions access latest --secret=DB_CLAVE_ASTERISK)'" \
  -v clave_tab="'$(gcloud secrets versions access latest --secret=DB_CLAVE_TABLERO)'" \
  < db/roles_produccion.sql
```

| Usuario | Rol | Lo usa |
| --- | --- | --- |
| `invoke_api` | `invoke_app` | Functions y los tres servicios de Cloud Run |
| `asterisk_rt_login` | `asterisk_rt` | Asterisk: solo lee los endpoints de los puestos |
| `invoke_tablero` | `invoke_lectura` | Tablero de productividad (vista `cdr_compat`) |

La aplicación nunca se conecta como `postgres`: las invariantes dependen de que `invoke_app` no pueda escribir proyecciones ni modificar hechos.

**Comprobá:**

```bash
sudo docker run --rm --network host postgres:16 psql \
  "postgresql://asterisk_rt_login:$(gcloud secrets versions access latest --secret=DB_CLAVE_ASTERISK)@$DB_IP:5432/invoke" \
  -c "SELECT count(*) FROM asterisk.ps_endpoints"
```

Tiene que responder `0` (todavía no hay puestos). Dejá la sesión de `gcloud` abierta: se usa de nuevo en el paso 13.

## Paso 8 — PBX Asterisk

### 8.1 Configuración

**Dónde:** tu máquina. El archivo se arma con los valores ya cargados, para no tipearlos a mano en la VM, y se copia a la VM:

```bash
source ~/invoke-despliegue.env
cat > /tmp/pbx.conf <<EOF
# Valores NO secretos del PBX (los secretos vienen de Secret Manager).
INVOKE_PBX_DOMINIO=$PBX_NOMBRE
PBX_IP_PUBLICA=$PBX_IP_PUBLICA
PBX_IP_PRIVADA=$PBX_IP_PRIVADA
TRUNK_HOST=$TRUNK_HOST
TRUNK_IP=$TRUNK_IP
TRUNK_DOMINIO=$TRUNK_DOMINIO
TRUNK_USUARIO=$TRUNK_USUARIO
DB_HOST=$DB_IP
DB_PUERTO=5432
DB_NOMBRE=invoke
EOF
cat /tmp/pbx.conf                                   # revisalo antes de copiarlo
gcloud compute scp /tmp/pbx.conf pbx:/tmp/pbx.conf --zone=$ZONA --tunnel-through-iap
gcloud compute scp invoke-disculpas.wav pbx:/tmp/invoke-disculpas.wav --zone=$ZONA --tunnel-through-iap
```

`invoke-disculpas.wav` tiene que ser WAV PCM de 16 bits, 8 kHz y mono. Si la grabación viene en otro formato: `sox grabacion.wav -r 8000 -c 1 -b 16 invoke-disculpas.wav`.

**Dónde:** la VM `pbx`.

```bash
sudo install -m 0640 /tmp/pbx.conf /etc/invoke/pbx.conf
sudo install -m 0644 /tmp/invoke-disculpas.wav /etc/invoke/sonidos/invoke-disculpas.wav
rm /tmp/pbx.conf /tmp/invoke-disculpas.wav
```

### 8.2 Solo si el proveedor exige registro

Si el trunk autentica por IP, saltá este punto. Si exige registro, en `/opt/invoke/infra/asterisk/pjsip.conf` descomentá el bloque `[trunk-registro]` (las líneas que empiezan con `;` debajo de «Si el proveedor exige registro»). Conviene hacer ese cambio en el repositorio y desplegarlo como una versión, no editarlo solo en la VM.

### 8.3 Certificado TLS

**Dónde:** la VM `pbx`. El DNS de 2.3 ya tiene que resolver.

```bash
INVOKE_PBX_DOMINIO=$(sudo sed -n 's/^INVOKE_PBX_DOMINIO=//p' /etc/invoke/pbx.conf)
sudo certbot certonly --standalone -d $INVOKE_PBX_DOMINIO --non-interactive --agree-tos \
  -m <correo de sistemas> --deploy-hook /opt/invoke/infra/asterisk/renovar_certificado.sh
```

certbot deja programada la renovación automática. Cuando renueva, el *hook* copia el certificado nuevo y reinicia Asterisk recién cuando no hay canales; en la práctica, al terminar el turno.

**Comprobá:** `sudo ls -l /etc/invoke/keys/` muestra `pbx.crt` y `pbx.key`.

### 8.4 Imagen y servicio

**Dónde:** la VM `pbx`. Construir la imagen tarda unos minutos.

```bash
cd /opt/invoke
sudo docker build -t invoke-pbx:actual infra/asterisk
sudo install -m 0644 infra/asterisk/invoke-pbx.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now invoke-pbx
sudo journalctl -u invoke-pbx -n 50 --no-pager
```

Al arrancar, el servicio arma `/run/invoke-pbx.env` con la configuración y los secretos, recrea el contenedor, y el contenedor completa la configuración, instala el certificado y el mensaje de disculpas y arranca Asterisk como usuario `asterisk`. Si falta algo, el log lo dice (`Faltan variables de entorno: …`, `Falta /etc/invoke/keys/pbx.crt`, `Falta el mensaje de disculpas`).

### 8.5 Comprobá

**Dónde:** la VM `pbx`.

```bash
A="sudo docker exec invoke-pbx asterisk -rx"
$A "core show version"                     # Asterisk 22.x
$A "pjsip show transports"                 # transport-udp y transport-wss
$A "pjsip show contacts"                   # el contacto del trunk en estado Avail
$A "pjsip show registrations"              # solo con registro (8.2): Registered
$A "realtime show pgsql status"            # conectado a la base
$A "http show status"                      # 8088 en la IP privada y 8089 con TLS
sudo ss -lntup | grep asterisk             # solo 5060/udp, 10.10.0.10:8088 y *:8089 (más RTP)
```

Desde tu máquina:

```bash
openssl s_client -connect $PBX_NOMBRE:8089 -servername $PBX_NOMBRE </dev/null 2>/dev/null | grep 'Verify return code'
# Tiene que decir 0 (ok).
curl -m 5 http://$PBX_IP_PUBLICA:8088/ari/asterisk/info
# Tiene que FALLAR por tiempo de espera: ARI nunca se publica.
```

Si el trunk aparece `Unavailable`, revisá con el proveedor que tenga habilitada `$PBX_IP_PUBLICA` y la regla `invoke-pbx-sip`. El servicio `ari` todavía no existe: `ari show apps` va a estar vacío hasta el paso 11.

**Recomendado antes de seguir:** correr la prueba de laboratorio (apéndice A) contra la misma imagen. No toca la base ni el PBX de producción.

## Paso 9 — TURN

El TURN es el camino de audio para los puestos cuya red no deja llegar directo al PBX. Va en una VM aparte (`docs/decisiones.md`, ADR-005).

### 9.1 Crear la VM

**Dónde:** tu máquina.

```bash
source ~/invoke-despliegue.env
gcloud compute instances create turn --zone=$ZONA --machine-type=e2-small \
  --network-interface=subnet=invoke-sub,address=turn-ip --tags=turn \
  --image-family=ubuntu-2604-lts-amd64 --image-project=ubuntu-os-cloud \
  --service-account=invoke-turn@$PROYECTO.iam.gserviceaccount.com --scopes=cloud-platform
gcloud compute scp infra/coturn/turnserver.conf turn:/tmp/turnserver.conf --zone=$ZONA --tunnel-through-iap
echo "TURN_NOMBRE=$TURN_NOMBRE  PBX_IP_PUBLICA=$PBX_IP_PUBLICA"      # los vas a necesitar en la VM
gcloud compute ssh turn --zone=$ZONA --tunnel-through-iap
```

### 9.2 Instalar coturn

**Dónde:** la VM `turn`. Reemplazá los dos valores de la primera línea por los que imprimió el `echo` anterior.

```bash
TURN_NOMBRE=<nombre del TURN>; PBX_IP_PUBLICA=<IP pública del PBX>
sudo apt-get update && sudo apt-get install -y docker.io certbot gettext-base
sudo certbot certonly --standalone -d $TURN_NOMBRE --non-interactive --agree-tos -m <correo de sistemas>

# Instala el certificado para coturn y lo reinstala en cada renovación
sudo tee /etc/letsencrypt/renewal-hooks/deploy/coturn.sh >/dev/null <<EOF
#!/usr/bin/env bash
install -d /etc/coturn/certs
install -m 0644 /etc/letsencrypt/live/$TURN_NOMBRE/fullchain.pem /etc/coturn/certs/turn.crt
install -m 0600 /etc/letsencrypt/live/$TURN_NOMBRE/privkey.pem /etc/coturn/certs/turn.key
chown -R 65534:65534 /etc/coturn/certs        # coturn corre como nobody dentro del contenedor
docker restart turn 2>/dev/null || true
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/coturn.sh
sudo /etc/letsencrypt/renewal-hooks/deploy/coturn.sh

# Configuración: el secreto se lee de Secret Manager y no queda en el historial
export TURN_NOMBRE PBX_IP_PUBLICA
export TURN_SECRETO="$(sudo gcloud secrets versions access latest --secret=TURN_SECRETO)"
envsubst '${TURN_SECRETO} ${PBX_IP_PUBLICA} ${TURN_NOMBRE}' < /tmp/turnserver.conf | sudo tee /etc/coturn/turnserver.conf >/dev/null
unset TURN_SECRETO; rm /tmp/turnserver.conf
sudo chown 65534:65534 /etc/coturn/turnserver.conf && sudo chmod 0600 /etc/coturn/turnserver.conf

sudo docker run -d --name turn --network host --restart always \
  -v /etc/coturn/turnserver.conf:/etc/coturn/turnserver.conf:ro -v /etc/coturn/certs:/etc/coturn/certs:ro \
  coturn/coturn:4.6 -c /etc/coturn/turnserver.conf
```

El relay solo acepta como destino la IP pública del PBX: no es un proxy abierto.

**Comprobá (en la VM `turn`):**

- `sudo docker logs turn 2>&1 | tail -5` no muestra errores.
- `sudo ss -lntup | grep -E '3478|5349'` muestra coturn escuchando.
- `sudo grep -c '\${' /etc/coturn/turnserver.conf` responde `0`: no quedó ninguna variable sin completar.

La prueba real del TURN es el paso 14, con un puesto en una red que no llega directo.

## Paso 10 — Firebase: autenticación y app web

### 10.1 Habilitar el inicio de sesión

**Dónde:** consola de Firebase.

1. Abrí el proyecto en <https://console.firebase.google.com>.
2. **Authentication → Comenzar → Sign-in method → Correo electrónico/contraseña → Habilitar → Guardar.**
3. En **Authentication → Configuración → Dominios autorizados**, tienen que figurar `<proyecto>.web.app` y `<proyecto>.firebaseapp.com`. Si vas a usar un dominio propio para la web, agregalo acá.

### 10.2 Registrar la app web

**Dónde:** tu máquina.

```bash
source ~/invoke-despliegue.env
firebase apps:create WEB "INVOKE 2" --project $PROYECTO
firebase apps:sdkconfig WEB --project $PROYECTO
```

Guardá los valores `apiKey`, `authDomain`, `projectId` y `appId` que imprime el último comando: van en `web/js/config.js` en el paso 12. No son secretos (identifican el proyecto).

## Paso 11 — Servicios de Cloud Run

**Dónde:** tu máquina, en la raíz del repositorio.

### 11.1 Construir las imágenes

Cloud Build construye las tres imágenes (`infra/cloudrun/cloudbuild.yaml`). La etiqueta es el commit, así cada versión queda identificada y se puede volver atrás.

```bash
source ~/invoke-despliegue.env
gcloud artifacts repositories create invoke --repository-format=docker --location=$REGION
ETIQUETA=$(git rev-parse --short HEAD)
gcloud builds submit --config infra/cloudrun/cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_ETIQUETA=$ETIQUETA .
```

**Comprobá:** `gcloud artifacts docker images list $REGION-docker.pkg.dev/$PROYECTO/invoke --include-tags` muestra `ari`, `sala` y `pacer` con la etiqueta `$ETIQUETA`.

### 11.2 Desplegar

```bash
source ~/invoke-despliegue.env
ETIQUETA=$(git rev-parse --short HEAD)
IMG=$REGION-docker.pkg.dev/$PROYECTO/invoke
COMUN=(--region=$REGION --service-account=invoke-run@$PROYECTO.iam.gserviceaccount.com
       --vpc-connector=invoke-vpc --vpc-egress=private-ranges-only)

# ari: el único que le habla a Asterisk. Exactamente una instancia, sin acceso desde internet.
gcloud run deploy ari --image=$IMG/ari:$ETIQUETA "${COMUN[@]}" \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --ingress=internal --no-allow-unauthenticated \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest,ARI_CLAVE=ARI_CLAVE:latest \
  --set-env-vars="ARI_URL=http://$PBX_IP_PRIVADA:8088/ari,INVOKE_TRUNK=$INVOKE_TRUNK,INVOKE_CALLER_ID=$INVOKE_CALLER_ID,GRABACIONES_BUCKET=$PROYECTO-grabaciones"

# pacer: discador de power y predictivo. Exactamente una instancia.
gcloud run deploy pacer --image=$IMG/pacer:$ETIQUETA "${COMUN[@]}" \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --ingress=internal --no-allow-unauthenticated \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest

# sala: WebSocket del puesto y de la consola. Pública, pero exige el token de Firebase.
gcloud run deploy sala --image=$IMG/sala:$ETIQUETA "${COMUN[@]}" \
  --min-instances=1 --max-instances=3 --timeout=3600 --session-affinity --allow-unauthenticated \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest --set-env-vars=FIREBASE_PROJECT=$PROYECTO

echo "export SALA_URL=$(gcloud run services describe sala --region=$REGION --format='value(status.url)')" >> ~/invoke-despliegue.env
source ~/invoke-despliegue.env
echo "$SALA_URL"
```

La sala es pública porque el navegador se conecta directo, pero el primer mensaje de cada WebSocket trae el token de Firebase, que se verifica antes de suscribir: sin token válido cierra la conexión con el código 4401.

**Comprobá:**

- `curl -s $SALA_URL/salud` responde.
- `gcloud run services logs read ari --region=$REGION --limit=30` muestra `conectado a ARI`.
- En la VM `pbx`: `sudo docker exec invoke-pbx asterisk -rx "ari show apps"` muestra `invoke`.
- Los logs de `pacer` no muestran errores de conexión a la base.

## Paso 12 — Functions y Hosting

**Dónde:** tu máquina, en la raíz del repositorio.

### 12.1 Configuración de la web

Editá `web/js/config.js` con los valores de 10.2 y la URL de la sala, cambiando `https://` por `wss://` y agregando `/sala` al final:

```js
window.INVOKE_CONFIG = window.INVOKE_CONFIG || {
  firebase: {
    apiKey: '<apiKey de 10.2>',
    authDomain: '<proyecto>.firebaseapp.com',
    projectId: '<proyecto>',
    appId: '<appId de 10.2>',
  },
  salaWs: 'wss://sala-xxxxxxxx-rj.a.run.app/sala',
  zonaHoraria: 'America/Montevideo',
};
```

Este archivo es propio de cada entorno: no lo subas al repositorio con los valores de producción.

### 12.2 Variables de la API

```bash
source ~/invoke-despliegue.env
cat > functions/.env.$PROYECTO <<EOF
INVOKE_DB_POOL=10
INVOKE_DB_ESPERA_S=8
SIP_WSS=wss://$PBX_NOMBRE:8089/ws
SIP_DOMINIO=$PBX_NOMBRE
TURN_URLS=turn:$TURN_NOMBRE:3478?transport=udp,turns:$TURN_NOMBRE:5349?transport=tcp
STUN_URLS=stun:$TURN_NOMBRE:3478
EOF
```

`INVOKE_DB_POOL` coincide con la concurrencia de `functions/main.py`. **`INVOKE_AUTH_DEV` no se define nunca en un despliegue**: deja entrar sin contraseña. Los secretos (`DATABASE_URL`, `TURN_SECRETO`) no van en este archivo: la función los toma de Secret Manager.

### 12.3 Desplegar

Firebase exige un entorno virtual de Python con las dependencias de la función instaladas:

```bash
source ~/invoke-despliegue.env
python3.12 -m venv functions/venv
functions/venv/bin/pip install -r functions/requirements.txt
echo '{"projects":{"default":"'$PROYECTO'"}}' > .firebaserc

firebase deploy --only functions:invoke      # copia invoke/ y schemas/ dentro de functions/ antes de subir
firebase deploy --only hosting
```

**Comprobá:**

- `curl -s https://$PROYECTO.web.app/api/salud` responde `{"ok": true}`.
- `https://<proyecto>.web.app` abre la pantalla de inicio de sesión.

## Paso 13 — Primer administrador

1. **Consola de Firebase → Authentication → Usuarios → Agregar usuario**: el correo de quien coordina y una contraseña. Copiá el **UID** que aparece en la lista.
2. **En la VM `pbx`**, con la sesión de `gcloud` del paso 7, dalo de alta en la base:

```bash
DB_IP=<la IP del paso 4>
ADMIN_DSN="postgresql://postgres:$(gcloud secrets versions access latest --secret=DB_CLAVE_POSTGRES)@$DB_IP:5432/invoke"
sudo docker run --rm --network host postgres:16 psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -c \
  "INSERT INTO invoke.usuario (uid, email, nombre, roles)
   VALUES ('<UID>', '<correo>', 'Coordinación', ARRAY['administrador','investigador','supervisor'])"
gcloud auth revoke --all        # cerrá tu sesión en la VM: ya no hace falta
```

Desde ahí, el resto de los usuarios se crean desde la web, en **Administración → Usuarios**. La API los da de alta también en Firebase Auth con una clave inicial.

**Comprobá:** al entrar a `https://<proyecto>.web.app` con ese usuario, aparece el menú de administración.

## Paso 14 — Verificación de punta a punta

Con un encuestador de prueba (creado en **Administración → Usuarios**) y un teléfono propio a mano:

1. Como administrador, creá un estudio marcado como **prueba o capacitación**, importá el cuestionario de ejemplo (`schemas/ejemplos/satisfaccion_clientes.json`, con **Importar JSON** del editor), publicalo y cargá una guía con tu número.
2. Como encuestador, hacé la prueba de audio: la barra superior tiene que pasar a **Audio OK**.
3. Pedí el caso, llamá, atendé y comprobá que hay audio en las dos direcciones.
4. Desde la consola de supervisión, conectá el audio de escucha y escuchá la llamada. La calidad de red del puesto aparece en su tarjeta.
5. Clasificá el intento y abrí **Reportes**: tiene que figurar. Un estudio de prueba no da ficha técnica.
6. `gcloud run services logs read ari --region=$REGION --limit=50` no muestra comandos en error.
7. A los pocos minutos, la grabación está en el bucket: `gcloud storage ls gs://$PROYECTO-grabaciones/** | tail -1`.
8. Repetí el punto 3 desde una red distinta (por ejemplo, una red doméstica o la de un celular compartido). Si fue por relay, la tarjeta del puesto dice «TURN».
9. Abandono: en un estudio en power sin encuestadores libres, la persona oye el mensaje de disculpas completo antes del corte.
10. Códec: con el encuestador sin Opus, la tarjeta dice PCMA o PCMU, y en el PBX `sudo docker exec invoke-pbx asterisk -rx "core show channels verbose"` no muestra transcodificación. Repetí con un usuario con Opus habilitado y verificá que ahí sí aparece.

Si los diez puntos dan bien, el despliegue está completo.

## Actualizar una versión

En este orden, **fuera del horario de campo**:

1. **Base.** En la VM `pbx`: `sudo git -C /opt/invoke fetch && sudo git -C /opt/invoke checkout <versión>`, y las migraciones como en 7.1 (con `gcloud auth login` como al principio del paso 7, y `gcloud auth revoke --all` al terminar). Son aditivas: el código anterior sigue funcionando con el esquema nuevo.
2. **Functions.** En tu máquina, con el repositorio en la misma versión: `firebase deploy --only functions:invoke`.
3. **Cloud Run.** 11.1 y 11.2 con la etiqueta nueva, en el orden `sala`, `pacer` y por último `ari`. Al reiniciar, `ari` recupera los puestos desde la base y los navegadores reconectan solos. Para volver atrás: `gcloud run services update-traffic <servicio> --region=$REGION --to-revisions=<revisión anterior>=100`.
4. **Hosting.** `firebase deploy --only hosting`, con `web/js/config.js` del entorno.
5. **PBX**, solo si cambió `infra/asterisk`: prueba de laboratorio (apéndice A), y después en la VM `pbx` `cd /opt/invoke && sudo docker build -t invoke-pbx:actual infra/asterisk && sudo systemctl restart invoke-pbx`. Reiniciar corta las llamadas en curso y las patas de los puestos (los navegadores reconectan solos).

## Operar

### Qué mirar todos los días

| Indicador | Cómo | Si da mal |
| --- | --- | --- |
| Comandos de telefonía en error | `SELECT * FROM invoke.comando_telefonia WHERE estado = 'error'` | Revisar el trunk o ARI |
| Grabaciones sin subir | `SELECT count(*) FROM invoke.grabacion_pendiente` | Si crece, la subida falla y el disco del PBX se llena: revisar los logs de `ari` |
| Disco del PBX | En la VM: `df -h /var/lib/invoke/grabaciones` | Arriba del 70 %: ver la fila anterior |
| Trunk | En la VM: `sudo docker exec invoke-pbx asterisk -rx "pjsip show contacts"` | `Unavailable`: no sale ninguna llamada. Hablar con el proveedor |
| CPU del PBX | Consola de Compute Engine | Arriba del 60 % sostenido: cada puesto con Opus transcodifica |
| Puestos en Opus | Consola de supervisión: «N en Opus · Asterisk transcodifica» | Cada uno es transcodificación permanente |
| Proporción por TURN | `avg(relay::int)` sobre `invoke.calidad_red` del día | Insumo de ADR-005 |
| Abandono real | Consola → indicadores del discador | Arriba del objetivo, bajar N |

Una grabación ocupa unos 0,96 MB por minuto. Con 30 llamadas conversando todo el tiempo son unos 1,7 GB por hora; con 50 GB de disco hay un día de margen si la subida se corta. Conviene una alerta de Cloud Monitoring sobre disco y CPU de las dos VM (agente de operaciones de GCE).

### Tareas periódicas

| Tarea | Cuándo | Cómo |
| --- | --- | --- |
| Parches de Asterisk | Una vez por mes y ante cada aviso de seguridad del paquete `asterisk` de Ubuntu | En la VM `pbx`: `cd /opt/invoke && sudo docker build --pull --no-cache -t invoke-pbx:actual infra/asterisk`, prueba de laboratorio, y `sudo systemctl restart invoke-pbx` fuera del horario de campo. `core show version` dice cuál quedó |
| Parches del sistema de las VM | Automático | `unattended-upgrades` de Ubuntu viene activo |
| Certificados | Automático | certbot renueva 30 días antes del vencimiento. Comprobar con `sudo certbot certificates` |
| Copias de la base | Automático | Backup diario y recuperación a un punto en el tiempo (paso 4). Como el log de eventos es inmutable, recuperar a un instante reproduce exactamente los reportes de ese instante |

### Rotar una clave

| Clave | Pasos |
| --- | --- |
| `ARI_CLAVE` | Versión nueva (`openssl rand -hex 24 \| tr -d '\n' \| gcloud secrets versions add ARI_CLAVE --data-file=-`), en la VM `sudo systemctl restart invoke-pbx`, y redesplegar `ari` (11.2) |
| `TURN_SECRETO` | Versión nueva, rehacer la configuración de 9.2 y `docker restart turn`, y redesplegar Functions |
| `TRUNK_CLAVE` | Versión nueva y `sudo systemctl restart invoke-pbx` |
| `DB_CLAVE_ASTERISK` | Versión nueva, `ALTER ROLE asterisk_rt_login PASSWORD '…'` como `postgres`, y `sudo systemctl restart invoke-pbx` |
| `DB_CLAVE_API` | Versión nueva, `ALTER ROLE invoke_api PASSWORD '…'`, versión nueva de `DATABASE_URL` (paso 4), y redesplegar Functions y los tres servicios |

Las credenciales SIP de los puestos se rotan solas en cada inicio de sesión.

### Herramientas del PBX

| Para | Comando (en la VM `pbx`) |
| --- | --- |
| Ver el log | `sudo journalctl -u invoke-pbx -f` |
| Abrir la consola de Asterisk | `sudo docker exec -it invoke-pbx asterisk -r` |
| Ver llamadas en curso | `sudo docker exec invoke-pbx asterisk -rx "core show channels"` |
| Reiniciar sin cortar llamadas | `sudo docker exec invoke-pbx asterisk -rx "core restart when convenient"` |

## Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
| --- | --- | --- |
| `gcloud sql instances create` falla con el tipo de máquina | Falta `--edition=ENTERPRISE` | Usar el comando del paso 4 tal cual |
| El servicio del PBX no arranca y el log dice `No se encuentra gcloud` | La imagen de la VM no trae `gcloud` | Instalarlo: `sudo snap install google-cloud-cli --classic` |
| `Faltan variables de entorno` en el log del PBX | `/etc/invoke/pbx.conf` incompleto | Rehacer 8.1 |
| `PERMISSION_DENIED` al leer un secreto en la VM | Cuenta de servicio sin acceso, o la VM creada sin `--scopes=cloud-platform` | Revisar 5.2 y 6.1 |
| certbot no obtiene el certificado | El DNS todavía no resuelve o falta la regla `invoke-acme` | `dig +short $PBX_NOMBRE`; esperar y reintentar |
| Trunk `Unavailable` | El proveedor no habilitó la IP, o `TRUNK_HOST`/`TRUNK_IP` mal | Revisar con el proveedor; `sudo docker exec invoke-pbx asterisk -rx "pjsip set logger on"` muestra el tráfico SIP en el log |
| `ari show apps` vacío | `ari` no llega al PBX | Logs de `ari`; revisar la regla `invoke-ari`, el conector y `ARI_URL` |
| `--allow-unauthenticated` rechazado al desplegar `sala` | Política de la organización que restringe el acceso público | Pedir una excepción para el proyecto (`iam.allowedPolicyMemberDomains`) |
| La web no inicia sesión | `web/js/config.js` sin completar, o dominio no autorizado | Revisar 10.1 y 12.1 |
| El puesto dice «usuario no habilitado en INVOKE 2» | Existe en Firebase pero no en `invoke.usuario` | Darlo de alta desde Administración → Usuarios |
| Audio en un solo sentido | IP pública mal declarada o RTP bloqueado | `PBX_IP_PUBLICA` en `/etc/invoke/pbx.conf` y la regla `invoke-pbx-medios` |
| No hay audio desde algunas redes | El TURN no está funcionando | Logs de `turn` y `TURN_URLS` en `functions/.env.<proyecto>` |
| Las grabaciones no llegan al bucket | Falta `GRABACIONES_BUCKET` en `ari` o el permiso de 5.2 | Logs de `ari` |

## Apéndice A — PBX de laboratorio

Antes del primer despliegue, y después de cada cambio en `infra/asterisk` o en `services/ari`, se corre una prueba de punta a punta contra un Asterisk real con la misma imagen. La red telefónica y el navegador se simulan con canales Local (`infra/asterisk/laboratorio/extensions_laboratorio.conf`): no hace falta trunk ni navegador.

| Número simulado | Comportamiento |
| --- | --- |
| `0990000001` | Persona: timbra, atiende y habla |
| `0990000002` | Ocupado (causa 17) |
| `0990000003` | No contesta |
| `0990000004` | Contestador: atiende y reproduce un mensaje largo (AMD → máquina) |
| cualquier otro | Número inexistente (causa 1) |

`scripts/probar_pbx.py` recorre pata permanente del puesto, preview (timbre, atención, puente, grabación, escucha del supervisor, corte y clasificación), ocupado, no contesta, inexistente, power con persona, power sin encuestadores (abandono con el mensaje de disculpas completo), contestador, subida de la grabación, endpoints realtime con sus códecs y reinicio de `ari` con una llamada en curso. Resultado esperado: `44 de 44 comprobaciones bien`.

**Dónde:** una VM aparte (`pbx-lab`, e2-small alcanza) o cualquier máquina Linux con Docker y Python 3.12, en la raíz del repositorio. **Nunca contra la base de producción**: la prueba crea estudios y usuarios `lab-…`.

```bash
# 1. Base descartable
docker run -d --name pg-lab --network host -e POSTGRES_PASSWORD=lab postgres:16
export DATABASE_URL=postgresql://postgres:lab@127.0.0.1:5432/postgres
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/migrar.py --verificar
docker exec -i pg-lab psql -U postgres -v clave_api="'lab'" -v clave_ast="'lab'" -v clave_tab="'lab'" < db/roles_produccion.sql

# 2. Certificado autofirmado (en el laboratorio no hay navegador)
mkdir -p lab/keys
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=pbx-lab \
  -keyout lab/keys/pbx.key -out lab/keys/pbx.crt

# 3. PBX con la misma imagen y el dialplan de laboratorio
IP=$(hostname -I | awk '{print $1}')
cat > lab/pbx.env <<EOF
INVOKE_PBX_LABORATORIO=1
PBX_IP_PUBLICA=$IP
PBX_IP_PRIVADA=$IP
TRUNK_HOST=127.0.0.1:5999
TRUNK_IP=127.0.0.2
TRUNK_DOMINIO=lab.invalid
TRUNK_USUARIO=lab
TRUNK_CLAVE=lab
ARI_CLAVE=lab
DB_HOST=127.0.0.1
DB_PUERTO=5432
DB_NOMBRE=postgres
DB_CLAVE_ASTERISK=lab
EOF
docker build -t invoke-pbx:actual infra/asterisk
docker run -d --name invoke-pbx-lab --network host --env-file lab/pbx.env \
  -v $PWD/lab/keys:/run/invoke/keys:ro invoke-pbx:actual

# 4. Servicio ari apuntando a la red simulada, y la prueba
export ARI_URL=http://$IP:8088/ari ARI_USUARIO=invoke ARI_CLAVE=lab \
  INVOKE_TRUNK='Local/{numero}@invoke-lab-pstn'
PORT=8790 .venv/bin/python services/ari/main.py > lab/ari.log 2>&1 & echo $! > lab/ari.pid
LAB_ASTERISK_CLI="docker exec invoke-pbx-lab asterisk" \
LAB_REINICIO_ARI="kill \$(cat lab/ari.pid); sleep 1; PORT=8790 .venv/bin/python services/ari/main.py >> lab/ari.log 2>&1 & echo \$! > lab/ari.pid" \
  .venv/bin/python scripts/probar_pbx.py

# 5. Limpiar
kill $(cat lab/ari.pid); docker rm -f invoke-pbx-lab pg-lab; rm -rf lab
```

Lo esperado y que no es error:

- El trunk del laboratorio apunta a un puerto donde no hay nadie: `pjsip show contacts` lo muestra `Unavailable`.
- En `lab/ari.log`, el aviso de que `GRABACIONES_BUCKET` no está configurado (la prueba sube a un bucket local).
- En el log de Asterisk, `No audio available on Recorder`: las llamadas simuladas no tienen audio real, y la grabación se registra y se sube igual.

**Qué no cubre el laboratorio:** el trunk del proveedor, el audio real del navegador (SIP.js, ICE, DTLS-SRTP, TURN) y la negociación de Opus. Eso se verifica en el paso 14.

## Apéndice B — Qué se despliega y por qué

El detalle está en `docs/decisiones.md` (ADR-003, ADR-005, ADR-011 y ADR-013). En resumen:

| Elección | Valor | Por qué |
| --- | --- | --- |
| Versión del PBX | Asterisk 22 LTS del paquete de Ubuntu 26.04 LTS | LTS con soporte hasta 2029; el paquete trae el Opus de código abierto. Validado con la prueba de laboratorio |
| Empaquetado del PBX | Imagen Docker propia, red del host, bajo systemd | La configuración viaja con el repositorio; la red del host evita el NAT de Docker sobre 10.000 puertos RTP |
| Control del PBX | Solo ARI; AMI, CDR y CEL apagados | ADR-003; el registro de llamadas es `invoke.evento` |
| Endpoints de los puestos | Realtime desde Postgres, con un usuario de solo lectura | La API rota la credencial SIP en cada sesión sin recargar Asterisk |
| ARI | Solo en la IP privada | Nunca expuesto a internet |
| NAT de medios | IP pública declarada y candidato ICE mapeado | GCE hace NAT 1:1; no hace falta STUN en el PBX |
| Códec | G.711 por defecto; Opus solo para quien lo tenga habilitado | ADR-004: sin transcodificación salvo excepción visible |
| Secretos | Secret Manager, leídos al arrancar | No quedan en el disco ni en las imágenes |
| TURN | VM propia | El relay hacia la IP pública del PBX desde la misma VM no está garantizado en GCE |
| `ari` y `pacer` | Exactamente una instancia | Son consumidores únicos |

Archivos de `infra/asterisk`:

| Archivo | Para qué |
| --- | --- |
| `*.conf` | Plantillas de configuración de Asterisk; `entrypoint.sh` las completa al arrancar |
| `Dockerfile`, `entrypoint.sh`, `instalar_archivos.sh` | Imagen y arranque del contenedor |
| `invoke-pbx.service` | Unidad de systemd |
| `cargar_entorno.sh` | Junta `/etc/invoke/pbx.conf` y los secretos en `/run/invoke-pbx.env` |
| `renovar_certificado.sh` | Hook de certbot |
| `laboratorio/` | Red telefónica simulada para el apéndice A. Nunca en producción |

## Desarrollo local

```bash
pip install -r requirements-dev.txt
docker compose up -d postgres
export DATABASE_URL=postgresql://postgres:invoke@127.0.0.1:5432/invoke
python scripts/migrar.py --verificar
python scripts/semilla_demo.py
INVOKE_AUTH_DEV=1 python scripts/servidor_local.py   # http://127.0.0.1:8000/?dev_uid=dev-admin
```

Usuarios de la semilla: `dev-admin`, `dev-sup`, `dev-enc1`, `dev-enc2` y `dev-enc3`. Sin Asterisk funciona todo salvo el audio, y el puesto lo avisa. Para el entorno completo (PBX, TURN y servicios) está `docker compose up` con un `.env` armado a partir de `.env.ejemplo` y un certificado en `infra/asterisk/keys-dev/` (puede ser autofirmado). Con `INVOKE_PBX_LABORATORIO=1` en el `.env` y `INVOKE_TRUNK=Local/{numero}@invoke-lab-pstn`, el PBX local usa la red telefónica simulada y se puede correr `scripts/probar_pbx.py`.

### Integración continua

`.github/workflows/ci.yml` corre en cada push: pytest contra un Postgres real (invariantes, fórmulas, API, exportación y verificador de accesos), las baterías JS compartidas con Python y `tsc` sobre el frontend.
