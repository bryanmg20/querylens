#!/usr/bin/env bash
# Crea tablas con claves foraneas para que la seccion foreign_keys del
# snapshot traiga filas (sysbench no define ninguna FK).
#
#   docker exec ql_sysbench bash /scripts/postgres/create_foreign_keys_postgres.sh
#   docker exec ql_sysbench bash /scripts/postgres/create_foreign_keys_postgres.sh drop
#
# Relaciones que quedan:
#   ql_fk_orders.customer_id                 -> ql_fk_customers.id          (simple)
#   ql_fk_stock(warehouse_region, warehouse_code)
#                                            -> ql_fk_warehouses(region, code) (compuesta: 2 filas)

set -euo pipefail

export PGPASSWORD=ql_pass
# Sin los NOTICE de DROP TABLE IF EXISTS en la primera corrida.
export PGOPTIONS="-c client_min_messages=warning"

POSTGRES=(
  psql
  --host=postgres
  --port=5432
  --username=ql_user
  --dbname=ql_demo
  --no-psqlrc
  --quiet
  --set=ON_ERROR_STOP=1
  --command
)

DROP_SQL="DROP TABLE IF EXISTS ql_fk_stock, ql_fk_warehouses, ql_fk_orders, ql_fk_customers;"

if [[ "${1:-}" == "drop" ]]; then
  "${POSTGRES[@]}" "${DROP_SQL}"
  printf 'Tablas ql_fk_* eliminadas en PostgreSQL.\n'
  exit 0
fi

"${POSTGRES[@]}" "
  ${DROP_SQL}

  CREATE TABLE ql_fk_customers (
      id   int PRIMARY KEY,
      name text NOT NULL
  );

  CREATE TABLE ql_fk_orders (
      id          int PRIMARY KEY,
      customer_id int NOT NULL REFERENCES ql_fk_customers (id),
      total       numeric(10, 2) NOT NULL
  );

  CREATE TABLE ql_fk_warehouses (
      region text NOT NULL,
      code   int  NOT NULL,
      PRIMARY KEY (region, code)
  );

  CREATE TABLE ql_fk_stock (
      id               int PRIMARY KEY,
      warehouse_region text NOT NULL,
      warehouse_code   int  NOT NULL,
      qty              int  NOT NULL,
      CONSTRAINT ql_fk_stock_warehouse_fkey
          FOREIGN KEY (warehouse_region, warehouse_code)
          REFERENCES ql_fk_warehouses (region, code)
  );

  INSERT INTO ql_fk_customers
  SELECT g, 'customer ' || g FROM generate_series(1, 50) g;

  INSERT INTO ql_fk_orders
  SELECT g, 1 + g % 50, round((random() * 500)::numeric, 2) FROM generate_series(1, 500) g;

  INSERT INTO ql_fk_warehouses VALUES ('north', 1), ('north', 2), ('south', 1);

  INSERT INTO ql_fk_stock
  SELECT g, CASE WHEN g % 3 = 0 THEN 'south' ELSE 'north' END, CASE WHEN g % 3 = 1 THEN 2 ELSE 1 END, g
  FROM generate_series(1, 30) g;
"

"${POSTGRES[@]}" "
  SELECT con.conname AS constraint_name,
         c.relname  AS table_name,
         rc.relname AS referenced_table_name
  FROM pg_constraint con
  JOIN pg_class c  ON c.oid  = con.conrelid
  JOIN pg_class rc ON rc.oid = con.confrelid
  WHERE con.contype = 'f' AND c.relname LIKE 'ql_fk_%'
  ORDER BY 1;
"

printf 'Claves foraneas creadas en PostgreSQL (ql_demo, como ql_user).\n'
