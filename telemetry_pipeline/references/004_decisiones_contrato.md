# Decisiones de contrato (snapshot)

Decisiones de diseño del contrato de datos que emite el pipeline, validadas contra capturas reales de ambos motores.

## query_id: identidad del statement

- `query_id` identifica el statement dentro de su motor, no una ejecución en curso. Antes (commit `c730da1`) la explicación cruzaba `statements.query_id` contra `active_queries.query_id`, de modo que un candidato solo se explicaba si su query se estaba ejecutando en el momento del snapshot. Ese cruce ya **no aplica**: desde `93beb5e` el plan lo produce el propio motor sin necesitar la query en vivo.
- Cada motor resuelve el plan por su vía nativa, y el hook `mark_explainable` decide:
  - Postgres: `EXPLAIN (GENERIC_PLAN)` (PG16+) sobre `pg_stat_statements.query`, que ya viene con placeholders `$1`. Marca **todos** los candidatos como listos.
  - MySQL: `DIGEST_TEXT` trae `?`, que da error de sintaxis en `EXPLAIN`; se usa `QUERY_SAMPLE_TEXT`, que trae literales reales. Marca solo lo que tenga `query_sample_text` no vacío.
- Verificado en capturas reales (golden): MySQL digest hex → string; Postgres `queryid` → bigint (p. ej. `-1859038224550094023`). Ambos aparecen en `statements` y en `active_queries` al mismo tiempo, pero esa coincidencia ya no condiciona nada.
- Modelo: `QueryId = Union[str, int, None]`.
- Lo que queda de `select_explain_ready` es solo inicializar `ready_for_explain=False` y deduplicar por `query_id`; la decisión real la sobrescribe `mark_explainable` justo después, en el mismo `CandidatesStage`.

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
- `schema_resolver` (resultado de `SCHEMA_RESOLVER_QUERY`) es **transitorio**: lo consume `CandidatesStage` (al final, **antes** del EXPLAIN, para que el context SQL del plan tenga el schema resuelto) y se elimina de `stats` antes de emitir el snapshot.
- Limitación (aceptada a propósito): sin parseo, una statement no se distingue por tabla; si un rol tiene varias schemas reales en su `search_path`, `schema_name` toma la primera en orden de prioridad. La resolución por tabla (exacta, con `to_regclass`) quedó descartada por requerir parsear el texto de la query.
- Verificado en capturas reales (golden `postgres_snapshot.json`): 61 statements, todas de `userid=10`, con `schema_name: "public"` (search_path de `ql_user`); `top_impact_queries` y `non_explainable_candidates` heredan `schema_name` vía copia del statement + cruce, y `userid` se descarta igual que en statements.

## EXPLAIN y search_path

- El EXPLAIN se ejecuta sobre el `query_text` del digest (sin calificar). La resolución de tablas queda determinada por el `search_path` de la conexión del pipeline, no por el del rol dueño de la query.
- Verificado en vivo: con `search_path` que excluye la schema, `EXPLAIN SELECT * FROM sbtest1` falla con `relation does not exist`; con la schema incluida, genera plan.
- `ExplainStage` ahora emite la sentencia de contexto antes de cada `EXPLAIN`: en Postgres `SET LOCAL search_path TO <schema_name>` (identificador citado con `identifier_preparer`; si el candidato no tiene `schema_name`, `SET LOCAL search_path TO DEFAULT`); en MySQL `USE <schema_name>` (la conexión no trae database por defecto y el digest no califica). Al ser `LOCAL`/`USE` por candidato y secuenciales, no afectan al resto del pipeline.
- Limitación: para un rol con varias schemas reales, Postgres explora bajo la primera del `search_path`; y en MySQL no hay "reset a sin database" — un candidato sin `schema_name` hereda el `USE` del anterior.