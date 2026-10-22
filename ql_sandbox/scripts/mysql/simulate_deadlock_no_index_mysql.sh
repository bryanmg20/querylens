#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-04 (ampliacion de bloqueos por ausencia de indice)
#
# Tabla 3 del informe: un UPDATE sin indice utilizable bloquea cada fila que
# recorre, no solo las que modifica. La tabla tiene 200 pedidos y ningun
# indice sobre cliente. La sesion 1 modifica el pedido 150 por clave primaria;
# la sesion 2 hace UPDATE ... WHERE cliente = 7, recorre la tabla desde el
# pedido 1, bloquea los 149 primeros y espera el 150; la sesion 1 hace otro
# UPDATE por cliente, empieza tambien por el pedido 1 y espera a la 2. InnoDB
# detecta el ciclo al instante, revierte una de las dos (la victima recibe
# ERROR 1213) y deja el informe en SHOW ENGINE INNODB STATUS, de donde lo lee
# DeadlocksStage.
#
# Requiere REPEATABLE READ (el aislamiento por defecto de InnoDB): en READ
# COMMITTED InnoDB suelta las filas que no cumplen el WHERE y no hay abrazo.
#
# INNODB STATUS guarda solo el ultimo abrazo: si se corre el escenario dos
# veces dentro del mismo ciclo del pipeline (10 s), el primero llega sin
# detalle y no se clasifica. Dejar al menos un ciclo entre corridas.
#
# Esperado: hallazgo lock_amplification_deadlock con el ciclo de dos
# transacciones (los id de conexion que se imprimen abajo), la tabla
# ql_demo.ql_ct04_pedidos, la sesion 2 con unas 150 filas bloqueadas para 3
# modificadas, y la recomendacion CREATE INDEX ON ql_ct04_pedidos (cliente),
# a mas tardar 15 s despues del abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/mysql/simulate_deadlock_no_index_mysql.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/mysql/simulate_deadlock_no_index_mysql.sh
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
# del escenario. Sin indice sobre cliente: es justamente el caso.
"${MYSQL[@]}" --execute="
  CREATE TABLE IF NOT EXISTS ql_ct04_pedidos (
    id INT PRIMARY KEY,
    cliente INT NOT NULL,
    estado VARCHAR(10) NOT NULL
  ) ENGINE=InnoDB;
  INSERT IGNORE INTO ql_ct04_pedidos
    WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 200)
    SELECT i, i % 50, 'nuevo' FROM n;
" 2>/dev/null

OUT_1=$(mktemp)
OUT_2=$(mktemp)
trap 'rm -f "$OUT_1" "$OUT_2"' EXIT

session_1() {
  {
    echo "SELECT CONNECTION_ID();"
    echo "START TRANSACTION;"
    echo "UPDATE ql_ct04_pedidos SET estado = 'pagado' WHERE id = 150;"
    sleep "$PAUSE_SECONDS"
    # recorre desde el pedido 1, que ya tiene la sesion 2
    echo "UPDATE ql_ct04_pedidos SET estado = 'enviado' WHERE cliente = 3;"
    sleep 1
    echo "ROLLBACK;"
  } | "${MYSQL[@]}" --unbuffered >"$OUT_1" 2>&1 || true
}

session_2() {
  {
    echo "SELECT CONNECTION_ID();"
    echo "START TRANSACTION;"
    # recorre la tabla: bloquea los pedidos 1 a 149 y espera el 150
    echo "UPDATE ql_ct04_pedidos SET estado = 'anulado' WHERE cliente = 7;"
    sleep "$PAUSE_SECONDS"
    echo "ROLLBACK;"
  } | "${MYSQL[@]}" --unbuffered >"$OUT_2" 2>&1 || true
}

session_1 &
# la segunda arranca despues: cuando recorre la tabla, la 1 ya tiene el pedido 150
sleep 0.5
session_2 &

sleep 1
echo "[CT-04/mysql] sesion 1 id=$(head -n 1 "$OUT_1"): UPDATE del pedido 150 y despues UPDATE por cliente"
echo "[CT-04/mysql] sesion 2 id=$(head -n 1 "$OUT_2"): UPDATE por cliente (sin indice: recorre la tabla)"
wait

AT=$(date +%s)
VICTIM=$(grep -l "ERROR 1213" "$OUT_1" "$OUT_2" 2>/dev/null | head -n 1 || true)
if [ -z "$VICTIM" ]; then
  echo "[CT-04/mysql] no hubo abrazo mortal (el aislamiento es REPEATABLE READ? cliente tiene indice?)" >&2
  exit 1
fi
echo "[CT-04/mysql] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $([ "$VICTIM" = "$OUT_1" ] && echo 1 || echo 2)"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-04/mysql] hallazgo esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
