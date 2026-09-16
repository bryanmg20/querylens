# Decisiones de contrato (snapshot)

Decisiones de diseño del contrato de datos que emite el pipeline, validadas contra capturas reales de ambos motores.

## query_id: statements ↔ pg_stat_activity

- `select_explain_ready` depende de que el `query_id` de una query activa coincida con el de `pg_stat_statements` (o `performance_schema.events_statements_summary_by_digest` en MySQL). Así, un candidato obtiene plan real solo si su query está ejecutándose en el momento del snapshot.
- Verificado en capturas reales (golden): MySQL digest hex → string; Postgres `queryid` → bigint (p. ej. `-1859038224550094023`). Ambos aparecen en `statements` y en `active_queries` al mismo tiempo.
- Modelo: `QueryId = Union[str, int, None]`.

## disk_spill_indicator: divergencia de semántica entre motores

| Motor | Fuente | Semántica |
|-------|--------|-----------|
| Postgres | `temp_blks_written` (pg_stat_statements) | Bloques escritos en disco temporal: incluye spills de sort/hash/materialize |
| MySQL | `SUM_CREATED_TMP_DISK_TABLES` (events_statements_summary_by_digest) | Solo tablas temporales creadas en disco; los spills de sort (filesort) NO se reflejan aquí |

Pendiente de decisión: en MySQL los `filesort` a disco no tienen indicador sumarizable directo en el resumen por digest. Se documenta como limitación.

## avg_rows_per_call

- Unificado a float (6 decimales) en ambos motores. MySQL solía redondear a entero (`ROUND(...,0)`), inconsistente con Postgres (`rows/calls`).

## source

- Se eliminó `stats["source"]` (cambios de motor rompían la unicidad del evento PGMQ). El dialecto vive en el collector (`source_dialect`).