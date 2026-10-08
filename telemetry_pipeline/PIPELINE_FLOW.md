# Flujo del pipeline y patrones de diseño

Pipeline agentless de telemetría: captura métricas de `pg_stat_statements` (Postgres) y `performance_schema` (MySQL), selecciona las queries candidatas a explicar, normaliza planes y queries, y encola un snapshot validado en PGMQ para su consumo.

## Flujo de extremo a extremo

```
dos puntos de entrada, una sola maquina de ejecucion:

  main.py (integracion)                     main_sandbox.py (sandbox, tests y CI)
    load_registered_targets()                 ENGINES = (("postgres", get_connection_postgres),
      SELECT ... FROM registered_databases               ("mysql", get_connection_mysql))
      WHERE is_active                       dos targets fijos resueltos con
      Fernet(AUTH_ENCRYPTION_KEY) → Target    MONITOR_PG_* / MONITOR_MY_*
      sin tabla / sin filas / sin clave       (la tabla no existe en CI y no
      → None: no se extrae nada               hay filas registradas alli)
          │                                        │
          └──────────────────┬─────────────────────┘
                             v
               run_targets(targets)                             main.py
                 por target: try run_engine(...) except → logger.error(... engine_failed)
                             │
                             v

run_engine(dialect, connection_factory, db_id)                  main.py
  │  db_id → payload["db_id"]   pisa la constante DB_ID de enrich con el
  │                             database_identifier de la fila
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
  │     │           server_start_timestamp, columns
  │     │     conn.commit()  ← M-12: la transacción de lectura se cierra YA. No
  │     │     retener snapshot/vacuum (PG) ni MVCC (MySQL) durante el resto del ciclo.
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
  │     │     cada candidato corre en una transacción corta propia:
  │     │     BEGIN → SET LOCAL search_path / USE → EXPLAIN → COMMIT (M-12).
  │     │     rollback si falla; el USE de MySQL es estado de sesión y sobrevive.
  │     │     stats["query_explain"] = [{query_id, explain_source, plan}]
  │     │     EXPLAIN_NORMALIZERS[dialect]().normalize(stats)
  │     │       Postgres: _walk_plan sobre arbol JSON → logical_shape + physical_operations
  │     │       MySQL:    _walk_plan sobre query_block → idem
  │     │     stats["canonic_explains"] = [{query_id, explain_source, canonical_plan}]
  │     │
  │     ├─ (4) NormalizeStage.execute(stats)                 stages/normalize.py
  │     │     create_canonic_queries()      literal→placeholder via sqlglot (canonic_query)
  │     │     stats = collector.normalize_engine_artifacts(stats)   → hook por motor:
  │     │         MySQL: locks bool, blocking_pids list[int], limpieza de predicados
  │     │         Postgres: timestamps a ISO sin tz
  │     │     normalize_querytext_active()  active_queries pierde query_text,
  │     │                                   conserva canonic_query ("Not available" si vacío)
  │     │
  │     └─ (5) EnrichStage.execute(stats)                     stages/enrich.py
  │           stats["db_id"] = DB_ID        (identidad del job de análisis;
  │                                          run_engine lo pisa con el
  │                                          database_identifier de la fila)
  │
  ├─ SnapshotPayload.from_snapshot(payload)                  models/snapshot.py
  │     valida el dict contra los modelos pydantic (11 secciones)
  │     ValidationError → logger.error + return None (motor omitido, no aborta)
  │
  ├─ snapshot.to_json()                    → JSON canónico (model_dump_json)
  │
  └─ send_to_queue(payload_json, querylens_engine)           enqueue.py
        SELECT * FROM pgmq.send('analyze_job', CAST(:payload AS JSONB))
        → msg_id (print en main)
```

## Targets de conexión (registered_databases)

Las credenciales no están más atadas a dos pares de variables de entorno. El front y el auth service registran cada base del cliente en `registered_databases` (DDL en `querylens_database/registered_databases.sql`, montado por `docker-compose.yml`) con host, puerto, usuario y password cifrados con Fernet. `config/registered.py` hace el `SELECT ... WHERE is_active`, descifra con `AUTH_ENCRYPTION_KEY` y devuelve un `Target` por fila.

| Punto | Decisión |
|-------|----------|
| Conexión al select | `get_connection_querylens_db()` (`ql_user`, dueño de la tabla) |
| Identidad en el snapshot | `database_identifier` de la fila (`db_<8hex>`); lo pisa `run_engine` **antes** de validar |
| Motor | `postgresql\|postgres → "postgres"`, `mysql → "mysql"`; otro valor → log + skip de la fila |
| URL | `sqlalchemy.engine.URL.create(...)` (la password viene de Fernet y puede traer `@`, `:`) |
| Postgres | URL **con** `database_name` |
| MySQL | URL **sin** base (el `USE` lo emite `ExplainStage`), `pool_size=1, max_overflow=10, pool_pre_ping=True` |
| Fila indecifrable / puerto malo / engine desconocido | log + se omite esa fila, las demás siguen |
| Fila que no conecta (host caído, rol rotado) | `run_targets()` lo atrapa por target: `logger.error(... engine_failed ...)` y sigue con el resto |
| Sin tabla, 0 filas activas, sin clave o clave ilegible | `load_registered_targets()` devuelve `None` y `main()` no extrae nada (`logger.warning`) |

