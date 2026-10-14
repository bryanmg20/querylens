#!/usr/bin/env bash
# Carga de la bateria de predicados por el pipeline real. La lanza
# pred_pipeline.py dentro de ql_sysbench, igual que scenarios/workload.sh.
#
# USO
#   bash /tmp/pred_load.sh <postgres|mysql> <archivo_sql> <segundos>
#
# Repite las consultas del archivo (una por linea) sin pausa en una sola sesion
# hasta pasar <segundos> o hasta que exista STOP_FILE. El cliente lleva la marca
# ql_pred_workload en su linea de comandos para que el orquestador lo termine.

set -uo pipefail

ENGINE="$1"
FILE="$2"
MAX_SECONDS="$3"
STOP_FILE=/tmp/ql_pred_stop

produce() {
  while (( SECONDS < MAX_SECONDS )) && [ ! -e "$STOP_FILE" ]; do
    cat "$FILE"
  done
}

if [ "$ENGINE" = "postgres" ]; then
  export PGPASSWORD=ql_pass
  produce | psql --host=postgres --port=5432 --username=ql_user --dbname=ql_demo \
    --no-psqlrc --quiet --output=/dev/null --set=ql_pred_workload=1
else
  produce | mysql --host=mysql --port=3306 --user=app_user --password=app_pass \
    --database=ql_demo --force --prompt=ql_pred_workload >/dev/null 2>&1
fi
