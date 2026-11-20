#!/usr/bin/env bash
# ============================================================
# QUERYLENS — carga base del banco de pruebas
#
# La carga transaccional sobre la que se inyectan las patologias y la unica
# que corre en las corridas de control (SegundoInforme 3.3): sysbench con el
# perfil oltp_read_write y 4 hilos, sobre las tablas sbtest1..sbtest10 que
# crea /scripts/run.sh (si faltan, las crea).
#
# La lanza ql_sandbox/banco/banco.py dentro de ql_sysbench; a mano:
#   docker exec -d ql_sysbench bash /scripts/base_load.sh <postgres|mysql> [tx_por_segundo]
#
# Corre hasta que se la detiene (--time=0). banco.py la detiene terminando el
# proceso sysbench, que reconoce por "oltp_read_write", el driver del motor y
# "--time=0" en su linea de comandos (el contenedor no trae pkill).
#
# tx_por_segundo (por defecto 50): tasa fija de transacciones entre los 4
# hilos. Con tasa fija la latencia de la carga base no depende de cuanta CPU
# le deje el resto, y una misma corrida se puede repetir en otra maquina.
# ============================================================

set -uo pipefail

ENGINE="${1:?uso: base_load.sh <postgres|mysql> [tx_por_segundo]}"
RATE="${2:-50}"
TABLES=10
TABLE_SIZE=10000
THREADS=4

if [ "$ENGINE" = "postgres" ]; then
  CONNECTION=(--db-driver=pgsql --pgsql-host="${PG_HOST:-postgres}" --pgsql-port=5432
              --pgsql-user=ql_user --pgsql-password=ql_pass --pgsql-db=ql_demo)
  export PGPASSWORD=ql_pass
  exists=$(psql --host="${PG_HOST:-postgres}" --username=ql_user --dbname=ql_demo --no-psqlrc -At \
           -c "SELECT count(*) FROM pg_tables WHERE tablename = 'sbtest${TABLES}';")
elif [ "$ENGINE" = "mysql" ]; then
  CONNECTION=(--db-driver=mysql --mysql-host="${MYSQL_HOST:-mysql}" --mysql-port=3306
              --mysql-user=app_user --mysql-password=app_pass --mysql-db=ql_demo)
  exists=$(mysql --host="${MYSQL_HOST:-mysql}" --user=app_user --password=app_pass -N \
           -e "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'ql_demo' AND table_name = 'sbtest${TABLES}';" 2>/dev/null)
else
  echo "motor desconocido: $ENGINE (postgres | mysql)" >&2
  exit 1
fi

if [ "${exists:-0}" != "1" ]; then
  sysbench oltp_read_write "${CONNECTION[@]}" --tables=$TABLES --table-size=$TABLE_SIZE cleanup >/dev/null 2>&1 || true
  sysbench oltp_read_write "${CONNECTION[@]}" --tables=$TABLES --table-size=$TABLE_SIZE prepare >/dev/null || exit 1
fi

# --time=0: hasta que la detengan. exec: el proceso que queda es sysbench, con
# la linea de comandos por la que banco.py lo encuentra.
# --db-ps-mode=disable: sin sentencias preparadas del lado del servidor. MySQL
# no calcula el digest de sus ejecuciones (COM_STMT_EXECUTE), asi que con
# ellas la carga no apareceria en events_statements_summary_by_digest y el
# recolector no la veria. Se usa el mismo modo en los dos motores.
exec sysbench oltp_read_write "${CONNECTION[@]}" \
  --tables=$TABLES --table-size=$TABLE_SIZE --threads=$THREADS \
  --db-ps-mode=disable \
  --time=0 --rate="$RATE" --report-interval=0 run >/dev/null 2>&1
