#!/usr/bin/env bash

set -euo pipefail

export PGPASSWORD=ql_pass

POSTGRES=(
  psql
  --host=postgres
  --port=5432
  --username=ql_user
  --dbname=ql_demo
  --no-psqlrc
  --quiet
  --tuples-only
  --command
)

for _ in $(seq 1 100); do
  "${POSTGRES[@]}" "
    SET work_mem = '64kB';
    SELECT c, COUNT(*)
    FROM sbtest1
    GROUP BY c
    HAVING COUNT(*) > 0
    ORDER BY c;
  " >/dev/null
done

printf 'Consultas ejecutadas como app_user en PostgreSQL.\n'