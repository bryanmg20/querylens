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

- Unificado a float (6 decimales) con guard `NULLIF` en ambos motores:
  - Postgres: `ROUND(rows::numeric / NULLIF(calls, 0), 6)` (evita división entera de bigint y div-by-zero con `calls = 0`)
  - MySQL: `CAST(ROUND(s.SUM_ROWS_SENT / NULLIF(s.COUNT_STAR, 0), 6) AS DOUBLE)`
- MySQL solía redondear a entero (`ROUND(...,0)`) y Postgres truncaba por división bigint; ambos eran inconsistentes entre sí.
- Fijado por tests de regresión en `tests/test_queries_contract.py`.

## source

- Se eliminó `stats["source"]` (cambios de motor rompían la unicidad del evento PGMQ). El dialecto vive en el collector (`source_dialect`).

## schema_name y userid en statements

- `StatementRow` expone `schema_name` (además de los campos previos). `userid` vive solo en el flujo interno de recolección: se usa para el cruce y se elimina de cada statement/candidato al resolver, no se emite en el snapshot.
- `schema_name` es **escalar por statement** y se resuelve **sin parsear el texto**: se cruza el `userid` de cada statement con `SCHEMA_RESOLVER_QUERY`, que devuelve por rol el primer schema **real** de su `search_path` (fallback real de Postgres para roles sin `rolconfig`: `"$user", public`). Todas las statements del mismo rol heredan ese schema.
- MySQL: `schema_name` ya viene del digest (`events_statements_summary_by_digest.SCHEMA_NAME`), que es el schema activo del momento de ejecución; sin cruce extra.
- `schema_resolver` (resultado de `SCHEMA_RESOLVER_QUERY`) es **transitorio**: lo consume la fase de normalización y se elimina de `stats` antes de emitir el snapshot.
- Limitación (aceptada a propósito): sin parseo, una statement no se distingue por tabla; si un rol tiene varias schemas reales en su `search_path`, `schema_name` toma la primera en orden de prioridad. La resolución por tabla (exacta, con `to_regclass`) quedó descartada por requerir parsear el texto de la query.
- Verificado en capturas reales (golden `postgres_snapshot.json`): 61 statements, todas de `userid=10`, con `schema_name: "public"` (search_path de `ql_user`); `top_impact_queries` y `non_explainable_candidates` heredan `schema_name` vía copia del statement + cruce, y `userid` se descarta igual que en statements.

## EXPLAIN y search_path

- El EXPLAIN se ejecuta sobre el `query_text` del digest (sin calificar). La resolución de tablas queda determinada por el `search_path` de la conexión del pipeline, no por el del rol dueño de la query.
- Verificado en vivo: con `search_path` que excluye la schema, `EXPLAIN SELECT * FROM sbtest1` falla con `relation does not exist`; con la schema incluida, genera plan.
- `ExplainStage` ahora emite la sentencia de contexto antes de cada `EXPLAIN`: en Postgres `SET LOCAL search_path TO <schema_name>` (identificador citado con `identifier_preparer`; si el candidato no tiene `schema_name`, `SET LOCAL search_path TO DEFAULT`); en MySQL `USE <schema_name>` (la conexión no trae database por defecto y el digest no califica). Al ser `LOCAL`/`USE` por candidato y secuenciales, no afectan al resto del pipeline.
- Limitación: para un rol con varias schemas reales, Postgres explora bajo la primera del `search_path`; y en MySQL no hay "reset a sin database" — un candidato sin `schema_name` hereda el `USE` del anterior.