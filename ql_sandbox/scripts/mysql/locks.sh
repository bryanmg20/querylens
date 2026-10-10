#!/usr/bin/env bash

set -euo pipefail

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=app_user
  --password=app_pass
  --database=ql_demo
  --silent
  --skip-column-names
  -e
)

run_locker() {
  local range_start=$1
  local range_end=$2
  while true; do
    "${MYSQL[@]}" "
      START TRANSACTION;
      SELECT * FROM sbtest1 WHERE id BETWEEN ${range_start} AND ${range_end} FOR UPDATE;
      DO SLEEP(2);
      COMMIT;
    " >/dev/null || true
    sleep 0.1
  done
}

for i in {1..5}; do
  run_locker 1 10 &
done

echo "Procesos concurrentes con SELECT FOR UPDATE lanzados en MySQL."
wait