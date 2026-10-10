#!/usr/bin/env bash
# Rompe a proposito la configuracion de MySQL para ver los issues de
# pipeline_health (telemetry_pipeline/health/) y luego la restaura.
#
#   docker exec ql_sysbench bash /scripts/mysql/simulate_health_mysql.sh disable-consumers
#   docker exec ql_sysbench bash /scripts/mysql/simulate_health_mysql.sh revoke-process
#   docker exec ql_sysbench bash /scripts/mysql/simulate_health_mysql.sh restore
#
# Que deberia abrir el pipeline:
#   disable-consumers -> PS_CONSUMER_DISABLED (degraded; es del preflight,
#                        aparece cuando vence HEALTH_PREFLIGHT_TTL_S, 300 s)
#   revoke-process    -> MISSING_PROCESS_PRIVILEGE (degraded; en el ciclo
#                        siguiente, porque active_queries falla con 1227)
#   restore           -> ambos quedan con resolved_at (PROCESS en el ciclo
#                        siguiente, los consumers en el siguiente preflight)
#
# setup_consumers no persiste: un reinicio de ql_mysql vuelve a los flags del
# docker-compose del sandbox.

set -euo pipefail

# setup_consumers y los GRANT necesitan root, no app_user.
export MYSQL_PWD=ql_root

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=root
  -e
)

CONSUMERS="'statements_digest', 'events_statements_current'"

case "${1:-}" in
  disable-consumers)
    "${MYSQL[@]}" "UPDATE performance_schema.setup_consumers SET ENABLED = 'NO' WHERE NAME IN (${CONSUMERS});"
    printf 'Consumers %s desactivados en MySQL.\n' "${CONSUMERS}"
    ;;
  revoke-process)
    "${MYSQL[@]}" "REVOKE PROCESS ON *.* FROM 'querylens_monitor'@'%';"
    printf 'PROCESS revocado a querylens_monitor.\n'
    ;;
  restore)
    "${MYSQL[@]}" "
      UPDATE performance_schema.setup_consumers SET ENABLED = 'YES' WHERE NAME IN (${CONSUMERS});
      GRANT PROCESS ON *.* TO 'querylens_monitor'@'%';
    "
    printf 'Consumers y PROCESS restaurados en MySQL.\n'
    ;;
  *)
    printf 'uso: %s {disable-consumers|revoke-process|restore}\n' "$0" >&2
    exit 2
    ;;
esac
