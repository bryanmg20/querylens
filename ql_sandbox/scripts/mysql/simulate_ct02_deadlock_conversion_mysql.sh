#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-02 (abrazo mortal por conversion de modo)
#
# Tabla 3 del informe: dos sesiones leen la misma fila con FOR SHARE y despues
# la actualizan. S es compatible con S, asi que las dos entran a leer; cuando
# la sesion 1 pide X para el UPDATE espera el S de la 2, y cuando la 2 lo pide
# se cierra el ciclo. InnoDB lo detecta al instante, revierte una de las dos
# (la victima recibe ERROR 1213) y deja el informe en SHOW ENGINE INNODB
# STATUS, de donde lo lee DeadlocksStage.
#
# INNODB STATUS guarda solo el ultimo abrazo: si se corre el escenario dos
# veces dentro del mismo ciclo del pipeline (10 s), el primero llega sin
# detalle y no se clasifica. Dejar al menos un ciclo entre corridas.
#
# Esperado: hallazgo lock_mode_conversion_deadlock con el ciclo de dos
# transacciones (los id de conexion que se imprimen abajo), la tabla
# ql_demo.ql_ct02_cuentas, el indice PRIMARY, S,REC_NOT_GAP retenido y
# X,REC_NOT_GAP pedido sobre el mismo registro, y la recomendacion de pedir la
# fila con FOR UPDATE, a mas tardar 15 s despues del abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/mysql/simulate_ct02_deadlock_conversion_mysql.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/mysql/simulate_ct02_deadlock_conversion_mysql.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"

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

# Tabla propia: la carga de sysbench no la toca, asi que el unico ciclo es el
# del escenario
"${MYSQL[@]}" --execute="
  CREATE TABLE IF NOT EXISTS ql_ct02_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL) ENGINE=InnoDB;
  INSERT IGNORE INTO ql_ct02_cuentas VALUES (1, 100);
" 2>/dev/null

OUT_1=$(mktemp)
OUT_2=$(mktemp)
trap 'rm -f "$OUT_1" "$OUT_2"' EXIT

# $1 lo que descuenta, $2 archivo de salida
session() {
  {
    echo "SELECT CONNECTION_ID();"
    echo "START TRANSACTION;"
    echo "SELECT saldo FROM ql_ct02_cuentas WHERE id = 1 FOR SHARE;"
    # la aplicacion "verifica que alcanza" mientras las dos tienen el S
    sleep "$PAUSE_SECONDS"
    echo "UPDATE ql_ct02_cuentas SET saldo = saldo - $1 WHERE id = 1;"
    sleep 1
    echo "ROLLBACK;"
  } | "${MYSQL[@]}" --unbuffered >"$2" 2>&1 || true
}

session 1 "$OUT_1" &
# la segunda arranca despues: cuando la sesion 1 pide X, la 2 ya tiene su S
sleep 0.5
session 2 "$OUT_2" &

sleep 1
echo "[CT-02/mysql] sesion 1 id=$(head -n 1 "$OUT_1"): FOR SHARE de la fila 1 y despues UPDATE"
echo "[CT-02/mysql] sesion 2 id=$(head -n 1 "$OUT_2"): FOR SHARE de la fila 1 y despues UPDATE"
wait

AT=$(date +%s)
VICTIM=$(grep -l "ERROR 1213" "$OUT_1" "$OUT_2" 2>/dev/null | head -n 1 || true)
if [ -z "$VICTIM" ]; then
  echo "[CT-02/mysql] no hubo abrazo mortal" >&2
  exit 1
fi
echo "[CT-02/mysql] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $([ "$VICTIM" = "$OUT_1" ] && echo 1 || echo 2)"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-02/mysql] hallazgo esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
