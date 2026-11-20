#!/usr/bin/env bash
# Arma el archivo de entorno del PBX: valores no secretos de /etc/invoke/pbx.conf y
# secretos de Secret Manager. Lo llama invoke-pbx.service antes de cada arranque.
# El destino está en /run (tmpfs): los secretos no quedan en el disco de la VM.
set -euo pipefail
destino="$1"
umask 077

# Valores no secretos (IPs, host del trunk, base). Ver docs/despliegue.md.
# shellcheck source=/dev/null
source /etc/invoke/pbx.conf

# Sin el certificado no arranca (el navegador exige WSS válido). El mensaje de
# disculpas lo controla el contenedor: en el laboratorio puede faltar.
for f in /etc/invoke/keys/pbx.crt /etc/invoke/keys/pbx.key; do
  [[ -f "$f" ]] || { echo "Falta $f (docs/despliegue.md, PBX)" >&2; exit 1; }
done
mkdir -p /etc/invoke/sonidos /var/lib/invoke/grabaciones

secreto() { gcloud secrets versions access latest --secret="$1"; }

{
  echo "PBX_IP_PUBLICA=${PBX_IP_PUBLICA}"
  echo "PBX_IP_PRIVADA=${PBX_IP_PRIVADA}"
  echo "TRUNK_HOST=${TRUNK_HOST}"
  echo "TRUNK_IP=${TRUNK_IP}"
  echo "TRUNK_DOMINIO=${TRUNK_DOMINIO}"
  echo "TRUNK_USUARIO=${TRUNK_USUARIO}"
  echo "DB_HOST=${DB_HOST}"
  echo "DB_PUERTO=${DB_PUERTO:-5432}"
  echo "DB_NOMBRE=${DB_NOMBRE:-invoke}"
  echo "TRUNK_CLAVE=$(secreto TRUNK_CLAVE)"
  echo "ARI_CLAVE=$(secreto ARI_CLAVE)"
  echo "DB_CLAVE_ASTERISK=$(secreto DB_CLAVE_ASTERISK)"
  [[ "${INVOKE_PBX_LABORATORIO:-0}" == "1" ]] && echo "INVOKE_PBX_LABORATORIO=1"
} > "$destino"
