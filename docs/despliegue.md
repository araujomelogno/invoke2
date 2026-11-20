# Guía de despliegue — INVOKE 2

Nov 20, 2026 · @Equipos · con el despliegue completo del PBX

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
| PBX (Asterisk 20) | Compute Engine, Ubuntu 24.04 + Docker bajo systemd | e2-standard-2, 50 GB | `infra/asterisk` |
| TURN | Compute Engine, Ubuntu 24.04 + Docker | e2-small | `infra/coturn` |

**Por qué ari y pacer van con exactamente una instancia.** Los dos son consumidores únicos: ari tiene el único WebSocket de eventos contra Asterisk y pacer es un lazo de control con temporizadores. Dos instancias originarían llamadas dos veces. Se despliegan con `--min-instances 1 --max-instances 1` y CPU siempre asignada.

## Requisitos

- Un proyecto de Google Cloud con facturación, asociado a un proyecto de Firebase (el mismo id).
- `gcloud`, `firebase-tools` 13+, Docker, Python 3.12 y Node 22 en la máquina de quien despliega.
- Dos nombres DNS con IP fija, `pbx.<dominio>` y `turn.<dominio>`, con certificados TLS (Let's Encrypt). El navegador exige WSS con certificado válido.
- Los datos del trunk SIP actual (no se reevalúa el proveedor): la lista completa está en 4.2.

```bash
export PROYECTO=invoke-equipos
export REGION=southamerica-east1
gcloud config set project $PROYECTO
gcloud services enable run.googleapis.com cloudfunctions.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com vpcaccess.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com compute.googleapis.com cloudtasks.googleapis.com \
  servicenetworking.googleapis.com iap.googleapis.com
```

## 1. Red

Una VPC propia con tres piezas: acceso privado a servicios (Cloud SQL con IP privada), un conector serverless (Functions y Cloud Run llegan a la base y a ARI por IP privada) y las direcciones fijas del PBX y del TURN.

```bash
gcloud compute networks create invoke-vpc --subnet-mode=custom
gcloud compute networks subnets create invoke-sub --network=invoke-vpc --region=$REGION --range=10.10.0.0/24

# Cloud SQL con IP privada dentro de la VPC
gcloud compute addresses create invoke-servicios --global --purpose=VPC_PEERING --prefix-length=16 --network=invoke-vpc
gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com \
  --ranges=invoke-servicios --network=invoke-vpc

gcloud compute networks vpc-access connectors create invoke-vpc --region=$REGION \
  --network=invoke-vpc --range=10.8.0.0/28

# IP públicas fijas (DNS pbx.<dominio> y turn.<dominio>) e IP interna fija del PBX (la de ARI_URL)
gcloud compute addresses create pbx-ip turn-ip --region=$REGION
gcloud compute addresses create pbx-ip-interna --region=$REGION --subnet=invoke-sub --addresses=10.10.0.10
```

Firewall. La VPC es nueva, así que todo lo que no está acá queda cerrado:

```bash
# PBX: SIP solo desde el proveedor del trunk (TRUNK_IP, 4.2)
gcloud compute firewall-rules create invoke-pbx-sip --network=invoke-vpc --target-tags=pbx \
  --source-ranges=<IP o red del proveedor> --allow=udp:5060
# PBX: WSS de los navegadores y RTP (navegadores, TURN y proveedor)
gcloud compute firewall-rules create invoke-pbx-medios --network=invoke-vpc --target-tags=pbx \
  --allow=tcp:8089,udp:10000-20000
# PBX: ARI (8088) solo desde el conector, para el servicio ari
gcloud compute firewall-rules create invoke-ari --network=invoke-vpc --target-tags=pbx \
  --source-ranges=10.8.0.0/28 --allow=tcp:8088
# TURN: señalización y rango de relay
gcloud compute firewall-rules create invoke-turn --network=invoke-vpc --target-tags=turn \
  --allow=udp:3478,tcp:3478,tcp:5349,udp:49152-65535
# Let's Encrypt (desafío HTTP de certbot) en las dos VM
gcloud compute firewall-rules create invoke-acme --network=invoke-vpc --target-tags=pbx,turn --allow=tcp:80
# SSH solo por IAP
gcloud compute firewall-rules create invoke-ssh-iap --network=invoke-vpc \
  --source-ranges=35.235.240.0/20 --allow=tcp:22
```

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

`DB_CLAVE_ASTERISK` tiene que ser la misma clave que se usó para `asterisk_rt_login` en el paso 2. Dale `roles/secretmanager.secretAccessor` a la cuenta de servicio de Functions y de Cloud Run; las VM tienen cuentas propias que solo leen sus secretos (4.3 y 5).

## 4. PBX Asterisk

El PBX es parte del sistema, no un servicio externo: se despliega desde `infra/asterisk` y su configuración está versionada como el resto. Las razones de cada elección están en `docs/decisiones.md` (ADR-003, ADR-011 y ADR-013); acá va el resumen y el procedimiento.

### 4.1 Qué se despliega

```mermaid
flowchart LR
  subgraph VM pbx [VM pbx · Ubuntu 24.04]
    SD[systemd: invoke-pbx] --> C[Contenedor invoke-pbx<br/>Asterisk 20 · red del host]
    CE[cargar_entorno.sh] -->|/run/invoke-pbx.env| SD
    K[/etc/invoke/keys<br/>certificado/] --> C
    SN[/etc/invoke/sonidos<br/>disculpas/] --> C
    G[/var/lib/invoke/grabaciones/] --- C
  end
  SM[Secret Manager] --> CE
  N[Navegadores] -->|WSS 8089, RTP| C
  TU[coturn] -->|RTP relay| C
  PR[Proveedor del trunk] <-->|SIP 5060, RTP| C
  ARI[Cloud Run ari] -->|ARI 8088, IP privada| C
  C -->|realtime, solo lectura| SQL[(Cloud SQL)]
```

| Elección | Valor | Por qué, en una línea |
| --- | --- | --- |
| Versión | Asterisk 20.6 del paquete de Ubuntu 24.04 (`asterisk`, `asterisk-modules`, `asterisk-core-sounds-es`) | Es lo que se validó módulo por módulo y con la prueba de laboratorio (44 de 44). Sin compilar. Ver soporte en 4.12 |
| Empaquetado | Imagen Docker propia, `--network host`, bajo systemd | La configuración viaja con el repositorio; la red del host evita NAT de Docker sobre 10.000 puertos RTP |
| VM | Ubuntu 24.04 LTS, e2-standard-2, disco 50 GB | Hace falta `gcloud`, `certbot` y systemd en el host; Container-Optimized OS no los trae |
| Canal SIP | Solo `chan_pjsip` (`chan_sip` no se carga) | Único canal mantenido; WebRTC y realtime |
| Control | Solo ARI. AMI deshabilitado (`manager.conf`) | ADR-003 |
| Endpoints de los puestos | Realtime desde Postgres (`asterisk.ps_*`, `sorcery.conf`), usuario de solo lectura | La API rota la credencial SIP en cada inicio de sesión (DI-12) sin recargar Asterisk |
| Trunk, transportes | Archivo (`pjsip.conf`) | No cambian en operación |
| ARI | HTTP en la **IP privada**, 8088 | Nunca expuesto a internet; lo usa `ari` por el conector de la VPC |
| WSS | TLS en 8089, certificado de Let's Encrypt | El navegador no acepta WSS sin certificado válido |
| NAT de medios | IP pública declarada (`external_media_address`) y candidato ICE de host mapeado a la pública (`rtp.conf`) | GCE hace NAT 1:1; no hace falta STUN en el PBX |
| Códec | G.711 (alaw, ulaw) en el trunk y por defecto en los puestos; Opus solo si el encuestador lo tiene habilitado | ADR-004: sin transcodificación salvo excepción visible |
| CDR y CEL | Deshabilitados (`cdr.conf`, `cel.conf`) | El registro de llamadas es `invoke.evento` (ADR-002) y la vista `cdr_compat` (ADR-009) |
| Grabaciones | En `/var/lib/invoke/grabaciones` del host hasta que `ari` las sube al bucket | Sobreviven a que se recree el contenedor |
| Secretos | Secret Manager → archivo en `/run` (tmpfs) al arrancar | No quedan en el disco ni en la imagen (regla 11) |

Archivos del repositorio que intervienen:

| Archivo | Para qué |
| --- | --- |
| `infra/asterisk/*.conf` | Plantillas de configuración; `entrypoint.sh` las completa con las variables de entorno |
| `infra/asterisk/Dockerfile`, `entrypoint.sh`, `instalar_archivos.sh` | Imagen y arranque del contenedor |
| `infra/asterisk/invoke-pbx.service` | Unidad de systemd |
| `infra/asterisk/cargar_entorno.sh` | Junta `/etc/invoke/pbx.conf` y los secretos en `/run/invoke-pbx.env` |
| `infra/asterisk/renovar_certificado.sh` | Hook de certbot |
| `infra/asterisk/laboratorio/` | PSTN simulada para la prueba de laboratorio (4.10). Nunca en producción |

### 4.2 Datos que hay que pedirle al proveedor del trunk

No se reevalúa el proveedor, pero hacen falta estos datos antes de empezar:

| Dato | Variable | Nota |
| --- | --- | --- |
| Host (o IP) y puerto del proxy SIP | `TRUNK_HOST` | Si no es 5060, `TRUNK_HOST=host:puerto` |
| IP o red desde la que manda tráfico | `TRUNK_IP` | Para reconocerlo (`identify`) y para la regla de firewall |
| Dominio para el `From` | `TRUNK_DOMINIO` | Muchas veces es el mismo host |
| Usuario y clave | `TRUNK_USUARIO`, secreto `TRUNK_CLAVE` | Aunque autentique por IP, la plantilla responde a un desafío digest si lo hay |
| ¿Autentica por IP o exige registro? | — | Por IP: hay que darle la IP pública del PBX. Con registro: descomentar `[trunk-registro]` en `pjsip.conf` |
| Formato del número a discar | `INVOKE_TRUNK` del servicio `ari` | El número sale tal como está en la muestra; si el proveedor exige prefijo, va en la plantilla: `PJSIP/0{numero}@trunk` |
| Códecs | — | Tiene que aceptar G.711 A-law (PCMA) |
| Canales simultáneos contratados | — | En predictivo se originan hasta N por encuestador libre (Fase 3) |

### 4.3 Red y cuenta de servicio

Las reglas de firewall del PBX están en el paso 1. La VM usa una cuenta de servicio propia que solo puede leer sus tres secretos:

```bash
gcloud iam service-accounts create invoke-pbx --display-name="PBX de INVOKE 2"
for s in TRUNK_CLAVE ARI_CLAVE DB_CLAVE_ASTERISK; do
  gcloud secrets add-iam-policy-binding $s \
    --member=serviceAccount:invoke-pbx@$PROYECTO.iam.gserviceaccount.com \
    --role=roles/secretmanager.secretAccessor
done
```

`DB_CLAVE_ASTERISK` es la misma clave con la que se creó `asterisk_rt_login` en el paso 2.

### 4.4 La VM

```bash
gcloud compute instances create pbx --zone=${REGION}-a --machine-type=e2-standard-2 \
  --network-interface=subnet=invoke-sub,private-network-ip=10.10.0.10,address=pbx-ip \
  --tags=pbx --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB --boot-disk-type=pd-balanced \
  --service-account=invoke-pbx@$PROYECTO.iam.gserviceaccount.com --scopes=cloud-platform
gcloud compute addresses describe pbx-ip --region=$REGION --format='value(address)'
```

Con esa IP pública se crea el registro DNS `A pbx.<dominio>` y se le avisa al proveedor si autentica por IP. Después, en la VM (SSH solo por IAP):

```bash
gcloud compute ssh pbx --zone=${REGION}-a --tunnel-through-iap
sudo apt-get update && sudo apt-get install -y docker.io git certbot
sudo git clone <URL del repositorio> /opt/invoke      # repositorio privado: deploy key de solo lectura
sudo git -C /opt/invoke checkout <etiqueta de la versión>
sudo install -d -m 0750 /etc/invoke /etc/invoke/keys /etc/invoke/sonidos
sudo install -d /var/lib/invoke/grabaciones
gcloud secrets versions access latest --secret=ARI_CLAVE >/dev/null && echo "secretos accesibles"
```

La imagen de Ubuntu de GCE ya trae `gcloud`. Si la última línea falla, revisá la cuenta de servicio y el alcance `cloud-platform` de la VM.

### 4.5 Configuración no secreta

```bash
sudo tee /etc/invoke/pbx.conf >/dev/null <<'EOF'
# Valores NO secretos del PBX (los secretos vienen de Secret Manager).
INVOKE_PBX_DOMINIO=pbx.equipos.com.uy
PBX_IP_PUBLICA=203.0.113.10
PBX_IP_PRIVADA=10.10.0.10
TRUNK_HOST=sip.proveedor.example
TRUNK_IP=198.51.100.20
TRUNK_DOMINIO=sip.proveedor.example
TRUNK_USUARIO=usuario_del_trunk
DB_HOST=10.20.0.3
DB_PUERTO=5432
DB_NOMBRE=invoke
EOF
sudo chmod 0640 /etc/invoke/pbx.conf
```

| Variable | De dónde sale |
| --- | --- |
| `INVOKE_PBX_DOMINIO` | El nombre DNS de 4.4; tiene que coincidir con `SIP_WSS` de Functions |
| `PBX_IP_PUBLICA` | `gcloud compute addresses describe pbx-ip` |
| `PBX_IP_PRIVADA` | La fija de la VM (`10.10.0.10`); es la de `ARI_URL` |
| `TRUNK_*` | Del proveedor (4.2) |
| `DB_HOST` | `gcloud sql instances describe invoke --format='value(ipAddresses[0].ipAddress)'` |
| `INVOKE_PBX_LABORATORIO` | Solo en el PBX de laboratorio (4.10). **Nunca en producción** |

Si falta alguna variable, el contenedor no arranca y lo dice (`Faltan variables de entorno: …`).

### 4.6 Certificado TLS

```bash
sudo certbot certonly --standalone -d pbx.<dominio> --non-interactive --agree-tos -m <correo de sistemas> \
  --deploy-hook /opt/invoke/infra/asterisk/renovar_certificado.sh
ls -l /etc/invoke/keys/      # pbx.crt y pbx.key
```

certbot usa el puerto 80 (regla `invoke-acme` del paso 1) y deja programada la renovación. El hook copia el certificado a `/etc/invoke/keys` y pide `core restart when convenient`: Asterisk se reinicia recién cuando no hay canales, y como cada puesto conectado tiene su pata permanente (ADR-011), en la práctica eso pasa al terminar el turno. certbot renueva 30 días antes del vencimiento, así que hay margen.

### 4.7 Mensaje de disculpas

Es el audio que oye una persona cuando el discador abandona su llamada (power y predictivo). El texto es el pendiente P-06. Formato: WAV PCM de 16 bits, 8 kHz, mono:

```bash
sox grabacion.wav -r 8000 -c 1 -b 16 invoke-disculpas.wav
sudo install -m 0644 invoke-disculpas.wav /etc/invoke/sonidos/
```

Sin este archivo el PBX de producción no arranca, para que el abandono nunca corte en silencio.

### 4.8 Imagen y servicio

```bash
cd /opt/invoke
sudo docker build -t invoke-pbx:actual infra/asterisk
sudo install -m 0644 infra/asterisk/invoke-pbx.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now invoke-pbx
sudo journalctl -u invoke-pbx -f
```

Al arrancar, el servicio: (1) arma `/run/invoke-pbx.env` con la configuración y los secretos, (2) recrea el contenedor, (3) el contenedor completa las plantillas, instala el certificado y el mensaje de disculpas con dueño `asterisk` y (4) arranca Asterisk como usuario `asterisk`, no como root.

Después se despliega el servicio `ari` (paso 7) con `ARI_URL=http://10.10.0.10:8088/ari` y la plantilla de discado que pidió el proveedor en `INVOKE_TRUNK`.

### 4.9 Verificación

```bash
A="sudo docker exec invoke-pbx asterisk -rx"
$A "core show version"                     # Asterisk 20.x
$A "pjsip show transports"                 # transport-udp y transport-wss
$A "pjsip show contacts"                   # trunk/sip:… con estado Avail (qualify cada 30 s)
$A "pjsip show registrations"              # solo si el trunk usa registro: Registered
$A "realtime show pgsql status"            # conectado a la base
$A "http show status"                      # 8088 en la IP privada, 8089 con TLS
$A "ari show apps"                         # invoke, una vez que el servicio ari se conectó
$A "module show like chan_sip"             # 0 módulos: chan_sip no se carga
sudo ss -lntup | grep asterisk             # solo 5060/udp, 10.10.0.10:8088 y *:8089 (+ RTP)
```

Desde afuera de la VPC:

```bash
openssl s_client -connect pbx.<dominio>:8089 -servername pbx.<dominio> </dev/null | grep 'Verify return code'   # 0 (ok)
curl -m 5 http://<PBX_IP_PUBLICA>:8088/ari/asterisk/info   # tiene que fallar: ARI no se publica
```

Y desde la VPC (por ejemplo, desde la propia VM): `curl -u invoke:$(gcloud secrets versions access latest --secret=ARI_CLAVE) http://10.10.0.10:8088/ari/asterisk/info`.

Cuando un puesto inicia sesión, su endpoint aparece en `pjsip show endpoint p<id>` con los códecs que le corresponden (`alaw,ulaw`, u `opus,alaw,ulaw` si tiene Opus). La prueba con una persona real está en el paso 10.

### 4.10 PBX de laboratorio

Antes del primer despliegue, y después de cualquier cambio en `infra/asterisk` o en `services/ari`, se corre la prueba de punta a punta contra un Asterisk real con la misma imagen. La PSTN y el navegador se simulan con canales Local (`laboratorio/extensions_laboratorio.conf`): no hace falta trunk ni navegador.

| Número simulado | Comportamiento |
| --- | --- |
| `0990000001` | Persona: timbra, atiende y habla |
| `0990000002` | Ocupado (causa 17) |
| `0990000003` | No contesta |
| `0990000004` | Contestador: atiende y reproduce un mensaje largo (AMD → máquina) |
| cualquier otro | Número inexistente (causa 1) |

`scripts/probar_pbx.py` recorre: pata permanente del puesto, preview con timbre, atención, puente, grabación, escucha del supervisor, corte y clasificación; ocupado, no contesta, inexistente; power con persona; power sin encuestadores (abandono con mensaje de disculpas completo); contestador; subida de la grabación; endpoints realtime con sus códecs; y, con `LAB_REINICIO_ARI`, reinicio de `ari` con una llamada en curso. Resultado esperado: `44 de 44 comprobaciones bien`.

Se usa una VM aparte (`pbx-lab`, e2-small alcanza) o cualquier máquina con Docker, **con una base descartable**: la prueba crea estudios y usuarios `lab-…`.

```bash
# Base descartable
docker run -d --name pg-lab --network host -e POSTGRES_PASSWORD=lab postgres:16
export DATABASE_URL=postgresql://postgres:lab@127.0.0.1:5432/postgres
pip install -r requirements.txt
python scripts/migrar.py --verificar
docker exec -i pg-lab psql -U postgres -v clave_api="'lab'" -v clave_ast="'lab'" -v clave_tab="'lab'" < db/roles_produccion.sql

# Certificado autofirmado: en el laboratorio no hay navegador
mkdir -p lab/keys
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=pbx-lab \
  -keyout lab/keys/pbx.key -out lab/keys/pbx.crt

# PBX con la misma imagen y el dialplan de laboratorio
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

# Servicio ari apuntando a la PSTN simulada, y la prueba
export ARI_URL=http://$IP:8088/ari ARI_USUARIO=invoke ARI_CLAVE=lab \
  INVOKE_TRUNK='Local/{numero}@invoke-lab-pstn'
PORT=8790 python services/ari/main.py > lab/ari.log 2>&1 & echo $! > lab/ari.pid
LAB_REINICIO_ARI="kill \$(cat lab/ari.pid); sleep 1; PORT=8790 python services/ari/main.py >> lab/ari.log 2>&1 & echo \$! > lab/ari.pid" \
  python scripts/probar_pbx.py
```

El trunk del laboratorio apunta a un puerto donde no hay nadie: `pjsip show contacts` lo muestra `Unavailable` y es lo esperado. En `lab/ari.log` no tiene que haber errores; el único aviso esperado es que `GRABACIONES_BUCKET` no está configurado (la prueba sube a un bucket local).

**Qué no cubre el laboratorio** (queda para la prueba con personas, paso 10, y P-01): el trunk del proveedor, el audio real del navegador (SIP.js, ICE, DTLS-SRTP, TURN) y la negociación de Opus en un navegador.

### 4.11 Operación del PBX

| Tarea | Cómo |
| --- | --- |
| Logs | `sudo journalctl -u invoke-pbx` (la salida de Asterisk va al journal) |
| Consola | `sudo docker exec -it invoke-pbx asterisk -r` |
| Llamadas en curso | `$A "core show channels"` (con `A` como en 4.9); con transcodificación: `$A "core show channels verbose"` |
| Actualizar la configuración o la versión | `git -C /opt/invoke checkout <etiqueta>`, `docker build -t invoke-pbx:actual infra/asterisk`, `systemctl restart invoke-pbx` **fuera del horario de campo**: reiniciar corta todas las llamadas y las patas de los puestos (los navegadores reconectan solos y `ari` recupera los puestos desde la base) |
| Rotar `ARI_CLAVE` | Versión nueva en Secret Manager, `systemctl restart invoke-pbx` y redesplegar `ari` |
| Rotar `TRUNK_CLAVE` o `DB_CLAVE_ASTERISK` | Versión nueva (y `ALTER ROLE asterisk_rt_login PASSWORD …` en la base) y `systemctl restart invoke-pbx` |
| Parches del sistema | `unattended-upgrades` de Ubuntu (viene activo). La imagen se reconstruye para tomar parches de Asterisk |

Qué vigilar, además de la tabla de "Qué mirar en el día a día":

| Indicador | Umbral | Por qué |
| --- | --- | --- |
| Disco: `df -h /var/lib/invoke/grabaciones` | > 70 % | Una grabación ocupa ~0,96 MB por minuto (WAV 8 kHz, 16 bits); 30 llamadas conversando todo el tiempo son ~1,7 GB por hora. Con 50 GB hay un día de margen si la subida al bucket se corta |
| `SELECT count(*) FROM invoke.grabacion_pendiente` | crece y no baja | La subida está fallando: revisar `ari` |
| Trunk en `pjsip show contacts` | `Unavailable` | Sin trunk no sale ninguna llamada: los comandos `originar` terminan en no-contacto técnico |
| CPU de la VM | > 60 % sostenido | Cada puesto con Opus transcodifica permanentemente (H4); la consola los muestra |

Se recomienda una alerta de Cloud Monitoring sobre disco y CPU (agente de operaciones de GCE).

### 4.12 Seguridad y soporte de la versión

- **Superficie expuesta**: SIP 5060/udp solo desde el proveedor, WSS 8089/tcp y RTP 10000–20000/udp públicos. ARI solo en la IP privada y solo desde el conector. AMI apagado. SSH solo por IAP.
- **Sin anónimos**: el contexto de entrada del trunk (`invoke-entrante`) rechaza todo, y un endpoint desconocido no tiene contexto. Los puestos se autentican con una credencial efímera que la base guarda como hash (DI-12), y el dialplan solo deja entrar a `sala-N` al endpoint `pN` (ADR-011).
- **Escaneos SIP**: con 5060 cerrado salvo para el proveedor, el ruido llega por WSS, que exige TLS y credencial. Si aparecen intentos repetidos en el log, `fail2ban` sobre el journal es opcional.
- **Soporte**: Asterisk 20 es LTS; a fines de 2026 está en su etapa de solo parches de seguridad, que según el calendario publicado termina en octubre de 2027 (verificarlo en la wiki de Asterisk). El paquete de Ubuntu está en *universe*: recibe parches de seguridad de la comunidad, no de Canonical, salvo con Ubuntu Pro. Antes de esa fecha hay que pasar a Asterisk 22 LTS y repetir la prueba de laboratorio (P-15).

## 5. TURN (coturn)

Va en una VM aparte, no en la del PBX: coturn solo retransmite hacia la IP pública del PBX (`allowed-peer-ip`), y en la misma VM ese tráfico tendría que salir y volver a entrar por la misma IP pública, un camino que GCE no garantiza. Una e2-small alcanza para 30 sesiones (ADR-005 y ADR-013).

```bash
gcloud iam service-accounts create invoke-turn --display-name="TURN de INVOKE 2"
gcloud secrets add-iam-policy-binding TURN_SECRETO \
  --member=serviceAccount:invoke-turn@$PROYECTO.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor
gcloud compute instances create turn --zone=${REGION}-a --machine-type=e2-small \
  --network-interface=subnet=invoke-sub,address=turn-ip --tags=turn \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --service-account=invoke-turn@$PROYECTO.iam.gserviceaccount.com --scopes=cloud-platform
```

Con el DNS `A turn.<dominio>` apuntando a `turn-ip`, en la VM:

```bash
sudo apt-get update && sudo apt-get install -y docker.io certbot gettext-base
sudo certbot certonly --standalone -d turn.<dominio> --non-interactive --agree-tos -m <correo de sistemas>
sudo install -d /etc/coturn/certs
sudo cp -L /etc/letsencrypt/live/turn.<dominio>/fullchain.pem /etc/coturn/certs/turn.crt
sudo cp -L /etc/letsencrypt/live/turn.<dominio>/privkey.pem /etc/coturn/certs/turn.key
export TURN_SECRETO=$(gcloud secrets versions access latest --secret=TURN_SECRETO) PBX_IP_PUBLICA=<IP pública del PBX>
envsubst '${TURN_SECRETO} ${PBX_IP_PUBLICA}' < turnserver.conf | sudo tee /etc/coturn/turnserver.conf >/dev/null
# La imagen de coturn corre como nobody (65534): tiene que poder leer la clave y la configuración.
sudo chown -R 65534:65534 /etc/coturn && sudo chmod 0600 /etc/coturn/turnserver.conf /etc/coturn/certs/turn.key
sudo docker run -d --name turn --network host --restart always \
  -v /etc/coturn/turnserver.conf:/etc/coturn/turnserver.conf:ro -v /etc/coturn/certs:/etc/coturn/certs:ro \
  coturn/coturn:4.6 -c /etc/coturn/turnserver.conf
```

`turnserver.conf` es el de `infra/coturn/` (copialo con `gcloud compute scp`); ajustá `realm` al dominio. El relay solo acepta como destino al PBX. Al renovar el certificado, repetí la copia y `docker restart turn`, fuera del horario de campo.

## 6. Bucket de grabaciones

Se graban todas las llamadas de encuesta. El servicio `ari` las baja del PBX, las sube a este bucket y las borra del disco del PBX; sin el bucket, el disco se llena en días.

```bash
gcloud storage buckets create gs://$PROYECTO-grabaciones --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
# Retención: pendiente de decisión (decisiones.md, P-11). Ejemplo para borrar a los 365 días:
cat > retencion.json <<'EOF'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 365}}]}
EOF
gcloud storage buckets update gs://$PROYECTO-grabaciones --lifecycle-file=retencion.json
```

Permisos:

- La cuenta de servicio de `ari` sube objetos: `roles/storage.objectCreator` sobre el bucket.
- La cuenta de servicio de Functions firma las URL para escuchar desde la consola: `roles/storage.objectViewer` sobre el bucket y `roles/iam.serviceAccountTokenCreator` sobre sí misma.

Del orden de 25 GB por mes de operativo a sala llena. Conviene mirar `SELECT count(*) FROM invoke.grabacion_pendiente`: tiene que quedar cerca de cero.

## 7. Servicios de Cloud Run

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
  --set-env-vars=ARI_URL=http://10.10.0.10:8088/ari,INVOKE_TRUNK='PJSIP/{numero}@trunk',GRABACIONES_BUCKET=$PROYECTO-grabaciones

gcloud run deploy pacer --image=$REGION-docker.pkg.dev/$PROYECTO/invoke/pacer $COMUN \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --ingress=internal --no-allow-unauthenticated

gcloud run deploy sala --image=$REGION-docker.pkg.dev/$PROYECTO/invoke/sala $COMUN \
  --min-instances=1 --max-instances=3 --timeout=3600 --session-affinity --allow-unauthenticated \
  --set-env-vars=FIREBASE_PROJECT=$PROYECTO
```

`INVOKE_TRUNK` es la plantilla de discado: `{numero}` es el número tal como está en la muestra, con el prefijo que pida el proveedor (4.2). En el PBX de laboratorio es `Local/{numero}@invoke-lab-pstn`.

La sala es pública pero autenticada: el primer mensaje de cada WebSocket trae el token de Firebase, que se verifica antes de suscribir. Sin token válido, la sala cierra la conexión con el código 4401.

## 8. Firebase: Auth, Functions y Hosting

1. **Auth.** En la consola de Firebase, habilitá el proveedor «Correo y contraseña» y agregá el dominio de Hosting a los dominios autorizados.
2. **Configuración de la web.** Editá `web/js/config.js` con la configuración web del proyecto (no es secreta) y con `salaWs: 'wss://<url-de-sala>/sala'`.
3. **Variables de la función.** Creá `functions/.env.<proyecto>` con `INVOKE_DB_POOL=10` (conexiones por instancia; coincide con `concurrency` en `functions/main.py`), `INVOKE_DB_ESPERA_S=8`, `SIP_WSS=wss://pbx.<dominio>:8089/ws`, `SIP_DOMINIO=pbx.<dominio>`, `TURN_URLS=turn:turn.<dominio>:3478?transport=udp,turns:turn.<dominio>:5349?transport=tcp` y `STUN_URLS=stun:turn.<dominio>:3478`. `INVOKE_AUTH_DEV` no se define nunca.
4. **Functions.** En `functions/main.py` ya están la región, el conector VPC y los secretos que usa. El predeploy copia `invoke/` y `schemas/` adentro de `functions/`.
   ```bash
   echo '{"projects":{"default":"'$PROYECTO'"}}' > .firebaserc
   firebase deploy --only functions:invoke
   ```
5. **Hosting.** Reescribe `/api/**` a la función `api` (ver `firebase.json`).
   ```bash
   firebase deploy --only hosting
   ```

## 9. Primer administrador

Creá la cuenta en Firebase Auth (consola o CLI) y dala de alta en la base con su `uid`. Desde ahí, los demás usuarios se crean desde **Administración → Usuarios**.

```sql
INSERT INTO invoke.usuario (uid, email, nombre, roles)
VALUES ('UID_DE_FIREBASE', 'coordinacion@equipos.com.uy', 'Coordinación', ARRAY['administrador','investigador','supervisor']);
```

## 10. Verificación después de desplegar

1. `GET https://<hosting>/api/salud` responde `{"ok": true}`.
2. Entrar como administrador, crear un estudio de prueba marcado como **prueba o capacitación**, publicar el cuestionario de ejemplo (`schemas/ejemplos/satisfaccion_clientes.json`, desde «Importar JSON» del editor) y cargar una guía con un número propio.
3. Entrar como encuestador, hacer la prueba de audio y verificar que la barra superior pase a **Audio OK**.
4. Pedir el caso, llamar al número propio, atender y comprobar audio en las dos direcciones.
5. Desde la consola de supervisión, conectar el audio de escucha y escuchar la llamada. La calidad de red del puesto tiene que aparecer en la tarjeta.
6. Clasificar, abrir **Reportes** y ver el intento. El estudio de prueba no da ficha técnica, porque no es un estudio.
7. Revisar los logs de `ari`: tiene que figurar «conectado a ARI» y ningún comando en error (`SELECT * FROM invoke.comando_telefonia WHERE estado = 'error'`).
8. Unos minutos después de la llamada, la grabación tiene que estar en el bucket (`SELECT * FROM invoke.grabacion_en_bucket ORDER BY subida_en DESC LIMIT 1`) y no en el PBX.
9. Audio real (lo que el laboratorio no cubre): la persona del otro lado oye al encuestador y viceversa, desde una red de oficina y desde una red doméstica; en la consola, la tarjeta del puesto muestra pérdida y jitter, y "TURN" si fue por relay. Si no hay audio en un solo sentido, revisar `PBX_IP_PUBLICA` y la regla `invoke-pbx-medios`.
10. Abandono: en un estudio en power sin encuestadores libres, la persona oye el mensaje de disculpas completo antes del corte.
11. Códec: con el puesto sin Opus, la tarjeta de la consola dice PCMA o PCMU y `asterisk -rx "core show channels verbose"` no muestra transcodificación. Repetir con un usuario con Opus habilitado y verificar que ahí sí aparece.

## Actualizar y operar

### Orden de una actualización

1. **Migraciones** con `scripts/migrar.py --verificar`. Son aditivas: el código viejo tiene que seguir funcionando con el esquema nuevo.
2. **Functions** (`firebase deploy --only functions:invoke`).
3. **Cloud Run**: `sala`, después `pacer` y por último `ari`, fuera del horario de campo. Al reiniciar, `ari` recupera los puestos desde la base y los navegadores reconectan solos.
4. **Hosting**.
5. **PBX**, solo si cambió `infra/asterisk`: prueba de laboratorio (4.10) y después reconstruir la imagen y reiniciar el servicio (4.11), fuera del horario de campo.

### Copias y recuperación

Cloud SQL con backup diario y recuperación a un punto en el tiempo. Como el log de eventos es inmutable, una recuperación a un instante reproduce exactamente los reportes de ese instante.

### Rotación de secretos

Para rotar `TURN_SECRETO` o `ARI_CLAVE`, se crea una versión nueva y se redespliega el consumidor (coturn y Functions, o PBX y ari; el detalle del PBX está en 4.11). Las credenciales SIP de los puestos se rotan solas en cada inicio de sesión.

### Qué mirar en el día a día

| Indicador | Cómo | Para qué |
| --- | --- | --- |
| Comandos en error | `invoke.comando_telefonia` con `estado = 'error'` | Si suben, revisar el trunk o ARI |
| PBX | Disco, CPU y estado del trunk (4.11) | Grabaciones sin espacio o trunk caído |
| Grabaciones sin subir | `SELECT count(*) FROM invoke.grabacion_pendiente` | Si crece, el disco del PBX se está llenando |
| Puestos en Opus | Consola: «N en Opus · Asterisk transcodifica» | Cada uno es transcodificación permanente |
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

Usuarios de la semilla: `dev-admin`, `dev-sup`, `dev-enc1`, `dev-enc2` y `dev-enc3`. Sin Asterisk funciona todo salvo el audio, y el puesto lo avisa. Para el entorno completo (PBX, TURN y servicios) está `docker compose up` con un `.env` armado a partir de `.env.ejemplo` y un certificado en `infra/asterisk/keys-dev/` (puede ser autofirmado, como en 4.10). Con `INVOKE_PBX_LABORATORIO=1` en el `.env` y `INVOKE_TRUNK=Local/{numero}@invoke-lab-pstn`, el PBX local usa la PSTN simulada y se puede correr `scripts/probar_pbx.py`.

### Integración continua

`.github/workflows/ci.yml` corre en cada push: pytest contra un Postgres real (invariantes, fórmulas, API, exportación y verificador de accesos), las baterías JS compartidas con Python y `tsc` sobre el frontend.
