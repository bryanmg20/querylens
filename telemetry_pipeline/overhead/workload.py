"""Carga observada y query del control positivo.

QUERIES es la misma lista que ql_sandbox/scripts/continuous_load.sh (y los
mismos tipos que battery.sh) contra sbtest1: si cambia una, cambia la otra.
El id de plantilla es el indice en la tupla.
"""

QUERIES = (
    "SELECT a.c, COUNT(*) AS total FROM sbtest1 AS a JOIN sbtest1 AS b ON b.id = a.id WHERE a.k > 1000 GROUP BY a.c HAVING COUNT(*) > 1 ORDER BY total DESC LIMIT 50",
    "SELECT id, k, ROW_NUMBER() OVER (PARTITION BY k ORDER BY c) AS rn FROM sbtest1 ORDER BY rn DESC LIMIT 100",
    "SELECT 'x' AS tipo, SUM(k) AS s FROM sbtest1 UNION ALL SELECT 'y', AVG(id) FROM sbtest1 UNION ALL SELECT 'z', COUNT(*) FROM sbtest1",
    "SELECT SUBSTRING(c, 1, 8) AS pref, COUNT(*) AS cnt FROM sbtest1 WHERE c LIKE '%7%' GROUP BY pref ORDER BY cnt DESC LIMIT 50",
    "SELECT COUNT(*) FROM sbtest1 a WHERE a.id = (SELECT MAX(b.id) FROM sbtest1 b WHERE b.k = a.k)",
    "SELECT a.id, b.id FROM sbtest1 a JOIN sbtest1 b ON b.c = a.c WHERE a.id BETWEEN 2000 AND 4000 ORDER BY a.id, b.id LIMIT 100",
    "SELECT id, CASE WHEN k % 3 = 0 THEN 'A' WHEN k % 3 = 1 THEN 'B' ELSE 'C' END AS bucket FROM sbtest1 ORDER BY bucket, id DESC LIMIT 200",
    "SELECT a.k, COUNT(*) AS cnt FROM sbtest1 a JOIN sbtest1 b ON b.id = a.id JOIN sbtest1 c ON c.id = a.id WHERE a.k < 5000 GROUP BY a.k ORDER BY cnt DESC, a.k LIMIT 100",
    "SELECT DISTINCT k, c FROM sbtest1 WHERE id BETWEEN 1 AND 8000 ORDER BY k LIMIT 500",
    "SELECT a.id, (SELECT COUNT(*) FROM sbtest1 b WHERE b.k = a.k) AS cnt FROM sbtest1 a WHERE a.id > 3000 ORDER BY cnt DESC, a.id LIMIT 100",
)

# Control positivo (pipeline_proc --mode burn): CPU pura en el servidor, que
# compite con la carga como lo haria un pipeline caro. Calibrado en el sandbox
# para ~1,5 s por copia sin carga en cada motor; pipeline_proc lanza varias
# copias a la vez. No toca tablas a proposito: un join pesado sobre sbtest1 en
# MySQL cae en hash join con producto cartesiano y su duracion no es estable.
BURN_QUERIES = {
    "postgres": "SELECT count(*) FROM generate_series(1, 1500000) g WHERE md5(g::text) LIKE 'a%'",
    "mysql": "SELECT BENCHMARK(3500000, SHA2('querylens', 256))",
}
