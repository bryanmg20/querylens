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
  "${MYSQL[@]}" --execute="SELECT c, count(*) FROM sbtest1 GROUP BY c ORDER BY count(*) DESC, SLEEP(1);" >/dev/null
  "${MYSQL[@]}" --execute="SELECT DISTINCT c FROM sbtest1 WHERE id BETWEEN 1 AND 5000 ORDER BY c, SLEEP(1);" >/dev/null
  "${MYSQL[@]}" --execute="SELECT count(*) FROM sbtest1 WHERE c LIKE '%a%', SLEEP(1);" >/dev/null
done