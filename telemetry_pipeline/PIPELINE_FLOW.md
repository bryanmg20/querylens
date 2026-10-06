# Flujo del pipeline y patrones de diseño

Pipeline agentless de telemetría: captura métricas de `pg_stat_statements` (Postgres) y `performance_schema` (MySQL), selecciona las queries candidatas a explicar, normaliza planes y queries, y encola un snapshot validado en PGMQ para su consumo.

## Flujo de extremo a extremo

```
main.py
  │  ENGINES = [("postgres", get_connection_postgres), ("mysql", get_connection_mysql)]
  │  for dialect, engine_factory in ENGINES:
  │
  ├─ Engine_Factory.create_collector(dialect, engine)        → collector (Strategy por motor)
  │     ├─ Mysql_Collector(engine)      source_dialect="mysql"
  │     └─ Postgres_Collector(engine)   source_dialect="postgres"
  │
  ├─ Orchestrator(collector).run_pipeline()                  → stats (dict con el snapshot crudo)
  │     │
  │     ├─ (1) CollectStage.execute(stats, conn)             telemetry_pipeline/stages/collect.py
  │     │     for key, query in collector.queries:           ejecuta las consultas del motor
  │     │     stats[key] = list[dict] ; falla aislada con rollback {key: None}
  │     │     keys: indexes, tables, statements, locks, active_queries,
  │     │           stats_reset_timestamp, columns
  │     │
  │     ├─ (2) CandidatesStage.execute(stats)                stages/candidates.py
  │     │     stats = collector.preprocess_statements(stats) → hook por motor (MySQL calcula
  │     │                                                     stddev y coeff de variación)
  │     │     select_high_impact_time_statements()  top 10 por tiempo total
  │     │     select_unstable_statements()          coeff>2 y mean>10ms
  │     │     select_disk_spill_indicator()         disk_spill_indicator > 0
  │     │     select_candidates_to_explain()        dedupe por query_id + selected_by
  │     │                                           (SQL no explicable va a non_explainable_candidates)
  │     │     init_ready_for_explain()               dedupe por query_id +
  │     │                                           ready_for_explain=False (valor inicial;
  │     │                                           no mira active_queries; la decisión
  │     │                                           real es mark_explainable)
  │     │     collector.mark_explainable(stats)     → hook por motor: Postgres=True siempre,
  │     │                                           MySQL=True si query_sample_text no está vacío
  │     │
  │     ├─ (3) ExplainStage.execute(stats, conn)             stages/explain.py
  │     │     solo para candidatos con ready_for_explain
  │     │     "EXPLAIN (GENERIC_PLAN, FORMAT JSON) <query>"  → Postgres (PG16+, placeholders $1)
  │     │     "EXPLAIN FORMAT=JSON <query_sample_text>"     → MySQL (usa valores reales)
  │     │     stats["query_explain"] = [{query_id, explain_source, plan}]
  │     │     EXPLAIN_NORMALIZERS[dialect]().normalize(stats)
  │     │       Postgres: _walk_plan sobre arbol JSON → logical_shape + physical_operations
  │     │       MySQL:    _walk_plan sobre query_block → idem
  │     │     stats["canonic_explains"] = [{query_id, explain_source, canonical_plan}]
  │     │
  │     ├─ (4) NormalizeStage.execute(stats)                 stages/normalize.py
  │     │     anonimize_query_text()        restaura query_text desde statements (top_impact lo perdió)
  │     │     create_canonic_queries()      literal→placeholder via sqlglot (canonic_query)
  │     │     stats = collector.normalize_engine_artifacts(stats)   → hook por motor:
  │     │         MySQL: locks bool, blocking_pids list[int], limpieza de predicados
  │     │         Postgres: timestamps a ISO sin tz
  │     │     normalize_querytext_active()  active_queries pierde query_text,
  │     │                                   conserva canonic_query ("Not available" si vacío)
  │     │
  │     └─ (5) EnrichStage.execute(stats)                     stages/enrich.py
  │           stats["db_id"] = DB_ID        (identidad del job de análisis)
  │
  ├─ SnapshotPayload.from_snapshot(payload)                  models/snapshot.py
  │     valida el dict contra los modelos pydantic (11 secciones)
  │     ValidationError → logger.error + continue (motor omitido, no aborta)
  │
  ├─ snapshot.to_json()                    → JSON canónico (model_dump_json)
  │
  └─ send_to_queue(payload_json, querylens_engine)           enqueue.py
        SELECT * FROM pgmq.send('analyze_job', CAST(:payload AS JSONB))
        → msg_id (print en main)
```

