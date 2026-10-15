#!/usr/bin/env bash
# Carga de la campana de AP-02/AP-03/AP-04. La lanza campaign.py dentro de
# ql_sysbench.
#
# USO
#   bash /tmp/cmp_load.sh <postgres|mysql> <archivo_sql> <segundos>
#
# Repite las consultas del archivo (una por linea, con valores ya elegidos) sin
# pausa en una sola sesion hasta pasar <segundos> o hasta que exista STOP_FILE.
# El cliente lleva la marca ql_cmp_workload en su linea de comandos para que el
# orquestador lo termine.

set -uo pipefail

ENGINE="$1"
FILE="$2"
MAX_SECONDS="$3"
STOP_FILE=/tmp/ql_cmp_stop

produce() {
  while (( SECONDS < MAX_SECONDS )) && [ ! -e "$STOP_FILE" ]; do
    cat "$FILE"
  done
}

if [ "$ENGINE" = "postgres" ]; then
  export PGPASSWORD=ql_pass
  produce | psql --host=postgres --port=5432 --username=ql_user --dbname=ql_demo \
    --no-psqlrc --quiet --output=/dev/null --set=ql_cmp_workload=1
else
  produce | mysql --host=mysql --port=3306 --user=app_user --password=app_pass \
    --database=ql_demo --force --prompt=ql_cmp_workload >/dev/null 2>&1
fi
