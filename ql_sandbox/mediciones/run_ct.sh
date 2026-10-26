#!/usr/bin/env bash
# Corrida completa de la medicion de contencion (CT-01 a CT-05) en un solo
# comando: levanta el sandbox, pone al dia las imagenes del stack y corre
# measure_ct.py para Postgres y MySQL en paralelo, como en las corridas de
# referencia. Cada motor deja su carpeta en resultados/ct-<motor>-<fecha>/.
#
#   bash ql_sandbox/mediciones/run_ct.sh            # 10 repeticiones por caso (~2 h)
#   bash ql_sandbox/mediciones/run_ct.sh 5          # 5 repeticiones (~1 h)
#   bash ql_sandbox/mediciones/run_ct.sh 2 postgres # un solo motor
#
# Requiere el .env de la raiz (docker-compose.yml) y python3. Sale con error
# si alguno de los dos motores falla.
set -euo pipefail

REPS="${1:-10}"
ENGINES="${2:-postgres mysql}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RESULTS="$ROOT/ql_sandbox/mediciones/resultados"
STAMP="$(date +%Y%m%d-%H%M%S)"

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "sandbox: docker compose up -d"
docker compose -f "$ROOT/ql_sandbox/docker-compose.yml" up -d

# ql_sysbench instala psql 17 al arrancar (MODE=bind usa \bind, psql 16+)
log "esperando psql 17 en ql_sysbench"
for _ in $(seq 1 120); do
  if docker exec ql_sysbench psql --version 2>/dev/null | grep -q ' 1[6-9]\.'; then
    break
  fi
  sleep 5
done
docker exec ql_sysbench psql --version | grep -q ' 1[6-9]\.' || { log "ql_sysbench sin psql 16+"; exit 1; }

# Imagenes al dia: el pipeline y el detector con el codigo de la rama, y la
# CLI, que lleva report.sql dentro. El up corre tambien db-roles (GRANTs).
log "stack: build cli y up --build pipeline detection-engine"
docker compose -f "$ROOT/docker-compose.yml" build cli
docker compose -f "$ROOT/docker-compose.yml" up -d --build pipeline detection-engine

mkdir -p "$RESULTS"
pids=()
for engine in $ENGINES; do
  out="$RESULTS/run-ct-$engine-$STAMP.log"
  log "$engine: measure_ct.py --reps $REPS (log: $out)"
  python3 "$ROOT/ql_sandbox/mediciones/measure_ct.py" --engine "$engine" --reps "$REPS" >"$out" 2>&1 &
  pids+=("$!")
  # cada motor registra su base con la CLI: no al mismo tiempo
  sleep 5
done

status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
for engine in $ENGINES; do
  grep -h "RESULTADO CT" "$RESULTS/run-ct-$engine-$STAMP.log" || log "$engine: sin reporte, ver $RESULTS/run-ct-$engine-$STAMP.log"
done
exit "$status"
