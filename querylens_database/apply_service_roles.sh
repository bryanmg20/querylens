#!/bin/sh
# Aplica los roles por servicio (querylens_database/service_roles.sql). Lo
# corre el servicio db-roles de docker-compose en cada "up", despues de que
# postgres este sano y antes de los servicios que usan esos roles: cubre el
# volumen nuevo, uno creado antes de los roles y una contrasena cambiada en el
# .env. La conexion sale de PGHOST/PGUSER/PGPASSWORD/PGDATABASE.
set -eu

: "${PGDATABASE:?falta PGDATABASE}"
: "${QL_AUTH_DB_PASSWORD:?falta QL_AUTH_DB_PASSWORD}"
: "${QL_API_DB_PASSWORD:?falta QL_API_DB_PASSWORD}"
: "${QL_PIPELINE_DB_PASSWORD:?falta QL_PIPELINE_DB_PASSWORD}"
: "${QL_DETECTOR_DB_PASSWORD:?falta QL_DETECTOR_DB_PASSWORD}"
: "${QL_CLI_DB_PASSWORD:?falta QL_CLI_DB_PASSWORD}"

# Las contrasenas viajan como variables de psql (:'auth_pw'), no
# interpoladas en el SQL. -q y client_min_messages: solo errores en el log.
PGOPTIONS="-c client_min_messages=warning" psql -v ON_ERROR_STOP=1 --no-psqlrc -q \
    -v dbname="$PGDATABASE" \
    -v auth_pw="$QL_AUTH_DB_PASSWORD" \
    -v api_pw="$QL_API_DB_PASSWORD" \
    -v pipeline_pw="$QL_PIPELINE_DB_PASSWORD" \
    -v detector_pw="$QL_DETECTOR_DB_PASSWORD" \
    -v cli_pw="$QL_CLI_DB_PASSWORD" \
    -f /querylens_database/service_roles.sql

echo "db-roles | roles por servicio aplicados en $PGDATABASE"
