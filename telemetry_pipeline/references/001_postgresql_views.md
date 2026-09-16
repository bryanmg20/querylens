# PostgreSQL — vistas de catálogo usadas en la colección

Documentación oficial de las vistas del sistema que alimentan la capa de recolección (`collectors/postgres/queries.py`).

## pg_stat_user_indexes
Estadísticas de uso de índices (escaneos, último scan, tamaño).

https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STATIO-ALL-INDEXES-VIEW

## pg_stat_user_tables
Estadísticas de acceso a tablas (escaneos secuenciales, escaneos por índice, filas vivas).

https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STATIO-ALL-TABLES-VIEW

## pg_stat_statements
Estadísticas agregadas por sentencia: tiempos, conteos, `rows`, `temp_blks_written`. Es la principal fuente de la sección `statements` del snapshot.

- https://www.postgresql.org/docs/17/pgstatstatements.html
- https://www.postgresql.org/docs/current/pgstatstatements.html

## pg_locks
Bloqueos vivos al momento del muestreo: modo, estado `GRANTED`/`WAITING`, `blocking_pids`.

https://www.postgresql.org/docs/17/view-pg-locks.html

## pg_stat_activity
Sesiones activas con su `query_id` y `transaction_start_time`. Alimenta `active_queries` y la dependencia de `select_explain_ready`.

https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STAT-ACTIVITY-VIEW