#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-05 (cascada de bloqueo por transaccion larga)
#
# Tabla 3 del informe: una sesion abre una transaccion, actualiza una fila y
# permanece inactiva HOLD_SECONDS sin confirmar, mientras WAITERS sesiones
# intentan actualizar la misma fila.
#
# La raiz queda en Sleep con la transaccion abierta: el cliente mysql ejecuta
# el UPDATE y se queda esperando mas entrada por stdin.
#
# Las bloqueadas suben innodb_lock_wait_timeout (solo en su sesion): con los
# 50 s por defecto abandonarian la espera antes de que termine la raiz.
#
# Esperado: hallazgo long_transaction_cascade con la sesion raiz que se
# imprime abajo, su antiguedad y estado, y WAITERS sesiones bloqueadas, a
# mas tardar 45 s despues de que la raiz cumple 30 s (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/mysql/simulate_ct05_long_transaction_mysql.sh
#   HOLD_SECONDS=60 WAITERS=3 docker exec -e HOLD_SECONDS -e WAITERS ql_sysbench bash /scripts/mysql/simulate_ct05_long_transaction_mysql.sh
# ============================================================

set -euo pipefail

HOLD_SECONDS="${HOLD_SECONDS:-120}"
WAITERS="${WAITERS:-5}"

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=app_user
  --password=app_pass
  --database=ql_demo
  --batch
  --skip-column-names
)

# Tabla propia: la carga de sysbench no la toca, asi que las unicas sesiones
# bloqueadas son las del escenario
"${MYSQL[@]}" --execute="
  CREATE TABLE IF NOT EXISTS ql_ct05_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL) ENGINE=InnoDB;
  INSERT IGNORE INTO ql_ct05_cuentas VALUES (1, 100);
" 2>/dev/null

ROOT_OUT=$(mktemp)
trap 'rm -f "$ROOT_OUT"' EXIT

# Raiz: toma el lock de la fila y se queda sin enviar nada hasta el ROLLBACK
{
  echo "SELECT CONNECTION_ID();"
  echo "START TRANSACTION;"
  echo "UPDATE ql_ct05_cuentas SET saldo = saldo - 10 WHERE id = 1;"
  sleep "$HOLD_SECONDS"
  echo "ROLLBACK;"
} | "${MYSQL[@]}" --unbuffered >"$ROOT_OUT" 2>/dev/null &
ROOT_JOB=$!

sleep 1
ROOT_PID=$(head -n 1 "$ROOT_OUT")
START=$(date -u '+%Y-%m-%d %H:%M:%S')
echo "[CT-05/mysql] raiz pid=${ROOT_PID} en Sleep con transaccion abierta desde ${START} UTC (${HOLD_SECONDS} s)"
echo "[CT-05/mysql] la raiz cumple 30 s a las $(date -u -d "@$(( $(date +%s) + 30 ))" '+%H:%M:%S') UTC; hallazgo esperado antes de las $(date -u -d "@$(( $(date +%s) + 75 ))" '+%H:%M:%S') UTC"

LOCK_WAIT=$(( HOLD_SECONDS + 60 ))
for i in $(seq 1 "$WAITERS"); do
  "${MYSQL[@]}" --execute="
    SET SESSION innodb_lock_wait_timeout = ${LOCK_WAIT};
    UPDATE ql_ct05_cuentas SET saldo = saldo + ${i} WHERE id = 1;
  " 2>/dev/null &
done
echo "[CT-05/mysql] ${WAITERS} sesiones esperando la fila id=1"

wait "$ROOT_JOB"
wait
echo "[CT-05/mysql] raiz revertida a las $(date -u '+%H:%M:%S') UTC; las bloqueadas terminaron"
