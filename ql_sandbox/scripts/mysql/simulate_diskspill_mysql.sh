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

for _ in $(seq 1 100); do
  "${MYSQL[@]}" --execute="
    SET SESSION tmp_table_size = 1024;
    SET SESSION max_heap_table_size = 1024;
    SELECT c, COUNT(*) FROM sbtest1 GROUP BY c HAVING COUNT(*) > 0 ORDER BY c;
" >/dev/null
done

printf 'Consultas ejecutadas como app_user.\n'