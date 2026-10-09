#!/usr/bin/env bash
# ============================================================
# QUERYLENS — carga de un escenario de prueba de antipatrones
#
# Lo lanza ql_sandbox/scenarios/run_scenario.py dentro del contenedor
# ql_sysbench; no hace falta correrlo a mano.
#
# USO
#   bash /scripts/scenarios/workload.sh <motor> <tipo> <segundos>
#     motor: postgres | mysql
#     tipo:  full_scan | non_sargable | disk_spill | point_lookup | correlated
#
# Cada worker abre UNA sola sesion y le envia consultas sin pausa: asi la
# consulta esta casi siempre ejecutandose, que es lo que necesita el pipeline
# para encontrarla en active_queries y hacerle EXPLAIN. Termina solo al pasar
# <segundos>, o antes si existe STOP_FILE (el contenedor no trae pkill, asi que
# el orquestador crea ese archivo para detener la carga). Como el pipe puede
# tener cientos de consultas encoladas, el orquestador ademas termina el
# cliente psql/mysql, que lleva la marca ql_scn_workload en su linea de comandos.
# ============================================================

set -uo pipefail

ENGINE="$1"
KIND="$2"
MAX_SECONDS="$3"
STOP_FILE=/tmp/ql_scn_stop

rand() {
  # RANDOM llega hasta 32767; se combinan dos para cubrir los 100.000 valores de k y c
  echo $(( (RANDOM * 32768 + RANDOM) % 100000 ))
}

session_setup() {
  case "$ENGINE:$KIND" in
    postgres:disk_spill) echo "SET work_mem = '64kB';" ;;
    # en MySQL no alcanza con la sesion: el orquestador apaga temptable_use_mmap
    # (global, solo durante el escenario) para que el desborde vaya a disco
    mysql:disk_spill)    echo "SET SESSION sort_buffer_size = 32768;" ;;
  esac
}

query() {
  case "$KIND" in
    # c no tiene indice: escaneo completo para devolver ~10 de 1.000.000 filas
    full_scan)    echo "SELECT id, pad FROM ql_scn WHERE c = $(rand);" ;;
    # k tiene indice, pero la aritmetica sobre la columna impide usarlo
    non_sargable) echo "SELECT id, pad FROM ql_scn WHERE k + 0 = $(rand);" ;;
    # agrupa 100.000 valores distintos: no entra en la memoria de trabajo reducida
    disk_spill)   echo "SELECT c, COUNT(*) FROM ql_scn GROUP BY c ORDER BY c;" ;;
    # busqueda puntual por k: rapida con el indice, lenta si se elimina
    point_lookup) echo "SELECT pad FROM ql_scn WHERE k = $(rand);" ;;
    # a = b = c: el optimizador multiplica las tres selectividades (1/1000) y
    # estima ~100 filas, pero las tres condiciones son una sola y vuelven ~10.000
    correlated)   v=$(( RANDOM % 10 )); echo "SELECT id FROM ql_scn_corr WHERE a = $v AND b = $v AND c = $v;" ;;
    *) echo "tipo de carga desconocido: $KIND" >&2; exit 1 ;;
  esac
}

produce() {
  session_setup
  # al cortar el envio, psql/mysql termina la consulta en curso y sale solo
  while (( SECONDS < MAX_SECONDS )) && [ ! -e "$STOP_FILE" ]; do
    query
  done
}

if [ "$ENGINE" = "postgres" ]; then
  export PGPASSWORD=ql_pass
  produce | psql --host=postgres --port=5432 --username=ql_user --dbname=ql_demo \
    --no-psqlrc --quiet --output=/dev/null --set=ql_scn_workload=1
else
  produce | mysql --host=mysql --port=3306 --user=app_user --password=app_pass \
    --database=ql_demo --force --prompt=ql_scn_workload >/dev/null 2>&1
fi
