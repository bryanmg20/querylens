#!/usr/bin/env bash
# ============================================================
# QUERYLENS — escenario CT-02 (abrazo mortal por conversion de modo)
#
# Tabla 3 del informe: dos sesiones leen la misma fila con FOR SHARE y despues
# la actualizan. S es compatible con S, asi que las dos entran a leer; cuando
# la sesion 1 pide X para el UPDATE espera el S de la 2, y cuando la 2 lo pide
# espera el S de la 1. Tras deadlock_timeout (1 s en el sandbox) Postgres
# detecta el ciclo, revierte una de las dos (la victima recibe "deadlock
# detected") y escribe el informe en el log del servidor, de donde lo lee
# DeadlocksStage.
#
# El log de Postgres no dice que fila espera cada proceso: el pipeline la saca
# de los valores escritos en las sentencias (WHERE id = 1). Con MODE=prepared
# las sentencias van como PREPARE/EXECUTE y el log no los trae, como con un
# driver que manda los parametros aparte: el abrazo no se puede clasificar.
#
# Esperado:
#   MODE=literal (por defecto): hallazgo lock_mode_conversion_deadlock sobre
#     ql_ct02_cuentas, marcado como inferido en limitations, con la
#     recomendacion de pedir la fila con FOR UPDATE.
#   MODE=prepared: hallazgo unclassified_deadlock con las dos causas posibles
#     (CT-01 y CT-02) y como averiguar cual fue.
# En los dos casos a mas tardar 15 s despues del abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/postgres/simulate_ct02_deadlock_conversion_postgres.sh
#   MODE=prepared docker exec -e MODE ql_sysbench bash /scripts/postgres/simulate_ct02_deadlock_conversion_postgres.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/postgres/simulate_ct02_deadlock_conversion_postgres.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
MODE="${MODE:-literal}"

case "$MODE" in
  literal | prepared) ;;
  *)
    echo "[CT-02/postgres] MODE debe ser literal o prepared, no '$MODE'" >&2
    exit 2
    ;;
esac

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
# del escenario. La clave primaria es la que le dice al pipeline que id = 1
# es una sola fila.
"${POSTGRES[@]}" --command="
  CREATE TABLE IF NOT EXISTS ql_ct02_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
  INSERT INTO ql_ct02_cuentas VALUES (1, 100) ON CONFLICT (id) DO NOTHING;
" >/dev/null

OUT_1=$(mktemp)
OUT_2=$(mktemp)
trap 'rm -f "$OUT_1" "$OUT_2"' EXIT

# $1 lo que descuenta, $2 archivo de salida
session() {
  {
    echo "SELECT pg_backend_pid();"
    if [ "$MODE" = "prepared" ]; then
      echo "PREPARE leer(int) AS SELECT saldo FROM ql_ct02_cuentas WHERE id = \$1 FOR SHARE;"
      echo "PREPARE descontar(int, int) AS UPDATE ql_ct02_cuentas SET saldo = saldo - \$1 WHERE id = \$2;"
    fi
    echo "BEGIN;"
    if [ "$MODE" = "prepared" ]; then
      echo "EXECUTE leer(1);"
    else
      echo "SELECT saldo FROM ql_ct02_cuentas WHERE id = 1 FOR SHARE;"
    fi
    # la aplicacion "verifica que alcanza" mientras las dos tienen el S
    sleep "$PAUSE_SECONDS"
    if [ "$MODE" = "prepared" ]; then
      echo "EXECUTE descontar($1, 1);"
    else
      echo "UPDATE ql_ct02_cuentas SET saldo = saldo - $1 WHERE id = 1;"
    fi
    sleep 1
    # la victima ya esta revertida: este ROLLBACK solo cierra la de la otra
    echo "ROLLBACK;"
  } | "${POSTGRES[@]}" >"$2" 2>&1 || true
}

session 1 "$OUT_1" &
# la segunda arranca despues: cuando la sesion 1 pide X, la 2 ya tiene su S
sleep 0.5
session 2 "$OUT_2" &

sleep 1
echo "[CT-02/postgres] MODE=$MODE"
echo "[CT-02/postgres] sesion 1 pid=$(head -n 1 "$OUT_1"): FOR SHARE de la fila 1 y despues UPDATE"
echo "[CT-02/postgres] sesion 2 pid=$(head -n 1 "$OUT_2"): FOR SHARE de la fila 1 y despues UPDATE"
wait

AT=$(date +%s)
VICTIM=$(grep -l "deadlock detected" "$OUT_1" "$OUT_2" 2>/dev/null | head -n 1 || true)
if [ -z "$VICTIM" ]; then
  echo "[CT-02/postgres] no hubo abrazo mortal: subir PAUSE_SECONDS (> 0.5 s, el desfase entre sesiones)" >&2
  exit 1
fi
EXPECTED=$([ "$MODE" = "prepared" ] && echo unclassified_deadlock || echo lock_mode_conversion_deadlock)
echo "[CT-02/postgres] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $([ "$VICTIM" = "$OUT_1" ] && echo 1 || echo 2)"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-02/postgres] hallazgo $EXPECTED esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
