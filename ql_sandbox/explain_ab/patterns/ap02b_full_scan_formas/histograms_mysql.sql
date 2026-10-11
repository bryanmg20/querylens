-- AP-02b | histogramas MySQL (paso opcional: --extra histograms)
-- Mismo criterio que ap02_avoidable_full_scan/histograms_mysql.sql: solo las
-- columnas SIN indice que filtran los casos.

ANALYZE TABLE ql_ab_orders UPDATE HISTOGRAM ON status, region, amount, created_at WITH 100 BUCKETS;
ANALYZE TABLE ql_ab_customers UPDATE HISTOGRAM ON segment WITH 100 BUCKETS;
