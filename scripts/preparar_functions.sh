#!/usr/bin/env bash
# Copia el núcleo compartido dentro de functions/ antes de desplegar (Firebase
# solo sube el directorio source). Las copias están en .gitignore.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf functions/invoke functions/schemas
cp -r invoke functions/invoke
cp -r schemas functions/schemas
find functions/invoke -name __pycache__ -prune -exec rm -rf {} +
