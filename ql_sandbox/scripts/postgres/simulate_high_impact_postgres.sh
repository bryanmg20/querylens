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

while true; do
  "${POSTGRES[@]}" "
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

printf 'Consultas ejecutadas como ql_user en PostgreSQL.\n'