#!/usr/bin/env bash
# Hook de certbot (--deploy-hook): copia el certificado renovado a donde lo lee el
# contenedor y reinicia Asterisk SOLO cuando no hay canales en curso
# ("core restart when convenient"): una renovación nunca corta una entrevista.
set -euo pipefail
# shellcheck source=/dev/null
source /etc/invoke/pbx.conf
dominio="${INVOKE_PBX_DOMINIO:?definí INVOKE_PBX_DOMINIO en /etc/invoke/pbx.conf}"
install -d -m 0750 /etc/invoke/keys
install -m 0644 "/etc/letsencrypt/live/${dominio}/fullchain.pem" /etc/invoke/keys/pbx.crt
install -m 0640 "/etc/letsencrypt/live/${dominio}/privkey.pem" /etc/invoke/keys/pbx.key
# Dentro del contenedor: copia el certificado nuevo a /etc/asterisk/keys y pide el
# reinicio. "when convenient" espera a que no haya canales, y la pata permanente de
# cada puesto es un canal (ADR-011): en la práctica se aplica al terminar el turno.
# certbot renueva 30 días antes del vencimiento, así que hay margen de sobra.
# En la primera emisión el contenedor todavía no existe: alcanza con la copia.
if [[ -n "$(docker ps -q --filter name='^invoke-pbx$')" ]]; then
  docker exec invoke-pbx /usr/local/bin/instalar_archivos.sh
  docker exec invoke-pbx asterisk -rx "core restart when convenient"
fi
