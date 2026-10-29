#!/bin/sh
# Prepara la base de tests y corre las suites.
#
#   docker compose run --rm tests                    # pipeline + detector
#   docker compose run --rm tests pytest -m unit     # cualquier comando
#
# La base es $QUERYLENS_DB (ql_test), no la del stack: test_e2e_main.py
# inserta y desactiva filas de registered_databases, y en la base de
# desarrollo pausaria las bases registradas de verdad.
set -eu

: "${QUERYLENS_DB:?falta QUERYLENS_DB}"

export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$QUERYLENS_USER" PGPASSWORD="$QUERYLENS_PASSWORD"

if ! psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$QUERYLENS_DB'" | grep -q 1; then
    psql -d postgres -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE \"$QUERYLENS_DB\""
fi

# Lo mismo que el initdb del stack, idempotente: la cola (init.sql sin el
# CREATE ROLE, que es de todo el cluster y ya existe) y los DDL.
psql -d "$QUERYLENS_DB" -v ON_ERROR_STOP=1 -q -o /dev/null <<'SQL'
SET client_min_messages = warning;
CREATE EXTENSION IF NOT EXISTS pgmq;
SELECT pgmq.create('analyze_job')
WHERE NOT EXISTS (SELECT FROM pgmq.meta WHERE queue_name = 'analyze_job');
SQL
for ddl in registered_databases pipeline_health; do
    psql -d "$QUERYLENS_DB" -v ON_ERROR_STOP=1 -q -c 'SET client_min_messages = warning' \
        -f "/repo/querylens_database/$ddl.sql"
done

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Las dos suites tienen config propia (pytest.ini de la raiz y de
# detection_engine): se corren por separado y el codigo de salida refleja
# ambas.
status=0
pytest || status=$?
(cd detection_engine && pytest) || status=$?
exit "$status"
