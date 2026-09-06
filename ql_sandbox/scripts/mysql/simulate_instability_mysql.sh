#!/usr/bin/env bash

set -euo pipefail

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=app_user
  --password=app_pass
  --database=ql_demo
)

for _ in $(seq 1 1000); do
  "${MYSQL[@]}" --execute="SELECT SLEEP(CASE WHEN RAND() < 0.05 THEN 1 ELSE 0.01 END);" >/dev/null
done

printf 'Consultas ejecutadas como app_user.\n'