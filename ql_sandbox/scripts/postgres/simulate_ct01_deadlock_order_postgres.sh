#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-01 (abrazo mortal por orden inconsistente de acceso)
#
# Tabla 3 del informe: dos sesiones actualizan las filas A y B en ordenes
# opuestos con una pausa entre ambas sentencias. La sesion 1 toma A y pide B;
# la sesion 2 toma B y pide A. Tras deadlock_timeout (1 s en el sandbox)
# Postgres detecta el ciclo, revierte una de las dos (la victima recibe
# "deadlock detected") y escribe el informe en el log del servidor, de donde
# lo lee DeadlocksStage.
#
# Esperado: hallazgo inconsistent_lock_order_deadlock con el ciclo de dos
# transacciones (los pid que se imprimen abajo), la tabla ql_ct01_cuentas y la
# recomendacion de un orden de acceso unico, a mas tardar 15 s despues del
# abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"

export PGPASSWORD=ql_pass
# sin el NOTICE de CREATE TABLE IF NOT EXISTS en cada corrida
export PGOPTIONS='-c client_min_messages=warning'

POSTGRES=(
  psql
  --host=postgres
  --port=5432
  --username=ql_user
  --dbname=ql_demo
  --no-psqlrc
  --quiet
  --tuples-only
  --no-align
)

# Tabla propia: la carga de sysbench no la toca, asi que el unico ciclo es el
# del escenario
"${POSTGRES[@]}" --command="
  CREATE TABLE IF NOT EXISTS ql_ct01_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
  INSERT INTO ql_ct01_cuentas VALUES (1, 100), (2, 100) ON CONFLICT (id) DO NOTHING;
" >/dev/null

OUT_1=$(mktemp)
OUT_2=$(mktemp)
trap 'rm -f "$OUT_1" "$OUT_2"' EXIT

# $1 primera fila, $2 segunda fila, $3 archivo de salida
session() {
  {
    echo "SELECT pg_backend_pid();"
    echo "BEGIN;"
    echo "UPDATE ql_ct01_cuentas SET saldo = saldo - 10 WHERE id = $1;"
    sleep "$PAUSE_SECONDS"
    echo "UPDATE ql_ct01_cuentas SET saldo = saldo + 10 WHERE id = $2;"
    sleep 1
    # la victima ya esta revertida: este ROLLBACK solo cierra la de la otra
    echo "ROLLBACK;"
  } | "${POSTGRES[@]}" >"$3" 2>&1 || true
}

session 1 2 "$OUT_1" &
# la segunda arranca despues: cuando pide la fila 1, la sesion 1 ya la tiene
sleep 0.5
session 2 1 "$OUT_2" &

sleep 1
echo "[CT-01/postgres] sesion 1 pid=$(head -n 1 "$OUT_1"): fila 1 y despues fila 2"
echo "[CT-01/postgres] sesion 2 pid=$(head -n 1 "$OUT_2"): fila 2 y despues fila 1"
wait

AT=$(date +%s)
VICTIM=$(grep -l "deadlock detected" "$OUT_1" "$OUT_2" 2>/dev/null | head -n 1 || true)
if [ -z "$VICTIM" ]; then
  echo "[CT-01/postgres] no hubo abrazo mortal: subir PAUSE_SECONDS (> deadlock_timeout)" >&2
  exit 1
fi
echo "[CT-01/postgres] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $([ "$VICTIM" = "$OUT_1" ] && echo 1 || echo 2)"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-01/postgres] hallazgo esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
