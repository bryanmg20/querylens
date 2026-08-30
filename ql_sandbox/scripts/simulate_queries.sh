#!/usr/bin/env bash

set -euo pipefail

export PGPASSWORD="app_pass"

PSQL=(
  psql
  --host=postgres
  --port=5432
  --username=app_user
  --dbname=ql_demo
  --set=ON_ERROR_STOP=1
)

for _ in $(seq 1 100); do
  "${PSQL[@]}" --command="SELECT count(*) FROM sbtest1 WHERE k BETWEEN 1 AND 5000;" >/dev/null
  "${PSQL[@]}" --command="SELECT c, count(*) FROM sbtest1 GROUP BY c ORDER BY count(*) DESC;" >/dev/null
  "${PSQL[@]}" --command="SELECT pg_sleep(CASE WHEN random() < 0.05 THEN 1 ELSE 0.01 END);" >/dev/null
  "${PSQL[@]}" --command="SELECT count(*) FROM sbtest1 WHERE c LIKE '%a%';" >/dev/null
  "${PSQL[@]}" --command="SET work_mem = '64kB'; SELECT c FROM sbtest1 ORDER BY c;" >/dev/null
done

printf 'Consultas ejecutadas como app_user.\n'
