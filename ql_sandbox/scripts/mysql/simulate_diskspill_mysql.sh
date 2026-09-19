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

while true; do
  "${MYSQL[@]}" --execute="
    SELECT * FROM (SELECT DISTINCT c FROM sbtest1 LIMIT 100000) t ORDER BY t.c, RAND();
" >/dev/null
done

printf 'Consultas ejecutadas como app_user.\n'