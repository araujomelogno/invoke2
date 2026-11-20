#!/usr/bin/env bash
# Arranque del PBX de INVOKE 2.
#
# 1. Verifica que estén todas las variables (los secretos llegan desde Secret Manager).
# 2. Renderiza las plantillas con envsubst y una LISTA EXPLÍCITA de variables: sin la
#    lista, envsubst también vacía las variables del dialplan (${EXTEN:5},
#    ${CHANNEL(endpoint)}, ${LLAMADA}, ${AMDSTATUS}) y el PBX queda roto.
# 3. Con INVOKE_PBX_LABORATORIO=1 agrega el dialplan que simula la PSTN (nunca en producción).
# 4. Instala el certificado TLS y el mensaje de disculpas que llegan del host (instalar_archivos.sh).
# 5. Arranca Asterisk en primer plano como usuario asterisk, no como root.
set -euo pipefail

VARIABLES=(PBX_IP_PUBLICA PBX_IP_PRIVADA TRUNK_HOST TRUNK_IP TRUNK_DOMINIO TRUNK_USUARIO TRUNK_CLAVE
           ARI_CLAVE DB_HOST DB_PUERTO DB_NOMBRE DB_CLAVE_ASTERISK)

faltan=()
for v in "${VARIABLES[@]}"; do
  [[ -n "${!v:-}" ]] || faltan+=("$v")
done
if (( ${#faltan[@]} )); then
  echo "Faltan variables de entorno: ${faltan[*]}" >&2
  exit 1
fi

lista=""
for v in "${VARIABLES[@]}"; do lista+="\${$v} "; done
for f in /etc/asterisk/plantillas/*.conf; do
  envsubst "$lista" < "$f" > "/etc/asterisk/$(basename "$f")"
done

if [[ "${INVOKE_PBX_LABORATORIO:-0}" == "1" ]]; then
  echo "ATENCIÓN: PBX de laboratorio (PSTN simulada). No usar en producción." >&2
  cp /etc/asterisk/plantillas/laboratorio/extensions_laboratorio.conf /etc/asterisk/
else
  rm -f /etc/asterisk/extensions_laboratorio.conf
fi

/usr/local/bin/instalar_archivos.sh

if ! compgen -G "/usr/share/asterisk/sounds/es/invoke-disculpas.*" >/dev/null; then
  if [[ "${INVOKE_PBX_LABORATORIO:-0}" == "1" ]]; then
    # En el laboratorio alcanza con uno de los sonidos de Asterisk.
    ln -s /usr/share/asterisk/sounds/es/vm-goodbye.gsm /usr/share/asterisk/sounds/es/invoke-disculpas.gsm
  else
    echo "Falta el mensaje de disculpas (invoke-disculpas.wav en /etc/invoke/sonidos del host): sin él, el abandono corta en silencio" >&2
    exit 1
  fi
fi

if [[ ! -s /etc/asterisk/keys/pbx.crt || ! -s /etc/asterisk/keys/pbx.key ]]; then
  echo "Falta el certificado TLS en /etc/asterisk/keys (pbx.crt y pbx.key): el navegador exige WSS válido" >&2
  exit 1
fi

chown -R asterisk:asterisk /etc/asterisk /var/spool/asterisk /var/lib/asterisk /var/log/asterisk /var/run/asterisk
exec asterisk -f -U asterisk -G asterisk -vvv
