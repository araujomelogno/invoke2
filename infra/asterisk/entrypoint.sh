#!/usr/bin/env bash
# Renderiza las plantillas con las variables de entorno y arranca Asterisk en primer plano.
set -euo pipefail
for f in /etc/asterisk/plantillas/*.conf; do
  envsubst < "$f" > "/etc/asterisk/$(basename "$f")"
done
exec asterisk -f -vvv
