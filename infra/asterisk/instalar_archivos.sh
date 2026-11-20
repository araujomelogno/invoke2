#!/usr/bin/env bash
# Copia, dentro del contenedor, los archivos que vienen del host (montados de solo
# lectura en /run/invoke) a donde los lee Asterisk, con dueño asterisk:
#   /run/invoke/keys/pbx.crt|key           → /etc/asterisk/keys/
#   /run/invoke/sonidos/invoke-disculpas.* → /usr/share/asterisk/sounds/es/
# Se copian (no se leen en el lugar) porque la clave privada del host es de root y
# 0640, y el usuario asterisk del contenedor no tiene por qué poder leerla allá.
# Lo llaman entrypoint.sh al arrancar y renovar_certificado.sh al renovar el certificado.
set -euo pipefail
origen=/run/invoke
if [[ -d "$origen/keys" ]]; then
  install -d -o asterisk -g asterisk -m 0750 /etc/asterisk/keys
  install -o asterisk -g asterisk -m 0644 "$origen/keys/pbx.crt" /etc/asterisk/keys/pbx.crt
  install -o asterisk -g asterisk -m 0600 "$origen/keys/pbx.key" /etc/asterisk/keys/pbx.key
fi
if [[ -d "$origen/sonidos" ]]; then
  for f in "$origen"/sonidos/invoke-disculpas.*; do
    [[ -e "$f" ]] && install -o asterisk -g asterisk -m 0644 "$f" /usr/share/asterisk/sounds/es/
  done
fi
