#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-01 (abrazo mortal por orden inconsistente de acceso)
#
# Tabla 3 del informe: dos sesiones actualizan las filas A y B en ordenes
# opuestos con una pausa entre ambas sentencias. La sesion 1 toma A y pide B;
# la sesion 2 toma B y pide A. InnoDB detecta el ciclo al instante, revierte
# una de las dos (la victima recibe ERROR 1213) y deja el informe en
# SHOW ENGINE INNODB STATUS, de donde lo lee DeadlocksStage.
#
# SESSIONS=3 cierra el ciclo con tres sesiones: la i toma la fila i y pide la
# siguiente, y la ultima pide la fila 1.
#
# DBNAME corre el escenario en otro schema del mismo servidor (tiene que
# existir y app_user necesita permisos en el). InnoDB no separa por schema ni
# el informe ni el contador lock_deadlocks.
#
# INNODB STATUS guarda solo el ultimo abrazo: si se corre el escenario dos
# veces dentro del mismo ciclo del pipeline (10 s), el primero llega sin
# detalle y no se clasifica. Dejar al menos un ciclo entre corridas.
#
# Esperado: hallazgo inconsistent_lock_order_deadlock con el ciclo de
# SESSIONS transacciones (los id de conexion que se imprimen abajo), la tabla
# ql_demo.ql_ct01_cuentas, el indice PRIMARY, los modos X,REC_NOT_GAP y la
# recomendacion de un orden de acceso unico, a mas tardar 15 s despues del
# abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/mysql/simulate_ct01_deadlock_order_mysql.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/mysql/simulate_ct01_deadlock_order_mysql.sh
#   SESSIONS=3 docker exec -e SESSIONS ql_sysbench bash /scripts/mysql/simulate_ct01_deadlock_order_mysql.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
SESSIONS="${SESSIONS:-2}"
DBNAME="${DBNAME:-ql_demo}"

case "$SESSIONS" in
  2 | 3) ;;
  *)
    echo "[CT-01/mysql] SESSIONS debe ser 2 o 3, no '$SESSIONS'" >&2
    exit 2
    ;;
esac

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=app_user
  --password=app_pass
  --database="$DBNAME"
  --batch
  --skip-column-names
)

# Tabla propia: la carga de sysbench no la toca, asi que el unico ciclo es el
# del escenario
"${MYSQL[@]}" --execute="
  CREATE TABLE IF NOT EXISTS ql_ct01_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL) ENGINE=InnoDB;
  INSERT IGNORE INTO ql_ct01_cuentas VALUES (1, 100), (2, 100), (3, 100);
" 2>/dev/null

OUTS=()
for i in $(seq 1 "$SESSIONS"); do
  OUTS+=("$(mktemp)")
done
trap 'rm -f "${OUTS[@]}"' EXIT

# $1 primera fila, $2 segunda fila, $3 archivo de salida
session() {
  {
    echo "SELECT CONNECTION_ID();"
    echo "START TRANSACTION;"
    echo "UPDATE ql_ct01_cuentas SET saldo = saldo - 10 WHERE id = $1;"
    sleep "$PAUSE_SECONDS"
    echo "UPDATE ql_ct01_cuentas SET saldo = saldo + 10 WHERE id = $2;"
    sleep 1
    echo "ROLLBACK;"
  } | "${MYSQL[@]}" --unbuffered >"$3" 2>&1 || true
}

# cada una arranca despues de la anterior: cuando pide la fila siguiente, la
# duena ya la tiene
for i in $(seq 1 "$SESSIONS"); do
  session "$i" $(( i % SESSIONS + 1 )) "${OUTS[$(( i - 1 ))]}" &
  if [ "$i" -lt "$SESSIONS" ]; then sleep 0.5; fi
done

sleep 1
echo "[CT-01/mysql] SESSIONS=$SESSIONS DBNAME=$DBNAME"
for i in $(seq 1 "$SESSIONS"); do
  echo "[CT-01/mysql] sesion $i id=$(head -n 1 "${OUTS[$(( i - 1 ))]}"): fila $i y despues fila $(( i % SESSIONS + 1 ))"
done
wait

AT=$(date +%s)
VICTIM=""
for i in $(seq 1 "$SESSIONS"); do
  if grep -q "ERROR 1213" "${OUTS[$(( i - 1 ))]}"; then
    VICTIM=$i
    break
  fi
done
if [ -z "$VICTIM" ]; then
  echo "[CT-01/mysql] no hubo abrazo mortal" >&2
  exit 1
fi
echo "[CT-01/mysql] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $VICTIM"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-01/mysql] hallazgo esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