## Patrones de diseño

| Patrón | Dónde | Código |
|--------|-------|--------|
| **Strategy** | Cada motor es una estrategia de la misma interfaz `DB_Engine_Collector` (queries, preprocess, normalize_engine_artifacts). Seleccionada en runtime por el dialecto, sin `if` en los stages. | `collectors/base.py:1`, `collectors/mysql/collector.py:14`, `collectors/postgres/collector.py:14` |
| **Simple Factory** | `Engine_Factory.create_collector(dialect, engine)` devuelve la estrategia correcta y lanza `ValueError` ante un dialecto desconocido. | `collectors/factory.py:3` |
| **Registry** | `EXPLAIN_NORMALIZERS = {"postgres": ..., "mysql": ...}` mapea dialecto→normalizador; `ExplainStage` hace lookup por `source_dialect`. | `stages/explain_normalizer.py:296`, `stages/explain.py:11` |
| **Pipeline** | `Orchestrator` encadena stages en orden fijo; cada stage implementa `execute`. Composición lineal + coordinador (director). | `orchestrator.py:8` |
| **Template Method** | Misma firma de etapa, pero el esqueleto `CandidatesStage`/`NormalizeStage` delega hooks `preprocess_statements`, `mark_explainable` y `normalize_engine_artifacts` a la estrategia del motor. | `stages/candidates.py:7`, `stages/normalize.py:20` |
| **Facade** | `SnapshotPayload.from_snapshot` / `to_json` ocultan validación y serialización pydantic a `main.py`. | `models/snapshot.py:157` |
| **DTO** | Los 15 modelos pydantic son el contrato de transporte entre el pipeline y la cola; `to_json` es la representación wire. | `models/snapshot.py` |
| **Command (variante)** | Cada `Stage.execute(stats, conn)` es un comando autocontenido y comprobable de forma aislada. | `stages/*.py` |

## Patrones estructurales de alto nivel

- **Arquitectura en capas (layered)**: `collectors` (datos) → `stages` (lógica de análisis) → `enqueue`/cola (integración saliente). Las dependencias fluyen de arriba hacia abajo; la única inversión leve es que `collectors` usan helpers de `stages.normalize` (normalizers compartidos por motor).
- **Pipeline secuencial**: el snapshot `stats` viaja de stage en stage mutado por pasos idempotentes; enriquecimiento al final (`db_id`).
- **Registro de estrategias** para normalizadores de plan y selección por razones (`selected_by` agrega motivos; dedupe por `query_id`).
- **Contrato de datos explícito en la frontera**: validación strict-ish en el punto de emisión (antes de encolar), no en el consumo.

## Tests que confirman los patrones de la arquitectura

Sí — además de los tests de comportamiento, `tests/test_architecture.py` (19 tests, marker `contract`) fija los patrones estructurales:

- **Strategy**: ambas clases heredan `DB_Engine_Collector`, exponen el mismo contrato (7 claves de `queries`, `source_dialect`, hooks callables), y el hook `preprocess_statements` solo está especializado en MySQL.
- **Simple Factory**: devuelve la estrategia correcta por dialecto y `ValueError` para "oracle".
- **Registry**: `EXPLAIN_NORMALIZERS` cubre exactamente `{postgres, mysql}` y sus instancias satisfacen `normalize`.
- **Pipeline**: los 5 stages exponen `execute`; `Orchestrator` compone `Collect → Candidates → Explain → Normalize → Enrich` en ese orden.
- **Facade/DTO**: round-trip `from_snapshot → to_json → model_validate_json` preserva `db_id` y el número de statements; `to_json` es JSON encolable; payload inválido rompe con `ValidationError`.
- **Hook de Template**: `DB_Engine_Collector.normalize_engine_artifacts` y `mark_explainable` devuelven `stats` intactos (contrato de la base), y los selectores encadenados por el stage siguen presentes y callables.
- `tests/test_collectors_explainable.py` fija la semántica de `mark_explainable` por motor: Postgres marca todo listo, MySQL respeta `query_sample_text` nulo, vacío o solo-espacios.

