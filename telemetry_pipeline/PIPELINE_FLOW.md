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
  │     │     for key, query in collector.queries:           ejecuta las 7 consultas del motor
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
  │     │     select_explain_ready()                real_query_found=True si el query_id
  │     │                                           está activo en pg_stat_activity (idem performance_schema)
  │     │
  │     ├─ (3) ExplainStage.execute(stats, conn)             stages/explain.py
  │     │     solo para candidatos con real_query_found
  │     │     "EXPLAIN (FORMAT JSON) <query>"        → Postgres
  │     │     "EXPLAIN FORMAT=JSON <query>"          → MySQL
  │     │     stats["query_explain"] = [{query_id, plan}]
  │     │     EXPLAIN_NORMALIZERS[dialect]().normalize(stats)
  │     │       Postgres: _walk_plan sobre arbol JSON → logical_shape + physical_operations
  │     │       MySQL:    _walk_plan sobre query_block → idem
  │     │     stats["canonic_explains"] = [{query_id, canonical_plan}]
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
| **Template Method** | Misma firma de etapa, pero el esqueleto `CandidatesStage`/`NormalizeStage` delega hooks `preprocess_statements` y `normalize_engine_artifacts` a la estrategia del motor. | `stages/candidates.py:7`, `stages/normalize.py:20` |
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
- **Pipeline**: los 5 stages exponen `execute`; `Orchestrator` compone los 5 stages en su orden.
- **Facade/DTO**: round-trip `from_snapshot → to_json → model_validate_json` preserva `db_id` y el número de statements; `to_json` es JSON encolable; payload inválido rompe con `ValidationError`.
- **Hook de Template**: `DB_Engine_Collector.normalize_engine_artifacts` devuelve `stats` intactos (contrato de la base), y los selectores encadenados por el stage siguen presentes y callables.

Los tests de arquitectura son **contratos estructurales**: validan "qué interfaz debe existir", no el detalle de implementación de cada rol. Si alguien reemplaza el Strategy por `if/elif` de dialectos o rompe la firma de `execute`, estos tests fallan aunque el comportamiento siga pasando.

## Notas de diseño deliberado

- `stats["source"]` fue eliminado: el dialecto vive en el collector (`source_dialect`); replicarlo en el payload rompía la unicidad del evento en la cola.
- `query_id` es `Union[str, int, None]`: MySQL lo emite como digest hex (string), Postgres como `queryid` bigint.
- `real_query_found` es la puerta de entrada a `EXPLAIN`: solo se explica en vivo lo que está ejecutándose al capturar el snapshot (agentless de bajo impacto).
- Se explica después de `CandidatesStage` y antes de `NormalizeStage`: el EXPLAIN usa el `query_text` en vivo; la normalización (canonicalización/anonimización) ocurre después.