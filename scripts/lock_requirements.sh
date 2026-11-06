#!/bin/sh
# Regenera los locks de Python (requirements.txt / requirements-dev.txt) a
# partir de los requirements.in. Correr tras cambiar un .in y commitear los
# dos archivos juntos:
#
#   pip install uv && sh scripts/lock_requirements.sh
#
# --universal: un solo lock vale para Linux (imagenes, CI) y para el venv de
# Windows/macOS. --python-version 3.11: la de las imagenes. Un paquete que ya
# esta en el lock conserva su version; para subirla, --upgrade-package <pkg>.
set -eu

cd "$(dirname "$0")/.."

compile() {
    uv pip compile "$@" \
        --universal --python-version 3.11 --generate-hashes --quiet \
        --custom-compile-command "sh scripts/lock_requirements.sh"
}

# El orden importa: requirements-dev y el detector se resuelven con el lock
# del pipeline como restriccion, para compartir versiones.
compile telemetry_pipeline/requirements.in -o telemetry_pipeline/requirements.txt
compile telemetry_pipeline/requirements-dev.in -o telemetry_pipeline/requirements-dev.txt \
    -c telemetry_pipeline/requirements.txt
compile detection_engine/requirements.in -o detection_engine/requirements.txt \
    -c telemetry_pipeline/requirements.txt
compile api_rest/requirements.in -o api_rest/requirements.txt
compile auth_service/requirements.in -o auth_service/requirements.txt
