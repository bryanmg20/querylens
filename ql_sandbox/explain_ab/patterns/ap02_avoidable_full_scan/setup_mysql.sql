-- AP-02 escaneo completo evitable | preparacion MySQL
-- Mismos datos que setup_postgres.sql (ver ahi la descripcion de cada columna).

DROP TABLE IF EXISTS ql_ab_orders;
DROP TABLE IF EXISTS ql_ab_small;

CREATE TABLE ql_ab_orders (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    status      VARCHAR(12) NOT NULL,
    kind        VARCHAR(12) NOT NULL,
    region      INT NOT NULL,
    amount      INT NOT NULL,
    created_at  DATETIME NOT NULL,
    pad         CHAR(60) NOT NULL DEFAULT 'relleno'
);

INSERT INTO ql_ab_orders (customer_id, status, kind, region, amount, created_at)
WITH d AS (
    SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
    UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
    UNION ALL SELECT 8 UNION ALL SELECT 9
),
nums AS (
    SELECT d1.n + d2.n * 10 + d3.n * 100 + d4.n * 1000 + d5.n * 10000 + d6.n * 100000 + 1 AS g
    FROM d d1, d d2, d d3, d d4, d d5, d d6
)
SELECT
    FLOOR(RAND() * 100000),
    CASE WHEN g % 1000 = 0 THEN 'error' WHEN g % 100 < 3 THEN 'pending' ELSE 'done' END,
    CASE WHEN g % 1000 = 0 THEN 'error' WHEN g % 100 < 3 THEN 'pending' ELSE 'done' END,
    g % 5,
    FLOOR(RAND() * 1000000),
    TIMESTAMP('2026-01-01 00:00:00') + INTERVAL (g * 25) SECOND
FROM nums;

CREATE INDEX ql_ab_orders_customer ON ql_ab_orders (customer_id);
CREATE INDEX ql_ab_orders_kind ON ql_ab_orders (kind);

CREATE TABLE ql_ab_small (
    id   INT AUTO_INCREMENT PRIMARY KEY,
    code INT NOT NULL
);
INSERT INTO ql_ab_small (code)
WITH d AS (
    SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
    UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
    UNION ALL SELECT 8 UNION ALL SELECT 9
)
SELECT d1.n + d2.n * 10 + d3.n * 100 FROM d d1, d d2, d d3;

ANALYZE TABLE ql_ab_orders;
ANALYZE TABLE ql_ab_small;
