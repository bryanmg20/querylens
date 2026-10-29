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
# El log de Postgres no dice que fila espera cada proceso. El pipeline la saca
# de los valores escritos en las sentencias (WHERE id = 1) o, si no estan,
# del ctid que el log da para cada espera: la victima en su CONTEXT y las
# demas en su aviso "still waiting" (log_lock_waits), que solo escriben si
# esperaron mas que deadlock_timeout antes de que se cerrara el ciclo.
#
# MODE=bind manda los valores aparte con \bind (protocolo extendido, como
# psycopg 3, JDBC o node-postgres): el log trae $1 y $2. Requiere psql 16+.
#
# GAP: segundos entre el comienzo de cada sesion, y por lo tanto entre el
# comienzo de sus esperas. Con GAP mayor que deadlock_timeout las sesiones
# que no son victima escriben su "still waiting".
#
# VARIANT cambia que filas se cruzan:
#   cuentas (por defecto): ql_ct01_cuentas por su clave primaria.
#   sin_unica: por una columna sin indice unico; los valores no alcanzan.
#   dos_tablas: la fila id=1 de ql_ct01_a y la de ql_ct01_b (mismo ctid).
#   dos_schemas: ql_qa.ql_ct01_cuentas y ql_qb.ql_ct01_cuentas, mismo nombre
#     y mismo ctid; el schema sale de cada sentencia.
#   particionada: filas en particiones distintas (mismo ctid).
# dos_tablas y dos_schemas son de dos sesiones.
#
# DBNAME corre el escenario en otra base del mismo servidor (tiene que
# existir). QueryLens monitorea ql_demo: un abrazo de otra base no es suyo.
#
# Esperado: hallazgo inconsistent_lock_order_deadlock con el ciclo de
# SESSIONS transacciones (los pid que se imprimen abajo) y la recomendacion
# de un orden de acceso unico, si los valores identifican la fila (MODE=literal
# y una clave unica) o si GAP supera deadlock_timeout. Si no,
# unclassified_deadlock. En los dos casos a mas tardar 15 s despues del abrazo
# (metrica M4).
#
# USO
#   docker exec ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
#   PAUSE_SECONDS=3 docker exec -e PAUSE_SECONDS ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
#   SESSIONS=3 MODE=bind GAP=1.5 docker exec -e SESSIONS -e MODE -e GAP ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
#   VARIANT=dos_schemas MODE=bind GAP=1.5 docker exec -e VARIANT -e MODE -e GAP ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh
# ============================================================

set -euo pipefail

PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
SESSIONS="${SESSIONS:-2}"
MODE="${MODE:-literal}"
GAP="${GAP:-0.5}"
VARIANT="${VARIANT:-cuentas}"
DBNAME="${DBNAME:-ql_demo}"

case "$MODE" in
  literal) ;;
  bind)
    if [ "$(psql --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')" -lt 16 ]; then
      echo "[CT-01/postgres] MODE=bind necesita psql 16 o superior (\\bind)" >&2
      exit 2
    fi
    ;;
  *)
    echo "[CT-01/postgres] MODE debe ser literal o bind, no '$MODE'" >&2
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
  --host="${PG_HOST:-postgres}"
  --port=5432
  --username=ql_user
  --dbname="$DBNAME"
  --no-psqlrc
  --quiet
  --tuples-only
  --no-align
)

