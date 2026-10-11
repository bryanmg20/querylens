-- AP-02 escaneo completo evitable | histogramas MySQL (paso opcional: --extra histograms)
--
-- MySQL no crea histogramas solo. Sin histograma ni indice, el optimizador
-- estima `filtered` con constantes (10 % igualdad, 33,33 % rango) sin mirar el
-- literal. Con histograma usa el literal real: el de la consulta (v1) o el de
-- QUERY_SAMPLE_TEXT (v3).
--
-- Solo columnas SIN indice que filtran los casos: en las indexadas (customer_id,
-- kind) el optimizador ya cuenta filas leyendo el indice con el literal.
-- Con pocos valores distintos (status, region) MySQL arma un histograma
-- "singleton" (frecuencia exacta por valor); con muchos (amount, created_at,
-- code), uno "equi-height" de 100 tramos.

ANALYZE TABLE ql_ab_orders UPDATE HISTOGRAM ON status, region, amount, created_at WITH 100 BUCKETS;
ANALYZE TABLE ql_ab_small UPDATE HISTOGRAM ON code WITH 100 BUCKETS;
