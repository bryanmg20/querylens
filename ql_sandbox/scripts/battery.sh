#!/usr/bin/env bash
# ============================================================
# QUERYLENS — battery of varied impactful queries
#
# Runs ~10 different statement types against PostgreSQL and
# MySQL so they rank in the top-impact candidates of the
# pipeline, then they should be recovered as "real queries"
# from the logs.
#
# USAGE (from the sysbench container):
#   bash /scripts/battery.sh [REPS]
#   (default REP iteration count per query = 50)
#
# It also gives a CLEAN measurement window:
#   - truncates both logs (pg csvlog + mysql slow log)
#   - resets pg_stat_statements / digest summary
# so that:  log window == stats window == battery window.
# ============================================================

set -euo pipefail

REPS="${1:-50}"

export PGPASSWORD=ql_pass

PSQL=(psql --host=postgres --port=5432 --username=ql_user --dbname=ql_demo --no-psqlrc --quiet --tuples-only --command)
MYSQL=(mysql --host=mysql --port=3306 --user=app_user --password=app_pass --database=ql_demo --batch --skip-column-names -e)

echo ">>> Truncating log windows..."
truncate -s 0 /pg_logs/postgresql.csv 2>/dev/null || true
truncate -s 0 /mysql_logs/ql-slow.log 2>/dev/null || true

echo ">>> Resetting stats windows..."
"${PSQL[@]}" "SELECT pg_stat_statements_reset();" >/dev/null
mysql --host=mysql --port=3306 --user=root --password=ql_root \
  -e "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;" >/dev/null

QUERIES=(
  "SELECT a.c, COUNT(*) AS total FROM sbtest1 AS a JOIN sbtest1 AS b ON b.id = a.id WHERE a.k > 1000 GROUP BY a.c HAVING COUNT(*) > 1 ORDER BY total DESC LIMIT 50;"
  "SELECT id, k, ROW_NUMBER() OVER (PARTITION BY k ORDER BY c) AS rn FROM sbtest1 ORDER BY rn DESC LIMIT 100;"
  "SELECT 'x' AS tipo, SUM(k) AS s FROM sbtest1 UNION ALL SELECT 'y', AVG(id) FROM sbtest1 UNION ALL SELECT 'z', COUNT(*) FROM sbtest1;"
  "SELECT SUBSTRING(c, 1, 8) AS pref, COUNT(*) AS cnt FROM sbtest1 WHERE c LIKE '%7%' GROUP BY pref ORDER BY cnt DESC LIMIT 50;"
  "SELECT COUNT(*) FROM sbtest1 a WHERE a.id = (SELECT MAX(b.id) FROM sbtest1 b WHERE b.k = a.k);"
  "SELECT a.id, b.id FROM sbtest1 a JOIN sbtest1 b ON b.c = a.c WHERE a.id BETWEEN 2000 AND 4000 ORDER BY a.id, b.id LIMIT 100;"
  "SELECT id, CASE WHEN k % 3 = 0 THEN 'A' WHEN k % 3 = 1 THEN 'B' ELSE 'C' END AS bucket FROM sbtest1 ORDER BY bucket, id DESC LIMIT 200;"
  "SELECT a.k, COUNT(*) AS cnt FROM sbtest1 a JOIN sbtest1 b ON b.id = a.id JOIN sbtest1 c ON c.id = a.id WHERE a.k < 5000 GROUP BY a.k ORDER BY cnt DESC, a.k LIMIT 100;"
  "SELECT DISTINCT k, c FROM sbtest1 WHERE id BETWEEN 1 AND 8000 ORDER BY k LIMIT 500;"
  "SELECT a.id, (SELECT COUNT(*) FROM sbtest1 b WHERE b.k = a.k) AS cnt FROM sbtest1 a WHERE a.id > 3000 ORDER BY cnt DESC, a.id LIMIT 100;"
)

echo ">>> Running ${#QUERIES[@]} varied queries x $REPS (postgres + mysql)..."
for i in $(seq 1 "$REPS"); do
  for q in "${QUERIES[@]}"; do
    "${PSQL[@]}" "$q" >/dev/null &
    "${MYSQL[@]}" "$q" >/dev/null &
    wait
  done
  if (( i % 5 == 0 )); then echo "  rep $i/$REPS"; fi
done

echo ">>> Battery done."