# Tablas propias: la carga de sysbench no las toca, asi que el unico ciclo es
# el del escenario. TABLES[i] y KEYS[i] son la fila que toma la sesion i+1;
# despues pide la de la siguiente.
case "$VARIANT" in
  cuentas)
    SETUP="
      CREATE TABLE IF NOT EXISTS ql_ct01_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
      INSERT INTO ql_ct01_cuentas VALUES (1, 100), (2, 100), (3, 100) ON CONFLICT (id) DO NOTHING;"
    TABLES=(ql_ct01_cuentas ql_ct01_cuentas ql_ct01_cuentas)
    COLUMN=id
    KEYS=(1 2 3)
    ;;
  sin_unica)
    SETUP="
      CREATE TABLE IF NOT EXISTS ql_ct01_sin_unica (codigo TEXT NOT NULL, saldo INT NOT NULL);
      INSERT INTO ql_ct01_sin_unica SELECT v, 100 FROM unnest(ARRAY['c1', 'c2', 'c3']) AS v
      WHERE NOT EXISTS (SELECT FROM ql_ct01_sin_unica);"
    TABLES=(ql_ct01_sin_unica ql_ct01_sin_unica ql_ct01_sin_unica)
    COLUMN=codigo
    KEYS=(c1 c2 c3)
    ;;
  dos_tablas)
    SETUP="
      CREATE TABLE IF NOT EXISTS ql_ct01_a (id INT PRIMARY KEY, saldo INT NOT NULL);
      CREATE TABLE IF NOT EXISTS ql_ct01_b (id INT PRIMARY KEY, saldo INT NOT NULL);
      INSERT INTO ql_ct01_a VALUES (1, 100) ON CONFLICT (id) DO NOTHING;
      INSERT INTO ql_ct01_b VALUES (1, 100) ON CONFLICT (id) DO NOTHING;"
    TABLES=(ql_ct01_a ql_ct01_b)
    COLUMN=id
    KEYS=(1 1)
    ;;
  dos_schemas)
    SETUP="
      CREATE SCHEMA IF NOT EXISTS ql_qa;
      CREATE SCHEMA IF NOT EXISTS ql_qb;
      CREATE TABLE IF NOT EXISTS ql_qa.ql_ct01_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
      CREATE TABLE IF NOT EXISTS ql_qb.ql_ct01_cuentas (id INT PRIMARY KEY, saldo INT NOT NULL);
      INSERT INTO ql_qa.ql_ct01_cuentas VALUES (1, 100) ON CONFLICT (id) DO NOTHING;
      INSERT INTO ql_qb.ql_ct01_cuentas VALUES (1, 100) ON CONFLICT (id) DO NOTHING;"
    TABLES=(ql_qa.ql_ct01_cuentas ql_qb.ql_ct01_cuentas)
    COLUMN=id
    KEYS=(1 1)
    ;;
  particionada)
    SETUP="
      CREATE TABLE IF NOT EXISTS ql_ct01_part (id INT PRIMARY KEY, saldo INT NOT NULL) PARTITION BY RANGE (id);
      CREATE TABLE IF NOT EXISTS ql_ct01_part_1 PARTITION OF ql_ct01_part FOR VALUES FROM (1) TO (2);
      CREATE TABLE IF NOT EXISTS ql_ct01_part_2 PARTITION OF ql_ct01_part FOR VALUES FROM (2) TO (3);
      CREATE TABLE IF NOT EXISTS ql_ct01_part_3 PARTITION OF ql_ct01_part FOR VALUES FROM (3) TO (4);
      INSERT INTO ql_ct01_part VALUES (1, 100), (2, 100), (3, 100) ON CONFLICT (id) DO NOTHING;"
    TABLES=(ql_ct01_part ql_ct01_part ql_ct01_part)
    COLUMN=id
    KEYS=(1 2 3)
    ;;
  *)
    echo "[CT-01/postgres] VARIANT no reconocida: '$VARIANT'" >&2
    exit 2
    ;;
esac
if [ "${#TABLES[@]}" -lt "$SESSIONS" ]; then
  echo "[CT-01/postgres] VARIANT=$VARIANT es de ${#TABLES[@]} sesiones" >&2
  exit 2
fi
"${POSTGRES[@]}" --command="$SETUP" >/dev/null

# La ultima sesion tiene que tomar su fila antes de que la primera la pida
PAUSE_SECONDS=$(awk -v p="$PAUSE_SECONDS" -v g="$GAP" -v n="$SESSIONS" 'BEGIN { m = g * (n - 1) + 1; print (p > m ? p : m) }')

OUTS=()
for i in $(seq 1 "$SESSIONS"); do
  OUTS+=("$(mktemp)")
done
trap 'rm -f "${OUTS[@]}"' EXIT

# UPDATE de la fila $2 (indice en TABLES/KEYS) sumando $1
update() {
  local table=${TABLES[$2]} key=${KEYS[$2]}
  if [ "$MODE" = "bind" ]; then
    echo "UPDATE $table SET saldo = saldo + \$1 WHERE $COLUMN = \$2 \\bind $1 $key \\g"
  elif [ "$COLUMN" = "id" ]; then
    echo "UPDATE $table SET saldo = saldo + $1 WHERE $COLUMN = $key;"
  else
    echo "UPDATE $table SET saldo = saldo + $1 WHERE $COLUMN = '$key';"
  fi
}

# $1 primera fila, $2 segunda fila (indices), $3 archivo de salida
session() {
  {
    echo "SELECT pg_backend_pid();"
    echo "BEGIN;"
    update -10 "$1"
    sleep "$PAUSE_SECONDS"
    update 10 "$2"
    sleep 1
    # la victima ya esta revertida: este ROLLBACK solo cierra la de las otras
    echo "ROLLBACK;"
  } | "${POSTGRES[@]}" >"$3" 2>&1 || true
}

# cada una arranca GAP s despues de la anterior: cuando pide la fila
# siguiente, la duena ya la tiene
for i in $(seq 1 "$SESSIONS"); do
  session $(( i - 1 )) $(( i % SESSIONS )) "${OUTS[$(( i - 1 ))]}" &
  if [ "$i" -lt "$SESSIONS" ]; then sleep "$GAP"; fi
done

sleep 1
echo "[CT-01/postgres] MODE=$MODE SESSIONS=$SESSIONS GAP=$GAP VARIANT=$VARIANT DBNAME=$DBNAME"
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
echo "[CT-01/postgres] escenario terminado a las $(date -u -d "@${AT}" '+%H:%M:%S') UTC; victima: sesion $VICTIM"
# el abrazo fue un poco antes: el hallazgo trae detection_delay_s medido contra la hora del motor
echo "[CT-01/postgres] hallazgo esperado antes de las $(date -u -d "@$(( AT + 15 ))" '+%H:%M:%S') UTC (M4: detection_delay_s <= 15)"
