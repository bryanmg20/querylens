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

run_locker() {
  local range_start=$1
  local range_end=$2
  while true; do
    "${POSTGRES[@]}" "
      BEGIN;
      SELECT * FROM sbtest1 WHERE id BETWEEN ${range_start} AND ${range_end} FOR UPDATE;
      SELECT pg_sleep(2);
      COMMIT;
    " >/dev/null || true
    sleep 0.1
  done
}

for i in {1..5}; do
  run_locker 1 10 &
done

echo "Procesos concurrentes con SELECT FOR UPDATE lanzados en PostgreSQL."
wait