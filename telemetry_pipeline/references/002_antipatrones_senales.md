# Anti-patrones del catálogo y señales de selección

Referencias para el catálogo de 8 anti-patrones que detecta el proyecto, los bloqueos y las fuentes de evidencia para las señales de priorización de candidatos.

## Catálogo de anti-patrones

| Anti-patrón | Referencia |
|-------------|------------|
| Índice no utilizado o redundante | https://pganalyze.com/docs/checks/schema/index_unused |
| Índice ausente | https://pganalyze.com/docs/indexing-engine |
| Escaneo completo evitable | https://www.postgresql.org/docs/17/indexes-examine.html |
| Degradación de tiempo respecto a línea base | https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/performance-test |
| Predicado no sargable | https://www.sqlservercentral.com/blogs/what-is-a-sargable-predicate |
| Error grave de estimación de cardinalidad | https://wiki.postgresql.org/wiki/Count_estimate |
| Patrón N+1 | https://vladmihalcea.com/n-plus-1-query-problem |
| Vertido a disco | https://pganalyze.com/docs/explain/insights/disk-sort |

## Deadlocks y contención

Bloqueos y abrazos mortales — referencia oficial de bloques en PostgreSQL:

https://www.postgresql.org/docs/17/explicit-locking.html

## Señales de selección de candidatos (evidencia)

Las señales alimentan los selectores de `CandidatesStage` y la justificación (`selected_by`) de cada candidato.

### Señal 1 — Impacto total
https://www.solarwinds.com/sql-sentry/use-cases/top-sql
https://tosska.com/how-to-use-80-20-rule-to-tune-a-database-application-i/

### Señal 2 — Inestabilidad
https://inspector.azimutt.app/blog/postgresql-pg-stat-statements-guide/
https://stormatics.tech/blogs/exploring-postgres-performance-a-deep-dive-into-pg-stat-statements

### Señal 3 — Presión I/O
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view
https://neon.com/docs/extensions/pg_stat_statements
Referencia: https://www.postgresql.org/docs/current/pgstatstatements.html
https://neon.com/docs/extensions/pg_stat_statements
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view

### Señal 4 — Disk spill
https://pganalyze.com/docs/query-advisor/insights
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view

### Señal 5 — Rows ineficientes
https://inspector.azimutt.app/blog/postgresql-pg-stat-statements-guide/
https://oneuptime.com/blog/post/2026-01-21-postgresql-pg-stat-statements/view