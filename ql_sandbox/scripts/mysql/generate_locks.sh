#!/usr/bin/env bash

set -euo pipefail

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=app_user
  --password=app_pass
  --database=ql_demo
)

# Asegurar que las tablas de prueba existan y tengan datos iniciales
echo "Preparando datos de prueba..."
"${MYSQL[@]}" --execute="
  CREATE TABLE IF NOT EXISTS sbtest1 (
    id INT AUTO_INCREMENT PRIMARY KEY,
    k INT NOT NULL,
    c VARCHAR(120) NOT NULL,
    PAD VARCHAR(60) NOT NULL
  ) ENGINE=InnoDB;

  INSERT INTO sbtest1 (k, c, PAD) VALUES 
  (101, 'test_val_1', 'pad1'),
  (102, 'test_val_2', 'pad2')
  ON DUPLICATE KEY UPDATE k=VALUES(k);
"

echo "Lanzando hilos concurrentes para provocar contención y locks..."

# Proceso 1: Realiza transacciones largas actualizando filas y manteniendo bloqueos
run_tx_writer() {
  while true; do
    "${MYSQL[@]}" --execute="
      START TRANSACTION;
      UPDATE sbtest1 SET k = k + 1 WHERE id = 1;
      SELECT SLEEP(2);
      UPDATE sbtest1 SET k = k + 1 WHERE id = 2;
      COMMIT;
    " >/dev/null 2>&1 || true
    sleep 0.5
  done
}

# Proceso 2: Intenta actualizar en orden inverso para forzar bloqueos y posibles deadlocks
run_tx_contender() {
  while true; do
    "${MYSQL[@]}" --execute="
      START TRANSACTION;
      UPDATE sbtest1 SET k = k - 1 WHERE id = 2;
      SELECT SLEEP(1);
      UPDATE sbtest1 SET k = k - 1 WHERE id = 1;
      COMMIT;
    " >/dev/null 2>&1 || true
    sleep 0.5
  done
}

# Proceso 3: Consultas de lectura intensiva que compiten con los bloqueos exclusivos
run_reader() {
  while true; do
    "${MYSQL[@]}" --execute="
      SELECT * FROM sbtest1 WHERE id = 1 FOR UPDATE;
    " >/dev/null 2>&1 || true
    sleep 0.2
  done
}

# Limpieza de procesos al salir
trap 'echo "Deteniendo generadores de locks..."; kill 0' EXIT

run_tx_writer &
run_tx_contender &
run_reader &

wait