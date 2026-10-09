#!/usr/bin/env bash
# Crea tablas con claves foraneas para que la seccion foreign_keys del
# snapshot traiga filas (sysbench no define ninguna FK).
#
#   docker exec ql_sysbench bash /scripts/mysql/create_foreign_keys_mysql.sh
#   docker exec ql_sysbench bash /scripts/mysql/create_foreign_keys_mysql.sh drop
#
# Relaciones que quedan:
#   ql_fk_orders.customer_id                 -> ql_fk_customers.id          (simple)
#   ql_fk_stock(warehouse_region, warehouse_code)
#                                            -> ql_fk_warehouses(region, code) (compuesta: 2 filas)

set -euo pipefail

export MYSQL_PWD=app_pass

MYSQL=(
  mysql
  --host=mysql
  --port=3306
  --user=app_user
  --database=ql_demo
  -e
)

DROP_SQL="DROP TABLE IF EXISTS ql_fk_stock, ql_fk_warehouses, ql_fk_orders, ql_fk_customers;"

if [[ "${1:-}" == "drop" ]]; then
  "${MYSQL[@]}" "${DROP_SQL}"
  printf 'Tablas ql_fk_* eliminadas en MySQL.\n'
  exit 0
fi

"${MYSQL[@]}" "
  ${DROP_SQL}

  CREATE TABLE ql_fk_customers (
      id   INT PRIMARY KEY,
      name VARCHAR(64) NOT NULL
  ) ENGINE=InnoDB;

  CREATE TABLE ql_fk_orders (
      id          INT PRIMARY KEY,
      customer_id INT NOT NULL,
      total       DECIMAL(10, 2) NOT NULL,
      CONSTRAINT ql_fk_orders_customer_id_fkey
          FOREIGN KEY (customer_id) REFERENCES ql_fk_customers (id)
  ) ENGINE=InnoDB;

  CREATE TABLE ql_fk_warehouses (
      region VARCHAR(16) NOT NULL,
      code   INT NOT NULL,
      PRIMARY KEY (region, code)
  ) ENGINE=InnoDB;

  CREATE TABLE ql_fk_stock (
      id               INT PRIMARY KEY,
      warehouse_region VARCHAR(16) NOT NULL,
      warehouse_code   INT NOT NULL,
      qty              INT NOT NULL,
      CONSTRAINT ql_fk_stock_warehouse_fkey
          FOREIGN KEY (warehouse_region, warehouse_code)
          REFERENCES ql_fk_warehouses (region, code)
  ) ENGINE=InnoDB;

  INSERT INTO ql_fk_customers
  WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 50)
  SELECT n, CONCAT('customer ', n) FROM g;

  SET SESSION cte_max_recursion_depth = 1000;
  INSERT INTO ql_fk_orders
  WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 500)
  SELECT n, 1 + n % 50, ROUND(RAND() * 500, 2) FROM g;

  INSERT INTO ql_fk_warehouses VALUES ('north', 1), ('north', 2), ('south', 1);

  INSERT INTO ql_fk_stock
  WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 30)
  SELECT n, IF(n % 3 = 0, 'south', 'north'), IF(n % 3 = 1, 2, 1), n FROM g;

  SELECT CONSTRAINT_NAME AS constraint_name,
         TABLE_NAME AS table_name,
         REFERENCED_TABLE_NAME AS referenced_table_name,
         COUNT(*) AS columns
  FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = 'ql_demo'
    AND REFERENCED_TABLE_NAME IS NOT NULL
    AND TABLE_NAME LIKE 'ql\_fk\_%'
  GROUP BY CONSTRAINT_NAME, TABLE_NAME, REFERENCED_TABLE_NAME
  ORDER BY 1;
"

printf 'Claves foraneas creadas en MySQL (ql_demo, como app_user).\n'
