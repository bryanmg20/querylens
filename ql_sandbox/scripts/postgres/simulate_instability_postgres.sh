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

for _ in $(seq 1 1000); do
  "${POSTGRES[@]}" "
    SELECT pg_sleep(
      CASE WHEN random() < 0.05 THEN 1.0 ELSE 0.01 END
    );
  " >/dev/null
done

printf 'Consultas ejecutadas como app_user en PostgreSQL.\n'