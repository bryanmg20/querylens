-- AP-02 escaneo completo evitable | preparacion PostgreSQL
--
-- ql_ab_orders: 1.000.000 de filas
--   status      sin indice, sesgado: 'error' 0,1 %, 'pending' ~2,9 %, 'done' ~97 %
--   kind        mismo sesgo que status, CON indice
--   region      sin indice, 5 valores uniformes (20 % cada uno)
--   amount      sin indice, ~1.000.000 de valores (casi unico)
--   customer_id con indice, ~100.000 valores
--   created_at  sin indice, una fila cada 25 s desde 2026-01-01 (~3.456 por dia,
--               ultima fila ~2026-10-17 08:26)
-- ql_ab_small: 1.000 filas, por debajo de DEFAULT_MIN_LIVE_ROWS (10.000)

DROP TABLE IF EXISTS ql_ab_orders;
DROP TABLE IF EXISTS ql_ab_small;

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

-- En un Seq Scan paralelo, "Plan Rows" es la estimacion POR worker: la
-- selectividad calculada por AP-02 bajaria en ambas versiones por igual y
-- ensuciaria la comparacion. Se desactiva solo para esta tabla.
ALTER TABLE ql_ab_orders SET (parallel_workers = 0);

CREATE TABLE ql_ab_small (
    id   SERIAL PRIMARY KEY,
    code INT NOT NULL
);
INSERT INTO ql_ab_small (code) SELECT g % 1000 FROM generate_series(1, 1000) g;

ANALYZE ql_ab_orders;
ANALYZE ql_ab_small;
