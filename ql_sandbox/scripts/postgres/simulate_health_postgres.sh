#!/usr/bin/env bash
# Rompe a proposito la configuracion de PostgreSQL para ver los issues de
# pipeline_health (telemetry_pipeline/health/) y luego la restaura.
#
#   docker exec ql_sysbench bash /scripts/postgres/simulate_health_postgres.sh drop-extension
#   docker exec ql_sysbench bash /scripts/postgres/simulate_health_postgres.sh revoke-stats
#   docker exec ql_sysbench bash /scripts/postgres/simulate_health_postgres.sh restore
#
# Que deberia abrir el pipeline en el ciclo siguiente:
#   drop-extension -> PG_STATEMENTS_NOT_INSTALLED (blocking, seccion statements)
#   revoke-stats   -> MISSING_PG_READ_ALL_STATS (degraded) cuando haya queries
#                     de otros roles en pg_stat_statements / pg_stat_activity
#   restore        -> el ciclo siguiente marca resolved_at en ambos
#
# drop-extension borra las estadisticas acumuladas de pg_stat_statements.

set -euo pipefail

export PGPASSWORD=ql_pass
export PGOPTIONS="-c client_min_messages=warning"

POSTGRES=(
  psql
  --host=postgres
  --port=5432
  --username=ql_user
  --dbname=ql_demo
  --no-psqlrc
  --quiet
  --set=ON_ERROR_STOP=1
  --command
)

case "${1:-}" in
  drop-extension)
    "${POSTGRES[@]}" "DROP EXTENSION IF EXISTS pg_stat_statements;"
    printf 'pg_stat_statements eliminada en PostgreSQL.\n'
    ;;
  revoke-stats)
    "${POSTGRES[@]}" "REVOKE pg_read_all_stats FROM querylens_monitor;"
    printf 'pg_read_all_stats revocado a querylens_monitor.\n'
    ;;
  restore)
    "${POSTGRES[@]}" "
      CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
      GRANT pg_read_all_stats TO querylens_monitor;
    "
    printf 'pg_stat_statements y pg_read_all_stats restaurados.\n'
    ;;
  *)
    printf 'uso: %s {drop-extension|revoke-stats|restore}\n' "$0" >&2
    exit 2
    ;;
esac