`main.py` no contiene ningún par de credenciales: decide contra qué correr leyendo la tabla, y si no hay nada registrado se detiene. El par `postgres`/`mysql` que usan `ql_sandbox`, los tests de integración y el job de CI vive literal en **`main_sandbox.py`**, con su propio `main()` y el mismo `run_targets()`. `test_e2e_main.py` ejercita ese par y una fila real de la tabla (la crea, la usa y la borra); `test_registered_targets.py` cubre el loader con mocks, sin abrir ninguna conexión.

## Automatización (runner.py)

`main.py` corre una vez; **`runner.py`** es el daemon que repite esa misma vuelta cada `EXTRACT_INTERVAL_S` (10 s por defecto, mismo criterio para todas las filas). Es el proceso que se despliega en contenedor.

| Decisión | Implementación |
|----------|----------------|
| Cadencia | 10 s por defecto; cualquier env no usable (`vacío`, `abc`, `0`, `-3`, `inf`) vuelve a 10 s con warning |
| Anclaje | La espera se mide **desde el inicio del ciclo**: si la vuelta duró 0,4 s espera 9,6 s; si duró más que el intervalo espera 0 y no se acumulan ticks atrasados (un solo hilo, nunca hay dos ciclos solapados) |
| Gate de encolado | **Ninguno**: se encola siempre. La retención de PGMQ la resuelve el consumidor |
| Fallo de ciclo | backoff 10 → 20 → 40 → 60 s (tope) y vuelve al intervalo normal tras una vuelta buena; un target que revienta ya lo aislaba `run_targets()` |
| Ciclo de vida | `run_engine()` cierra los dos engines que creó (target y cola) en un `finally`: sin `dispose()`, cada 10 s dejaría pools abiertos y se agotaría `max_connections` |
| Señales | `SIGINT`/`SIGTERM`/`SIGBREAK` marcan stop y el loop termina tras el ciclo en curso |
| Logging | Estado de targets **por transición** (0↔N) a INFO —a 10 s un aviso por ciclo serían 8 640 al día—; detalle de cada vuelta (targets, duración) a DEBUG en `logs/pipeline.log` |
| Sin targets | No llama a `run_targets`, solo re-consulta: la tabla puede llenarse mientras el daemon corre |

Despliegue: `telemetry_pipeline/Dockerfile` (python:3.11-slim, compila `psycopg2` y purga el toolchain) y el servicio **`pipeline`** en `docker-compose.yml`, con `depends_on: postgres`, `restart: unless-stopped`, `host.docker.internal:host-gateway` (las filas registradas suelen apuntar al host) y `EXTRACT_INTERVAL_S=${EXTRACT_INTERVAL_S:-10}`. Los logs caen en el volumen `pipeline_logs`.

### Qué hace y qué no hace (estado actual)

Un ciclo es, completo:

```
load_registered_targets()        SELECT ... WHERE is_active   (1 conexión a la cola)
  → si devuelve None/[]: log de transición y no extrae nada, re-consulta al siguiente ciclo
  → si hay targets:    run_targets()                          (1 vuelta por fila: extrae,
                                                               valida, encola, dispose)
  → espera anclada al inicio del ciclo
```

Y explícitamente **no**:

