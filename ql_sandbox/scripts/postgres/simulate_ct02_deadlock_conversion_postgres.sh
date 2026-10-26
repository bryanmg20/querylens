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
# El log de Postgres no dice que fila espera cada proceso. El pipeline la saca
# de los valores escritos en las sentencias (WHERE id = 1) o, si no estan,
# del ctid que el log da para cada espera: la victima en su CONTEXT y la otra
# en su aviso "still waiting" (log_lock_waits), que solo escribe si espero
# mas que deadlock_timeout antes de que se cerrara el ciclo.
#
# MODE=bind manda los valores aparte con \bind (protocolo extendido, como
# psycopg 3, JDBC o node-postgres): el log trae $1 y $2. Requiere psql 16+.
#
# GAP: segundos entre el comienzo de las dos sesiones, y por lo tanto entre
# el comienzo de sus esperas.
#
# Esperado: hallazgo lock_mode_conversion_deadlock sobre ql_ct02_cuentas,
# marcado como inferido en limitations, con la recomendacion de pedir la fila
# con FOR UPDATE, si MODE=literal o si GAP supera deadlock_timeout. Si no,
# unclassified_deadlock con las dos causas posibles (CT-01 y CT-02) y como
# averiguar cual fue. En los dos casos a mas tardar 15 s despues del abrazo
# (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/postgres/simulate_ct02_deadlock_conversion_postgres.sh
#   MODE=bind GAP=1.5 docker exec -e MODE -e GAP ql_sysbench bash /scripts/postgres/simulate_ct02_deadlock_conversion_postgres.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/postgres/simulate_ct02_deadlock_conversion_postgres.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
MODE="${MODE:-literal}"
GAP="${GAP:-0.5}"

case "$MODE" in
  literal) ;;
  bind)
    if [ "$(psql --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')" -lt 16 ]; then
      echo "[CT-02/postgres] MODE=bind necesita psql 16 o superior (\\bind)" >&2
      exit 2
    fi
    ;;
  *)
    echo "[CT-02/postgres] MODE debe ser literal o bind, no '$MODE'" >&2
    exit 2
    ;;
esac
# la segunda tiene que tener su S antes de que la primera pida X
PAUSE_SECONDS=$(awk -v p="$PAUSE_SECONDS" -v g="$GAP" 'BEGIN { m = g + 1; print (p > m ? p : m) }')

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
    echo "BEGIN;"
    if [ "$MODE" = "bind" ]; then
      echo "SELECT saldo FROM ql_ct02_cuentas WHERE id = \$1 FOR SHARE \\bind 1 \\g"
    else
      echo "SELECT saldo FROM ql_ct02_cuentas WHERE id = 1 FOR SHARE;"
    fi
    # la aplicacion "verifica que alcanza" mientras las dos tienen el S
    sleep "$PAUSE_SECONDS"
    if [ "$MODE" = "bind" ]; then
      echo "UPDATE ql_ct02_cuentas SET saldo = saldo - \$1 WHERE id = \$2 \\bind $1 1 \\g"
    else
      echo "UPDATE ql_ct02_cuentas SET saldo = saldo - $1 WHERE id = 1;"
    fi
    sleep 1
    # la victima ya esta revertida: este ROLLBACK solo cierra la de la otra
    echo "ROLLBACK;"
  } | "${POSTGRES[@]}" >"$2" 2>&1 || true
}

session 1 "$OUT_1" &
# la segunda arranca GAP s despues: cuando la sesion 1 pide X, la 2 ya tiene su S
sleep "$GAP"
session 2 "$OUT_2" &

sleep 1
echo "[CT-02/postgres] MODE=$MODE GAP=$GAP"
echo "[CT-02/postgres] sesion 1 pid=$(head -n 1 "$OUT_1"): FOR SHARE de la fila 1 y despues UPDATE"
echo "[CT-02/postgres] sesion 2 pid=$(head -n 1 "$OUT_2"): FOR SHARE de la fila 1 y despues UPDATE"
wait

AT=$(date +%s)
VICTIM=$(grep -l "deadlock detected" "$OUT_1" "$OUT_2" 2>/dev/null | head -n 1 || true)
if [ -z "$VICTIM" ]; then
  echo "[CT-02/postgres] no hubo abrazo mortal: subir PAUSE_SECONDS (> GAP, el desfase entre sesiones)" >&2
  exit 1
fi
# sin valores, la fila de la otra sesion sale de su "still waiting" (GAP > deadlock_timeout)
EXPECTED=lock_mode_conversion_deadlock
if [ "$MODE" = "bind" ] && awk -v g="$GAP" 'BEGIN { exit !(g <= 1) }'; then
  EXPECTED=unclassified_deadlock
fi
echo "[CT-02/postgres] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $([ "$VICTIM" = "$OUT_1" ] && echo 1 || echo 2)"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-02/postgres] hallazgo $EXPECTED esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
