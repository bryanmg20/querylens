#!/usr/bin/env bash
# ============================================================
# QUERYLENS — continuous load for overhead measurement
#
# Runs a while-true loop of varied impactful queries against
# PostgreSQL and/or MySQL. Each query is TIMED and appended to
# a metrics CSV so measure_overhead.py can compute latency
# (p50/p95) and QPS of the observed workload.
#
# USAGE (from the host, via docker exec):
#   docker exec -d ql_sysbench bash /scripts/continuous_load.sh [ENGINE] [DURATION_S]
#   ENGINE = postgres | mysql | both (default both)
#   DURATION_S = seconds to run; 0 = until killed (default 0)
#
# Metrics file (inside the container):
#   /tmp/ql_load_metrics.csv
#   header: ts_ms,engine,duration_ms,status
#   status 0 = OK, other = client error
#
# PID: /tmp/ql_load.pid
#   docker exec ql_sysbench bash -c 'kill $(cat /tmp/ql_load.pid)'
# ============================================================

set -uo pipefail

ENGINE="${1:-both}"
DURATION_S="${2:-0}"

if [[ "$ENGINE" != "both" && "$ENGINE" != "postgres" && "$ENGINE" != "mysql" ]]; then
  echo "ENGINE debe ser postgres, mysql o both (recibido: $ENGINE)" >&2
  exit 1
fi

export PGPASSWORD=ql_pass

PSQL=(psql --host=postgres --port=5432 --username=ql_user --dbname=ql_demo --no-psqlrc --quiet --tuples-only --command)
MYSQL=(mysql --host=mysql --port=3306 --user=app_user --password=app_pass --database=ql_demo --batch --skip-column-names -e)

METRICS=/tmp/ql_load_metrics.csv
echo "ts_ms,engine,duration_ms,status" > "$METRICS"

# Misma carga observada por el pipeline (mismos tipos que battery.sh)
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

echo $$ > /tmp/ql_load.pid

run_pg() {
  [[ "$ENGINE" == "both" || "$ENGINE" == "postgres" ]] || return 0
  (
    t0=$(date +%s%3N)
    "${PSQL[@]}" "$1" >/dev/null 2>&1
    rc=$?
    t1=$(date +%s%3N)
    echo "$t1,postgres,$((t1 - t0)),$rc" >> "$METRICS"
  ) &
}

run_my() {
  [[ "$ENGINE" == "both" || "$ENGINE" == "mysql" ]] || return 0
  (
    t0=$(date +%s%3N)
    "${MYSQL[@]}" "$1" >/dev/null 2>&1
    rc=$?
    t1=$(date +%s%3N)
    echo "$t1,mysql,$((t1 - t0)),$rc" >> "$METRICS"
  ) &
}

START=$(date +%s)
ITERS=0
while true; do
  for q in "${QUERIES[@]}"; do
    run_pg "$q"
    run_my "$q"
    wait
  done
  ITERS=$((ITERS + 1))
  if (( DURATION_S > 0 )); then
    NOW=$(date +%s)
    if (( NOW - START >= DURATION_S )); then
      echo "continuous_load done (engine=$ENGINE iters=$ITERS)"
      break
    fi
  fi
done
