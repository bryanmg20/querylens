-- AP-02b escaneo completo evitable con formas de consulta mas complejas |
-- preparacion PostgreSQL
--
-- ql_ab_orders: identica a la de ap02_avoidable_full_scan (1.000.000 de filas)
--   status      sin indice, sesgado: 'error' 0,1 %, 'pending' ~2,9 %, 'done' ~97 %
--   kind        mismo sesgo que status, CON indice
--   region      sin indice, 5 valores uniformes (20 % cada uno)
--   amount      sin indice, ~1.000.000 de valores (casi unico)
--   customer_id con indice, ~100.000 valores (0..99.999)
--   created_at  sin indice, una fila cada 25 s desde 2026-01-01 (~3.456 por dia,
--               ultima fila ~2026-10-17 08:26)
-- ql_ab_customers: 100.000 filas, id 0..99.999 (cada uno con ~10 ordenes)
--   segment     sin indice, sesgado: 'vip' 0,1 %, 'gold' ~2,9 %, 'std' ~97 %

DROP TABLE IF EXISTS ql_ab_orders;
DROP TABLE IF EXISTS ql_ab_customers;

CREATE TABLE ql_ab_orders (
    id          SERIAL PRIMARY KEY,
    customer_id INT NOT NULL,
    status      VARCHAR(12) NOT NULL,
    kind        VARCHAR(12) NOT NULL,
    region      INT NOT NULL,
    amount      INT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    pad         CHAR(60) NOT NULL DEFAULT 'relleno'
);

INSERT INTO ql_ab_orders (customer_id, status, kind, region, amount, created_at)
SELECT
    floor(random() * 100000)::int,
    CASE WHEN g % 1000 = 0 THEN 'error' WHEN g % 100 < 3 THEN 'pending' ELSE 'done' END,
    CASE WHEN g % 1000 = 0 THEN 'error' WHEN g % 100 < 3 THEN 'pending' ELSE 'done' END,
    g % 5,
    floor(random() * 1000000)::int,
    timestamp '2026-01-01 00:00:00' + g * interval '25 seconds'
FROM generate_series(1, 1000000) g;

CREATE INDEX ql_ab_orders_customer ON ql_ab_orders (customer_id);
CREATE INDEX ql_ab_orders_kind ON ql_ab_orders (kind);

CREATE TABLE ql_ab_customers (
    id      INT PRIMARY KEY,
    segment VARCHAR(12) NOT NULL,
    name    VARCHAR(40) NOT NULL
);

INSERT INTO ql_ab_customers (id, segment, name)
SELECT
    g,
    CASE WHEN g % 1000 = 0 THEN 'vip' WHEN g % 100 < 3 THEN 'gold' ELSE 'std' END,
    'cliente ' || g
FROM generate_series(0, 99999) g;

-- En un Seq Scan paralelo, "Plan Rows" es la estimacion POR worker (ver
-- ap02_avoidable_full_scan/setup_postgres.sql)
ALTER TABLE ql_ab_orders SET (parallel_workers = 0);
ALTER TABLE ql_ab_customers SET (parallel_workers = 0);

ANALYZE ql_ab_orders;
ANALYZE ql_ab_customers;
