#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-03 (abrazo mortal por contencion de rango)
#
# Tabla 3 del informe: "verificar que no existe y despues insertar". La tabla
# tiene las filas 10 y 20; dos sesiones bloquean con FOR UPDATE una clave
# inexistente del mismo hueco (15 y 16, entre 10 y 20) y despues la insertan.
# Los bloqueos de hueco son compatibles entre si, asi que las dos los
# obtienen; cada INSERT pide intencion de insercion y espera el hueco de la
# otra. InnoDB detecta el ciclo al instante, revierte una de las dos (la
# victima recibe ERROR 1213) y deja el informe en SHOW ENGINE INNODB STATUS,
# de donde lo lee DeadlocksStage.
#
# GAP=end usa claves despues de la ultima fila (25 y 26): InnoDB bloquea el
# supremum como next-key y el hallazgo dice "el hueco al final del indice".
#
# Requiere REPEATABLE READ (el aislamiento por defecto de InnoDB): en READ
# COMMITTED las busquedas no toman bloqueos de hueco y no hay abrazo.
#
# INNODB STATUS guarda solo el ultimo abrazo: si se corre el escenario dos
# veces dentro del mismo ciclo del pipeline (10 s), el primero llega sin
# detalle y no se clasifica. Dejar al menos un ciclo entre corridas.
#
# Esperado: hallazgo range_contention_deadlock con el ciclo de dos
# transacciones (los id de conexion que se imprimen abajo), la tabla
# ql_demo.ql_ct03_reservas, el indice PRIMARY y el hueco (antes de un registro
# o al final del indice), y la recomendacion de INSERT ... ON DUPLICATE KEY
# UPDATE, a mas tardar 15 s despues del abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/mysql/simulate_deadlock_gap_mysql.sh
#   GAP=end docker exec -e GAP ql_sysbench bash /scripts/mysql/simulate_deadlock_gap_mysql.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
GAP="${GAP:-middle}"

case "$GAP" in
  middle) KEY_1=15; KEY_2=16 ;;
  end)    KEY_1=25; KEY_2=26 ;;
  *) echo "GAP debe ser middle o end" >&2; exit 2 ;;
esac

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
# del escenario. Se recrea para que el hueco sea siempre (10, 20) o (20, fin).
"${MYSQL[@]}" --execute="
  DROP TABLE IF EXISTS ql_ct03_reservas;
  CREATE TABLE ql_ct03_reservas (id INT PRIMARY KEY, sala INT NOT NULL) ENGINE=InnoDB;
  INSERT INTO ql_ct03_reservas VALUES (10, 1), (20, 2);
" 2>/dev/null

OUT_1=$(mktemp)
OUT_2=$(mktemp)
trap 'rm -f "$OUT_1" "$OUT_2"' EXIT

# $1 clave que verifica e inserta, $2 archivo de salida
session() {
  {
    echo "SELECT CONNECTION_ID();"
    echo "START TRANSACTION;"
    # la aplicacion "verifica que no existe": bloquea el hueco donde iria
    echo "SELECT id FROM ql_ct03_reservas WHERE id = $1 FOR UPDATE;"
    sleep "$PAUSE_SECONDS"
    echo "INSERT INTO ql_ct03_reservas VALUES ($1, 3);"
    sleep 1
    echo "ROLLBACK;"
  } | "${MYSQL[@]}" --unbuffered >"$2" 2>&1 || true
}

session "$KEY_1" "$OUT_1" &
# la segunda arranca despues: cuando la sesion 1 inserta, la 2 ya tiene el hueco
sleep 0.5
session "$KEY_2" "$OUT_2" &

sleep 1
echo "[CT-03/mysql] sesion 1 id=$(head -n 1 "$OUT_1"): FOR UPDATE de la clave ${KEY_1} (no existe) y despues INSERT"
echo "[CT-03/mysql] sesion 2 id=$(head -n 1 "$OUT_2"): FOR UPDATE de la clave ${KEY_2} (no existe) y despues INSERT"
wait

AT=$(date +%s)
VICTIM=$(grep -l "ERROR 1213" "$OUT_1" "$OUT_2" 2>/dev/null | head -n 1 || true)
if [ -z "$VICTIM" ]; then
  echo "[CT-03/mysql] no hubo abrazo mortal (el aislamiento es REPEATABLE READ?)" >&2
  exit 1
fi
echo "[CT-03/mysql] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $([ "$VICTIM" = "$OUT_1" ] && echo 1 || echo 2)"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-03/mysql] hallazgo esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
