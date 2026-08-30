
## pg_stat_user_indexes 
https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STATIO-ALL-INDEXES-VIEW

## pg_stat_user_tables 
https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STATIO-ALL-TABLES-VIEW

## pg_stat_statements
https://www.postgresql.org/docs/17/pgstatstatements.html

## pg_locks
https://www.postgresql.org/docs/17/view-pg-locks.html

## pg_stat_activity
https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STAT-ACTIVITY-VIEW




Indice no utilizado o redundante- https://pganalyze.com/docs/checks/schema/index_unused
Indice ausente- https://pganalyze.com/docs/indexing-engine
Escaneo completo evitable- https://www.postgresql.org/docs/17/indexes-examine.html
Degradación de tiempo respecto a línea base- https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/performance-test?utm_source
Predicado no sargable- https://www.sqlservercentral.com/blogs/what-is-a-sargable-predicate?utm_source
Error grave de estimación de cardinalidad- https://wiki.postgresql.org/wiki/Count_estimate?utm_source
Patron N+1 https://vladmihalcea.com/n-plus-1-query-problem/?utm_source
vertido a disco- https://pganalyze.com/docs/explain/insights/disk-sort


deadlocks- https://www.postgresql.org/docs/17/explicit-locking.html

Señal 1 — Impacto total

https://www.solarwinds.com/sql-sentry/use-cases/top-sql
https://tosska.com/how-to-use-80-20-rule-to-tune-a-database-application-i/

Señal 2 — Inestabilidad

https://inspector.azimutt.app/blog/postgresql-pg-stat-statements-guide/
https://stormatics.tech/blogs/exploring-postgres-performance-a-deep-dive-into-pg_stat_statements

Señal 3 — Presión I/O

https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view
https://neon.com/docs/extensions/pg_stat_statements
Referencia: https://www.postgresql.org/docs/current/pgstatstatements.html 
https://neon.com/docs/extensions/pg_stat_statements
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view

Señal 4 — Disk spill

https://pganalyze.com/docs/query-advisor/insights
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view

Señal 5 — Rows ineficientes

https://inspector.azimutt.app/blog/postgresql-pg-stat-statements-guide/
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view

To use simulate_queries.sh:
docker exec -it ql_sysbench bash

apt-get update && apt-get install -y --no-install-recommends postgresql-client

for i in $(seq 1 100); do bash /scripts/script.sh; done



para poder ver selects en mysql
docker exec -it ql_mysql mysql -uroot -pql_root