Los tests de arquitectura son **contratos estructurales**: validan "qué interfaz debe existir", no el detalle de implementación de cada rol. Si alguien reemplaza el Strategy por `if/elif` de dialectos o rompe la firma de `execute`, estos tests fallan aunque el comportamiento siga pasando.

## Notas de diseño deliberado

- `stats["source"]` fue eliminado: el dialecto vive en el collector (`source_dialect`); replicarlo en el payload rompía la unicidad del evento en la cola.
- `query_id` es `Union[str, int, None]`: MySQL lo emite como digest hex (string), Postgres como `queryid` bigint.
- `ready_for_explain` es la puerta de entrada a `EXPLAIN`. `init_ready_for_explain` solo la inicializa en `False` y deduplica por `query_id`; la decisión real la toma `mark_explainable`, por motor: Postgres marca todo como listo porque `EXPLAIN (GENERIC_PLAN)` resuelve los placeholders `$1` sin conocer los valores; MySQL marca solo lo que tiene `QUERY_SAMPLE_TEXT`, porque su `EXPLAIN` necesita literales reales.
- Antes (`c730da1`) la explicación exigía que el `query_id` del candidato estuviera en `active_queries`, es decir que la query se estuviera ejecutando en ese instante. Ese cruce ya no aplica desde `93beb5e`: cada motor produce el plan por su vía nativa sin necesitar la query en vivo, así que `init_ready_for_explain` ya no lee `active_queries` (aunque el nombre y la firma los conserven).
- La asimetría es propia de cada motor, no una inconsistencia: `pg_stat_statements.query` ya viene normalizado con `$1` y `EXPLAIN (GENERIC_PLAN)` (PG16+) lo convierte en plan. `DIGEST_TEXT` de MySQL trae `?`, que no produce plan; `QUERY_SAMPLE_TEXT` trae la consulta con valores reales, que sí lo produce.
- `EXPLAIN (GENERIC_PLAN)` requiere PostgreSQL 16+. En versiones anteriores el `EXPLAIN` falla, la excepción se captura por consulta, se hace `rollback` y el candidato queda fuera de `canonic_explains` sin abortar el ciclo.
- `explain_source` viaja en `canonic_explains` para que el consumidor sepa con qué fidelidad se obtuvo el plan: `"generic"` (Postgres, plan sin valores concretos) o `"sample"` (MySQL, plan de una ejecución real).
- `QUERY_SAMPLE_TEXT` se trunca a `performance_schema_max_digest_text_length` (1024 por defecto). Una consulta larga queda con SQL inválido: se trata igual que cualquier fallo de `EXPLAIN`, y aparece como no explicable.
- Los scripts que medían la mecánica basada en logs se descartaron; la justificación vive en `references/005_evaluacion_alternativas_query_real.md` (cobertura incompleta del slow log, permisos de FS, config del servidor, texto logueado ≠ texto planificado). La carpeta `research/` se eliminó del repo.
- Se explica después de `CandidatesStage` y antes de `NormalizeStage`: el EXPLAIN usa el texto de estadísticas; la normalización (canonicalización/anonimización) ocurre después.

## Nota sobre el README del repo

`README.md` enlaza informes (`SegundoInforme.md`, `InformeFinal.md`, `instalacion.md`, `Desarrollo.md`, `plan_patterns.md`) que aún no existen **a propósito**: se escribirán cuando el proyecto los tenga. No son links rotos pendientes de arreglar ni código muerto de documentación.