| No hace | Por qué |
|---------|---------|
| No lee `MONITOR_PG_*` / `MONITOR_MY_*` | El host, puerto, usuario y password de cada target salen de la fila (Fernet con `AUTH_ENCRYPTION_KEY`). Esas variables solo las consume `main_sandbox.py`; están en el servicio `pipeline` únicamente por si alguien corre esa entrada dentro del contenedor |
| No consume `analyze_job` | Solo produce. **Sin consumidor la cola crece**; el tamaño por snapshot **no está acotado** (va el payload completo: statements + candidatos + planes + columnas/índices/tablas). Medido en los fixtures golden: **37–49 KB con 12–14 statements** (~0,5–1 KB por statement adicional) → una base con cientos/miles de statements genera snapshots de cientos de KB a varios MB. Crecimiento ≈ `bytes_promedio × 8 640/día × nº de bases`. Verificar con `SELECT count(*), pg_size_pretty(avg(pg_column_size(message))::bigint) FROM pgmq.q_analyze_job` |
| No guarda estado entre ciclos | Sin dedupe ni gate por `counters_epoch`: cada vuelta emite un snapshot por base aunque nada haya cambiado. La retención de mensajes ya leídos es del consumidor |
| No corre en paralelo | Un hilo, una vuelta a la vez; el aislamiento por target (dentro de `run_targets()`) es lo que evita que una base caída tape a las demás |
| No decide nada del front/auth | Quién registra y activa filas es el auth service; el runner solo las lee. Si se agrega una fila mientras corre, la ve en el próximo ciclo |

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
- `query_id` es `Union[str, int, None]`: MySQL lo emite como `{schema}/{digest}` (string — el digest hex solo hubiera perdido una de cada par de filas con el mismo digest en dos schemas, que es la PK real de `events_statements_summary_by_digest`), Postgres como `queryid` bigint.
- Cada fila de `statements`, `locks` y `active_queries` lleva `database_name` (`str | None`): la recolección es server-wide (pg_stat_statements/pg_locks/pg_stat_activity y digests MySQL leen todo el servidor) y la atribución por fila permite saber de qué base vino cada cosa bajo un mismo `db_id`. Un candidato de Postgres de otra base se explica en una **conexión dedicada a esa base** (una conexión PG no cambia de base con `USE`); en MySQL el `USE {schema|database}` alcanza, y si no hay contexto de schema el candidato se salta (ver `references/004_decisiones_contrato.md`).
- `ready_for_explain` es la puerta de entrada a `EXPLAIN`. `init_ready_for_explain` solo la inicializa en `False` y deduplica por `query_id`; la decisión real la toma `mark_explainable`, por motor: Postgres marca todo como listo porque `EXPLAIN (GENERIC_PLAN)` resuelve los placeholders `$1` sin conocer los valores; MySQL marca solo lo que tiene `QUERY_SAMPLE_TEXT`, porque su `EXPLAIN` necesita literales reales.
- Antes (`c730da1`) la explicación exigía que el `query_id` del candidato estuviera en `active_queries`, es decir que la query se estuviera ejecutando en ese instante. Ese cruce ya no aplica desde `93beb5e`: cada motor produce el plan por su vía nativa sin necesitar la query en vivo, así que `init_ready_for_explain` ya no lee `active_queries` (aunque el nombre y la firma los conserven).
- La asimetría es propia de cada motor, no una inconsistencia: `pg_stat_statements.query` ya viene normalizado con `$1` y `EXPLAIN (GENERIC_PLAN)` (PG16+) lo convierte en plan. `DIGEST_TEXT` de MySQL trae `?`, que no produce plan; `QUERY_SAMPLE_TEXT` trae la consulta con valores reales, que sí lo produce.
- El ciclo del runner por defecto es de **10 s** (`interval_from_env()`, pensado para pruebas/sandbox). Para producción la cadencia prevista es de **30–60 s** (decisión de deploy, sin tocar código): cada vuelta emite un snapshot por base, así que el intervalo es la palanca directa de volumen de la cola (ver la fila "No guarda estado entre ciclos").
- La transacción del target se maneja en dos tiempos: el **collect** la cierra con `commit()` al terminar (no retiene snapshot/vacuum de Postgres ni MVCC de MySQL durante el resto del ciclo), y cada **EXPLAIN** corre en su propia **transacción corta** (`BEGIN → SET LOCAL/USE → EXPLAIN → COMMIT`; rollback en fallo; el `USE` de MySQL es estado de sesión y sobrevive al commit). Ver `references/004_decisiones_contrato.md` (M-12).
- `EXPLAIN (GENERIC_PLAN)` requiere PostgreSQL 16+. En versiones anteriores el `EXPLAIN` falla, la excepción se captura por consulta, se hace `rollback` y el candidato queda fuera de `canonic_explains` sin abortar el ciclo.
- `explain_source` viaja en `canonic_explains` para que el consumidor sepa con qué fidelidad se obtuvo el plan: `"generic"` (Postgres, plan sin valores concretos) o `"sample"` (MySQL, plan de una ejecución real).
- `QUERY_SAMPLE_TEXT` se trunca a `performance_schema_max_digest_text_length` (1024 por defecto). Una consulta larga queda con SQL inválido: se trata igual que cualquier fallo de `EXPLAIN`, y aparece como no explicable.
- `query_sample_text` es **flujo interno, no campo del contrato**: MySQL lo produce (`QUERY_SAMPLE_TEXT`, literales reales) y solo lo usan `mark_explainable` y el `EXPLAIN FORMAT=JSON <sample>` en memoria. El payload lo **descarta** (`extra="ignore"`; ver `references/004_decisiones_contrato.md`): la cola no debe conservar texto real de las queries — solo viajan los normalizados (`query_text` de Postgres con `$1`, `DIGEST_TEXT` de MySQL con `?`). El fix de M-10 que lo declaró en el contrato fue un error y quedó revertido; este requisito es la razón por la que el campo nunca estuvo en el contrato.
- Los scripts que medían la mecánica basada en logs se descartaron; la justificación vive en `references/005_evaluacion_alternativas_query_real.md` (cobertura incompleta del slow log, permisos de FS, config del servidor, texto logueado ≠ texto planificado). La carpeta `research/` se eliminó del repo.
- Se explica después de `CandidatesStage` y antes de `NormalizeStage`: el EXPLAIN usa el texto de estadísticas; la normalización (canonicalización) ocurre después.

## Nota sobre el README del repo

`README.md` enlaza informes (`SegundoInforme.md`, `InformeFinal.md`, `instalacion.md`, `Desarrollo.md`, `plan_patterns.md`) que aún no existen **a propósito**: se escribirán cuando el proyecto los tenga. No son links rotos pendientes de arreglar ni código muerto de documentación.