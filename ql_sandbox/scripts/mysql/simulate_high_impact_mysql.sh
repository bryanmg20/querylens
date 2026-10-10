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
    SELECT DISTINCT
        a.c,
        COUNT(*) AS total
    FROM sbtest1 AS a
    JOIN sbtest1 AS b
        ON b.id = a.id
    WHERE a.k > 100
      AND a.id = (
          SELECT MIN(x.id)
          FROM sbtest1 AS x
          WHERE x.c = a.c
      )
    GROUP BY a.c
    ORDER BY COUNT(*) DESC;
  " >/dev/null
done