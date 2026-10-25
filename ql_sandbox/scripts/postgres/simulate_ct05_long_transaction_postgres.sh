#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-05 (cascada de bloqueo por transaccion larga)
#
# Tabla 3 del informe: una sesion abre una transaccion, actualiza una fila y
# permanece inactiva HOLD_SECONDS sin confirmar, mientras WAITERS sesiones
# intentan actualizar la misma fila.
#
# La raiz queda 'idle in transaction' de verdad: psql ejecuta el UPDATE y se
# queda esperando mas entrada por stdin. Con pg_sleep() la sesion figuraria
# 'active', que no es el caso que se quiere reproducir.
#
# Esperado: hallazgo long_transaction_cascade con la sesion raiz que se
# imprime abajo, su antiguedad y estado, y WAITERS sesiones bloqueadas, a
# mas tardar 45 s despues de que la raiz cumple 30 s (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/postgres/simulate_ct05_long_transaction_postgres.sh
#   HOLD_SECONDS=60 WAITERS=3 docker exec -e HOLD_SECONDS -e WAITERS ql_sysbench bash /scripts/postgres/simulate_ct05_long_transaction_postgres.sh
# ============================================================

set -euo pipefail

HOLD_SECONDS="${HOLD_SECONDS:-120}"
WAITERS="${WAITERS:-5}"

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
  --no-align
)

# Tabla propia: la carga de sysbench no la toca, asi que las unicas sesiones
# bloqueadas son las del escenario
"${POSTGRES[@]}" --command="
  CREATE TABLE IF NOT EXISTS ql_ct05_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
  INSERT INTO ql_ct05_cuentas VALUES (1, 100) ON CONFLICT (id) DO NOTHING;
" >/dev/null

ROOT_OUT=$(mktemp)
trap 'rm -f "$ROOT_OUT"' EXIT

# Raiz: toma el lock de la fila y se queda sin enviar nada hasta el ROLLBACK
{
  echo "SELECT pg_backend_pid();"
  echo "BEGIN;"
  echo "UPDATE ql_ct05_cuentas SET saldo = saldo - 10 WHERE id = 1;"
  sleep "$HOLD_SECONDS"
  echo "ROLLBACK;"
} | "${POSTGRES[@]}" >"$ROOT_OUT" &
ROOT_JOB=$!

sleep 1
ROOT_PID=$(head -n 1 "$ROOT_OUT")
START=$(date -u '+%Y-%m-%d %H:%M:%S')
echo "[CT-05/postgres] raiz pid=${ROOT_PID} idle in transaction desde ${START} UTC (${HOLD_SECONDS} s)"
echo "[CT-05/postgres] la raiz cumple 30 s a las $(date -u -d "@$(( $(date +%s) + 30 ))" '+%H:%M:%S') UTC; hallazgo esperado antes de las $(date -u -d "@$(( $(date +%s) + 75 ))" '+%H:%M:%S') UTC"

# Bloqueadas: cada una espera el lock de la fila (lock_timeout = 0, sin limite)
for i in $(seq 1 "$WAITERS"); do
  "${POSTGRES[@]}" --command="UPDATE ql_ct05_cuentas SET saldo = saldo + ${i} WHERE id = 1;" >/dev/null &
done
echo "[CT-05/postgres] ${WAITERS} sesiones esperando la fila id=1"

wait "$ROOT_JOB"
wait
echo "[CT-05/postgres] raiz revertida a las $(date -u '+%H:%M:%S') UTC; las bloqueadas terminaron"
