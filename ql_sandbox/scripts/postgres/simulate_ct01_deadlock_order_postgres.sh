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
# SESSIONS=3 cierra el ciclo con tres sesiones: la i toma la fila i y pide la
# siguiente, y la ultima pide la fila 1.
#
# El log de Postgres no dice que fila espera cada proceso: el pipeline la saca
# de los valores escritos en las sentencias (WHERE id = 1). Con MODE=prepared
# las sentencias van como PREPARE/EXECUTE y el log no los trae, como con un
# driver que manda los parametros aparte: el abrazo no se puede clasificar.
#
# DBNAME corre el escenario en otra base del mismo servidor (tiene que
# existir). QueryLens monitorea ql_demo: un abrazo de otra base no es suyo.
#
# Esperado:
#   MODE=literal (por defecto): hallazgo inconsistent_lock_order_deadlock con
#     el ciclo de SESSIONS transacciones (los pid que se imprimen abajo), la
#     tabla ql_ct01_cuentas y la recomendacion de un orden de acceso unico.
#   MODE=prepared: hallazgo unclassified_deadlock.
# En los dos casos a mas tardar 15 s despues del abrazo (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
#   SESSIONS=3 MODE=prepared docker exec -e SESSIONS -e MODE ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
SESSIONS="${SESSIONS:-2}"
MODE="${MODE:-literal}"
DBNAME="${DBNAME:-ql_demo}"

case "$MODE" in
  literal | prepared) ;;
  *)
    echo "[CT-01/postgres] MODE debe ser literal o prepared, no '$MODE'" >&2
    exit 2
    ;;
esac
case "$SESSIONS" in
  2 | 3) ;;
  *)
    echo "[CT-01/postgres] SESSIONS debe ser 2 o 3, no '$SESSIONS'" >&2
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
  --dbname="$DBNAME"
  --no-psqlrc
  --quiet
  --tuples-only
  --no-align
)

# Tabla propia: la carga de sysbench no la toca, asi que el unico ciclo es el
# del escenario
"${POSTGRES[@]}" --command="
  CREATE TABLE IF NOT EXISTS ql_ct01_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
  INSERT INTO ql_ct01_cuentas VALUES (1, 100), (2, 100), (3, 100) ON CONFLICT (id) DO NOTHING;
" >/dev/null

OUTS=()
for i in $(seq 1 "$SESSIONS"); do
  OUTS+=("$(mktemp)")
done
trap 'rm -f "${OUTS[@]}"' EXIT

# $1 primera fila, $2 segunda fila, $3 archivo de salida
session() {
  {
    echo "SELECT pg_backend_pid();"
    if [ "$MODE" = "prepared" ]; then
      echo "PREPARE mover(int, int) AS UPDATE ql_ct01_cuentas SET saldo = saldo + \$1 WHERE id = \$2;"
    fi
    echo "BEGIN;"
    if [ "$MODE" = "prepared" ]; then
      echo "EXECUTE mover(-10, $1);"
    else
      echo "UPDATE ql_ct01_cuentas SET saldo = saldo - 10 WHERE id = $1;"
    fi
    sleep "$PAUSE_SECONDS"
    if [ "$MODE" = "prepared" ]; then
      echo "EXECUTE mover(10, $2);"
    else
      echo "UPDATE ql_ct01_cuentas SET saldo = saldo + 10 WHERE id = $2;"
    fi
    sleep 1
    # la victima ya esta revertida: este ROLLBACK solo cierra la de las otras
    echo "ROLLBACK;"
  } | "${POSTGRES[@]}" >"$3" 2>&1 || true
}

# cada una arranca despues de la anterior: cuando pide la fila siguiente, la
# duena ya la tiene
for i in $(seq 1 "$SESSIONS"); do
  session "$i" $(( i % SESSIONS + 1 )) "${OUTS[$(( i - 1 ))]}" &
  if [ "$i" -lt "$SESSIONS" ]; then sleep 0.5; fi
done

sleep 1
echo "[CT-01/postgres] MODE=$MODE SESSIONS=$SESSIONS DBNAME=$DBNAME"
for i in $(seq 1 "$SESSIONS"); do
  echo "[CT-01/postgres] sesion $i pid=$(head -n 1 "${OUTS[$(( i - 1 ))]}"): fila $i y despues fila $(( i % SESSIONS + 1 ))"
done
wait

AT=$(date +%s)
VICTIM=""
for i in $(seq 1 "$SESSIONS"); do
  if grep -q "deadlock detected" "${OUTS[$(( i - 1 ))]}"; then
    VICTIM=$i
    break
  fi
done
if [ -z "$VICTIM" ]; then
  echo "[CT-01/postgres] no hubo abrazo mortal: subir PAUSE_SECONDS (> deadlock_timeout)" >&2
  exit 1
fi
EXPECTED=$([ "$MODE" = "prepared" ] && echo unclassified_deadlock || echo inconsistent_lock_order_deadlock)
echo "[CT-01/postgres] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $VICTIM"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-01/postgres] hallazgo $EXPECTED esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
