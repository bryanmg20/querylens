# Revisión de código — `telemetry_pipeline` y `querylens_database`

**Fecha:** 2026-10-07
**Alcance:** `telemetry_pipeline/**` (código, tests, docs, Dockerfile/CI), `querylens_database/*.sql` y su integración en `docker-compose.yml`. Fuera de alcance: `auth_service/`, `frontend/`, `ql_sandbox/`.
**Método:** lectura completa de los módulos del pipeline, verificación de afirmaciones de los docs, revisión de la DDL, ejecución de la suite (`466 passed, 21 deselected` — las de integración requieren contenedores) y reproducción de los hallazgos sospechosos con scripts mínimos.
**Contexto:** proyecto en desarrollo. Las severidades son relativas a ese estado: "Alta" = pierde datos o puede parar el daemon en producción.

> **Actualizado (misma rama, ronda M-6 a M-12):** M-6 resuelto (los textos del fallback ya no engañan, y la implementación real queda anotada como pendiente); **M-7 parcial** (`_base_operation` único y `anonimize_query_text` eliminada; quedan las duplicaciones de normalización y del engine); M-8 resuelto (helper `URL.create` único + test de password round-trip); M-9 resuelto como documentación; M-12 resuelto (commit tras collect + transacción corta por EXPLAIN); M-5 y A-3 quedan con **notas sin cambio de contrato**; M-11 diferida. Mutation de los módulos tocados: 0 sobrevivientes. Suite **520 passed**. Detalles por hallazgo abajo.

> **Actualizado (mismo día, rama `fix/pipeline-review-202610`, commit `bf13020`):** A-1, A-2 y M-4 resueltos; A-3 corregido en la parte de docs/medición/observabilidad y cerrado sin `LIMIT` por decisión (el límite real de retención es del consumidor; ver nota por hallazgo). Suite tras los cambios: **478 unit/contract + 21 integration, todo verde**. Pendientes: M-5 a M-13, B-1 a B-12 y la defensa en profundidad del `EXPLAIN`.

> **Revisión adversarial posterior (mismo día) — N-1 a N-3 resueltos; M-* diferidos:** una segunda pasada verificando los supuestos de la revisión frontal encontró 3 hallazgos nuevos, todos corregidos en el working tree:
> - **N-1 (ALTA):** `CollectStage` escribe `stats[key] = None` cuando una query de telemetría falla, y varios loops iteraban `stats.get(key, [])` — con la clave presente y valor `None` crasheaban con `TypeError: 'NoneType' object is not iterable` (reproducido en `active_queries` y `locks`). Corregido con el patrón `or []` que ya usaba `normalize_active_query_timestamps`: `stages/canonicalizers.py:55` (`normalize_querytext_active`) y `stages/normalize.py:69,77` (`normalize_blocking_pids`, `normalize_locks`). Tests de regresión: `test_canonicalizers.py::test_normalize_querytext_active_none_section`, `test_normalize.py::test_normalize_locks_none_section` / `test_normalize_blocking_pids_none_section`, y cadena completa `TestNoneSectionsFromFailedCollect` en `test_normalize_stage.py` (MySQL y Postgres).
> - **N-2 (MEDIA):** el campo `stats_reset` no es un "reset de contadores": es la hora de arranque del servidor (`pg_postmaster_start_time()`, `now() - Uptime`). Renombrado a `server_start_time` (campo), `server_start_timestamp` (sección/colección), y `ServerStartRow` (modelo) en `models/snapshot.py`, `models/stats.py`, ambos `collectors/*/queries.py` y `collectors/*/collector.py`. Golden fixtures, `test_orchestrator.py`, `test_snapshot.py`, `test_architecture.py`, `test_integration_engines.py` y `test_e2e_main.py` reemplazan las claves viejas. Documentada la semántica en `references/004_decisiones_contrato.md`. Nota: `column_name: "stats_reset"` en `tests/golden/postgres_snapshot.json:694` es una **columna real de la base monitoreada**, se dejó intacta.
> - **N-3 (MEDIA):** en MySQL, `index_size_bytes` no es el tamaño individual del índice: `st.index_length` es nivel tabla y se repite por fila de índice. Documentado en `collectors/mysql/queries.py` (comentario) y `references/004_decisiones_contrato.md`.
> - **M-* diferidos por decisión del usuario:** no se tocaron M-5 a M-13 (incluido no añadir `collect_errors`/`captured_at`: el contrato "vacío" vs "falló" queda como está — un snapshot vacío encolado "no sirve para nada", según decisión).

> **Hallazgo de alcance por servidor (misma rama; en la conversación era "N-1") — resuelto:** las queries de recolección leían el **servidor completo** (`pg_stat_statements` devuelve filas de todas las bases, `pg_locks`/`pg_stat_activity` son cluster-wide; MySQL digests todos los schemas), pero el snapshot pertenece a un solo `db_id`. Decisión del usuario: **todo entra, atribuido por fila**. Implementado y verificado:
> - `database_name` es campo declarado del contrato (`StatementRow`, `LockRow`, `ActiveQueryRow`): `pg_stat_statements → pg_database.datname`, `pg_locks.database → pg_database.datname` (NULL para advisory/txnid), `pg_stat_activity.datname`, y en MySQL `SCHEMA_NAME`/`object_schema` (donde base == schema).
> - MySQL `query_id` ya no es solo el digest hex: es `{schema}/{digest}`. La PK real de `events_statements_summary_by_digest` es `(SCHEMA_NAME, DIGEST)`: con el id solo-digest, el dedup por `query_id` descartaba una de cada par de filas con el mismo digest en dos schemas. El goldens regenerado trae ids `ql_demo/864c…`.
> - Postgres no tiene `USE` propio: un candidato de otra base (`database_name !=` la base conectada del target) se explica en una **conexión dedicada a esa base** (engine por base con `NullPool` + `postgres_connect_args`, cacheado por base y `dispose()` en `finally` de `ExplainStage`). MySQL, en cambio, se explica con `USE {schema|database}` en la misma conexión.
> - **Cierra MEDIA-13** con un giro respecto a la sugerencia original: en MySQL un candidato **sin contexto de schema** (ni `schema_name` ni `database_name`) **se salta con warning** en vez de heredar el `USE` del candidato anterior (heredarlo arma el plan contra el schema equivocado). Limitación documentada: la resolución de schema extranjero corre contra el catálogo de la base conectada, y `EXPLAIN` sobre otra base requiere privilegios del rol monitor ahí.
> - Verificado en vivo con una base scratch (PG): un statement ejecutado en la 2ª base aparece en `statements` con `database_name` propio y obtiene su plan. Suite completa **520 passed**; goldens regenerados con `ci/regenerate_goldens.py`.

> **Actualizado (ronda adversarial 2, 2026-10-08, HEAD `00bd8f2`):** segunda pasada adversarial sobre el árbol completo (verificó los "resueltos" de este informe, encontró 8 hallazgos nuevos y corrigió 3). **Nota de numeración:** la serie N-x de esta ronda es propia de esta pasada y no coincide con los N-1..N-3 de la ronda anterior. Corregidos:
> - **N-1 (MEDIA — privacidad):** el `EXPLAIN FORMAT=JSON` de MySQL corre sobre `QUERY_SAMPLE_TEXT` (con literales), así que `canonic_explains[*].predicate` traía valores reales al golden — verificado: `(a.id > 3000)`, `(sbtest1.c LIKE '%7%')`, `BETWEEN 2000 AND 4000` — **contradiendo la afirmación de privacidad de `references/004_decisiones_contrato.md:73`** ("la cola no contiene literales de datos reales"; el informe verificó `query_sample_text` y `query_text` pero no este canal). Corregido en `clean_mysql_explain_predicate_dynamic` (`stages/normalize.py`): literales → placeholder `$n` en la ruta sqlglot **y** en el fallback por regex (el fallback era un canal de fuga igual); `NULL`/`TRUE`/`FALSE` intactos. Golden MySQL parcheado offline (6 de 17 predicados; idempotente en los 11 restantes — la regeneración oficial con sandbox queda para el usuario). Contrato nuevo: `test_snapshot_contract.py::test_predicates_are_canonicalized` exige "sin literales" (MySQL: números sueltos y strings entrecomillados; PG: strings — `SubPlan 1` es nomenclatura de plan, no dato). Docs: nota nueva en `references/004` y `PIPELINE_FLOW.md:207`. Tests: 7 nuevos. Suite **510 passed**.
> - **N-2 (BAJA — ciclo de vida):** `with active.begin()` cierra la **transacción**, no la **conexión**: la conexión extranjera de `ExplainStage._foreign_connection` (`engine.connect()`, `explain.py`) nunca se cerraba — `dispose()` no toca lo enganchado y sin `.close()` explícito dependía del GC. Corregido con `finally: if active is not conn: active.close()` (el guard es imprescindible: `conn` la cierra el orchestrator). Tests: cierre en éxito, cierre cuando el EXPLAIN falla, y que la conexión compartida **nunca** se cierra desde la etapa.
> - **N-4 (BAJA — docs/despliegue):** `PIPELINE_FLOW.md:134` prometía `QL_LOG_LEVEL=${QL_LOG_LEVEL:-INFO}` como palanca de despliegue sin estar cableado en el servicio `pipeline` del compose, y citaba tres nombres muertos (`pg_isready -d ql_metrics` — el healthcheck real usa `${QUERYLENS_DB}`; `01_schema.sql` — el init real es `init.sql`; `CollectorsStage` — la función es `main._log_payload_size`). Añadida `QL_LOG_LEVEL: ${QL_LOG_LEVEL:-INFO}` al compose y corregidas las tres referencias.
> - **Quedan abiertos de esa ronda (no tocados):** N-3 (`runner.py:34` fija DEBUG y anula `QL_LOG_LEVEL` para su módulo), N-5 (`load_dotenv(override=True)` en import), N-6 (`LOCKS_QUERY` incluye los locks del propio pipeline), N-7 (dos focos del patrón `or []` residual: `selectors.py:5`, `mysql/collector.py:49`), N-8 (una excepción de lectura de la cola se disfraza de "sin bases" y repite WARNING cada ciclo).
> - **Correcciones al informe:** B-6 queda **resuelto-implícito** con `QL_LOG_LEVEL` (Q1, `b890bdf`): el `debug` de `registered.py` ya es alcanzable desde el env (queda el matiz de N-3: `runner.py:34` lo ignora para su propio módulo). Y el mutante `selectors: no inyectar ready_for_explain` estaba en INVALIDOS (`ci/mutants.py:204` esperaba 16 espacios, el código tiene 12 desde que se reescribió `init_ready_for_explain`): corregido, ahora 42/42 muertos en `normalize` y 22/22 en `explain`.
> - **Siguen vigentes sin cambios:** A-3 restante (LIMIT / envío por cambio de secciones), M-5, M-7 parcial (duplicación de normalización y de engine), M-11 (`captured_at`/`source_dialect`), la defensa en profundidad del `EXPLAIN` (prefijo sobre `query_sample_text`) y B-1 a B-12 — salvo B-6, corregido arriba. De esta ronda: N-3 y N-5 a N-8. **Pendiente del usuario:** regenerar los goldens con el sandbox (`ci/regenerate_goldens.py`); el parche offline de N-1 debe coincidir.
>
> **Actualizado (2026-10-08, auditoría C-1 a C-7):** **los 7 hallazgos críticos C-1 a C-7 están RESUELTOS** con fixes y tests de regresión:
> - **C-1** (`selectors.py:5`, `mysql/collector.py:49`): `statements=None` ya no crashea — patrón `or []` en `select_high_impact_time_statements`, `select_unstable_statements`, `select_disk_spill_indicator`, y `calculate_stddev_coeff`. Tests: `test_selectors.py::test_select_high_impact_handles_none_statements`, `test_select_unstable_handles_none_statements`, `test_select_disk_spill_handles_none_statements`, `test_collectors_explainable.py::TestMysqlCalculateStddevCoeff::test_handles_none_statements`.
> - **C-2** (`registered.py:149`): excepción genérica reemplazada por `ProgrammingError` (WARNING "tabla no existe") vs `OperationalError` (ERROR "falla real de conexión"). Test: `test_registered_targets.py::test_load_registered_targets_operational_error_logged_as_error`.
> - **C-3** (`canonicalizers.py:27-28`): **inválido** — el código ya devolvía `"Not available"` constante, no texto crudo.
> - **C-4** (`registered.py:147`): engine de cola cacheado como singleton módulo (`_get_queue_engine` + `_reset_queue_engine`). Test: `test_load_registered_targets_reuses_queue_engine`.
> - **C-5** (`main.py:58-64`): engines creados **dentro** del `try`; `finally` dispone solo los que se crearon. Test: `test_run_engine_disposes_target_even_if_queue_engine_fails`.
> - **C-6** (`collectors/postgres/queries.py:49-59`, `collectors/mysql/queries.py:115-126`): `LOCKS_QUERY` filtra usuario del pipeline (`session_user` / `CURRENT_USER`) via join a `pg_stat_activity` / `performance_schema.threads`. Tests: `test_queries_contract.py::test_pg_locks_query_excludes_pipeline_user`, `test_mysql_locks_query_excludes_pipeline_user`.
> - **C-7** (`explain.py:14-16`): `is_single_statement` reescrito con máquina de estados que ignora `;` en comillas simples, dobles y dollar-quoted (Postgres). Tests: `test_explain_stage.py::test_is_single_statement_*` (7 casos).
> Suite completa: **547 tests passed**.
>
> **Actualizado (2026-10-08, consultoría del flujo — rama `fix/pipeline-review-202610`):**
> - **CRÍTICO — locks MySQL con `process_id` NULL tiraban el snapshot MySQL completo** (`collectors/mysql/queries.py` `LOCKS_QUERY`). El join `data_locks → information_schema.innodb_trx` falla porque `innodb_trx` es un caché (~100 ms) materializado antes de leer `data_locks`: las transacciones nuevas quedan sin par, `process_id` llega NULL y `LockRow.process_id: int` rechaza todo el payload. **Verificado en vivo** con 4 workers `FOR UPDATE`: 48/60 lecturas con NULL (1459/1662 filas). **Resuelto:** el hilo sale de `data_locks.thread_id → performance_schema.threads` y se filtran hilos sin `processlist_id`; misma carga, 0/60. Tests: `test_queries_contract.py::test_mysql_locks_query_*`.
> - **MEDIA (privacidad) — literales hex/bit viajaban crudos** (`0xDEADBEEF`, `X'..'`, `b'..'`, `E'..'`): sqlglot los modela como `HexString`/`BitString`/`ByteString`, no `Literal`. Afectaba `active_queries[*].canonic_query` y los predicados MySQL de `canonic_explains`. **Resuelto:** `canonicalizers.DATA_LITERAL_NODES` (fuente única para ambos canales) + regex del fallback. Tests en `test_canonicalizers.py` y `test_normalize.py`.
> - **Decisión — solo lectura:** el rol monitor tiene solo `SELECT` (PrimerInforme). `EXPLAINABLE_COMMANDS = ("SELECT", "WITH")`; INSERT/UPDATE/DELETE van a `non_explainable_candidates`. La puerta se re-aplica en `ExplainStage` sobre el texto que realmente se explica (cierra **I-13**: MySQL explica `query_sample_text`). Comentarios/paréntesis iniciales se saltan antes del prefijo. Grants DML retirados de `ql_sandbox/init/*.sql`.
> - **Sandbox:** retirados el log de todas las sentencias de PG y el slow log de MySQL (Opción B descartada; ~1,2 GB acumulados en `pg_logs/`/`mysql_logs/`).
> - **Mutantes:** 3 de `selectors` estaban INVÁLIDOS (texto viejo tras C-1) y uno, al revalidarse, sobrevivía por un test que miraba la razón equivocada; corregidos. 12/12 muertos en `selectors` + 3 nuevos (locks, hex, puerta de explain). Tras `sql_text`: suite **599 passed** (unit/contract; 621 con integración); mutación completa: corregidos también 2 preexistentes — `collect: iterar secciones al reves` era equivalente (el orden de secciones no cambia ningún resultado) y se reemplazó por `collect: una seccion fallida corta las siguientes`; `logger: anadir el handler siempre` usaba `False or any(...)` (no-op) y, corregido a `False and`, reveló que ningún test cubría un segundo handler hacia el mismo archivo (`test_logger.py::test_rebuilt_handler_for_same_file_is_not_added_twice`). `main: no hacer dispose` estaba INVÁLIDO desde C-5; corregido.
> - **C-7 reabierto y resuelto:** `is_single_statement` tenía 3 bugs (el cierre de dollar-quote reabría otro porque `dollar_tag` se vaciaba antes de usar su largo; un `'` dentro de un comentario abría un string falso y ocultaba el `;`; un `;` dentro de comentario descartaba una query válida). Reescrito sobre un enmascarador léxico por dialecto, `stages/sql_text.py::mask_sql`: strings (con `\` en MySQL y `E'..'`), identificadores citados, dollar-quotes (solo PG, no tras identificador: `a$b$c` es identificador en MySQL), comentarios `--`/`#`/bloque (anidados en PG), `/*! */` de MySQL como **código**. Texto sin cerrar (sample truncado) → no explicable. `;` final seguido de comentario sigue contando como segunda sentencia (conservador).
> - **Solo lectura, ampliado:** `is_explainable_command` (misma base léxica) también manda a no explicables los `SELECT`/`WITH` que el monitor no puede EXPLAINar: CTE que escriben (`WITH d AS (DELETE … RETURNING)`) y cláusulas de bloqueo (`FOR UPDATE`/`NO KEY UPDATE`/`SHARE`/`KEY SHARE`, `LOCK IN SHARE MODE`). Palabras dentro de strings/comentarios no cuentan. Tests: `tests/test_sql_text.py` (48). Mutantes `sql_text` 9/9.
> - **Versiones mínimas (verificado, resuelto como documentación):** `STATEMENTS_QUERY` lee `stats_since` (PG 17+). Contra un `postgres:16` temporal: `statements` falla en cada ciclo (`column "stats_since" does not exist`), las otras 7 queries y `GENERIC_PLAN` funcionan. Decisión del usuario: soporte **PostgreSQL 17+ y MySQL 8.0+** (5.7/MariaDB fuera); documentado en `PIPELINE_FLOW.md` → "Versiones soportadas". Sin cambio de código.
> - **Formas de plan (verificado):** 31 formas por motor (joins, UNION, subconsultas correlacionadas, CTE recursiva, ventanas, ROLLUP, LATERAL/JSON_TABLE, WHERE imposible, sin FROM…) por el camino real EXPLAIN → normalizador → predicados → `CanonicExplain`: 62/62 con plan, 0 excepciones, 0 literales en predicados.
> - **Sandbox verificado tras recrear:** PG `logging_collector=off`, `log_min_duration_statement=-1`; MySQL `slow_query_log=0`; `querylens_monitor` solo `SELECT` en ambos (incluida la default ACL de PG). Directorios `pg_logs/`/`mysql_logs/` (1,2 GB) eliminados.

---

## Resumen ejecutivo

La arquitectura está bien resuelta (Strategy/Factory/Pipeline documentados y testeados, aislamiento por target, dispose en `finally`, `.env` sin trackear, credenciales Fernet). La documentación es inusualmente honesta (admiten "sin consumidor la cola crece", "no guarda estado").

Lo que no está bien resuelto cae en tres ejes:

1. **Robustez del contrato de salida:** la validación pydantic es "todo o nada" y deshace el aislamiento de fallos del `CollectStage`; un fallo de una query tira el snapshot completo.
2. **Ausencia de timeouts y límites:** ni timeouts de conexión/consulta, ni `LIMIT` en ninguna query de recolección, ni límite de candidatos a `EXPLAIN`, con un runner de un solo hilo.
3. **Docs vs. realidad en operación:** mensajes de log que anuncian un fallback que no existe, tamaño de snapshot subestimado en ~1 orden de magnitud, y un log a nivel DEBUG que jamás se emite.

---

## Hallazgos

### ALTA-1 — Un fallo en `statements` descarta el snapshot completo (el aislamiento de `CollectStage` no llega al payload)

**Dónde:** `orchestrator.py:22`, `models/snapshot.py:150-152,159`, `stages/collect.py:19-21`

`CollectStage` aisla fallos por clave (`stats[key] = None`), pero `Orchestrator` solo ejecuta Candidates/Explain/Normalize si `statements is not None`. En ese caso faltan las claves `top_impact_queries`, `non_explainable_candidates` y `canonic_explains`, que son **obligatorias sin default** en `SnapshotPayload`:

```
ValidationError: [('top_impact_queries',), ('non_explainable_candidates',), ('canonic_explains',)]
```

(Verificado ejecutando `SnapshotPayload.from_snapshot` con las secciones restantes en `None`.)

**Impacto:** si `STATEMENTS_QUERY` falla — `pg_stat_statements` no instalado, rol sin permisos, extensión caída — **no se encola nada**, ni siquiera las secciones que sí se recolectaron (locks, active_queries, indexes, tables, columns), y el daemon repite el mismo `snapshot_validation` cada 10 s para siempre. Es exactamente el escenario que el diseño de "falla aislada" pretendía cubrir (`test_orchestrator.py:215` comprueba que `statements` queda en `None`, pero ningún test empuja ese resultado a la validación).

**Sugerencia:** defaults `=[]` en esos tres campos, o better: exponer en el payload lo que falló (`collect_errors: {key: msg}`) para que el consumidor distinga "vacío" de "falló".

**Resuelto en `bf13020`:** defaults `=[]` en los tres campos derivados (`models/snapshot.py`); las 8 claves que escribe `CollectStage` siguen obligatorias. La vía `collect_errors` se dejó **fuera por decisión**: el log ya distingue "falló" de "vacío" y el campo nuevo exigía coordinar el contrato con el consumidor por un beneficio que hoy no se lee. Tests: `test_derived_sections_default_when_stage_skipped` (repro exacto: `statements=None` sin las 3 claves → valida).

---

### ALTA-2 — Cero timeouts + un solo hilo = una base colgada paraliza toda la extracción

**Dónde:** `config/registered.py:105-114`, `config/connections.py:24,42,67`, `runner.py:99-118`

- `create_engine` sin `connect_timeout` (psycopg2/libpq: **infinito por defecto**), sin `read_timeout`/`connect_timeout` de pymysql (este último sí trae 10 s por defecto), sin `pool_recycle`.
- Ninguna query lleva `SET statement_timeout` / `SET SESSION max_execution_time`.
- `runner` es secuencial por diseño: un target que no resuelve (firewall con DROP de paquetes, DNS colgado, `information_schema.columns` de MySQL con cientos de tablas sobre cargas altas) bloquea el ciclo y **todas las demás bases se quedan sin telemetría**. El backoff del runner no aplica: el ciclo nunca termina.

**Sugerencia:** `connect_timeout` en las URLs (o `connect_args`), `statement_timeout` razonable en el target al abrir la sesión (p.ej. 15-30 s), y/o un tope de duración por target.

**Resuelto en `bf13020`:** `connect_timeout=10` y `statement_timeout=30` al **arrancar la sesión**, no vía event listener — verificado empíricamente en el sandbox que el `rollback()` de collect/explain deshace un `SET` transaccional de Postgres (`5s → 0` tras rollback), así que el listener no servía. Postgres/cola: `options="-c statement_timeout=30000"`; MySQL: `init_command="SET SESSION max_execution_time=30000, time_zone='+00:00'"` + `read_timeout=40` (el `max_execution_time` no cubre EXPLAIN/USE). Builders únicos en `config/connections.py` reutilizados por `registered._engine`: una sola fuente. **Residual consciente:** no hay tope wall-clock por target (los acotes de abajo cortan cada sentencia; la alternativa cooperativa no interrumpe una en vuelo).

---

### ALTA-3 — Payload sin límite y tamaño real subestimado en los docs

**Dónde:** `collectors/*/queries.py` (ninguna tiene `LIMIT`), `models/snapshot.py:150`, `PIPELINE_FLOW.md:143`, `TESTING.md:76`

El payload incluye **todas** las secciones crudas: `pg_stat_statements` completo (default `max=5000`, la CI usa 10000), todas las filas de `information_schema.columns`, índices, tablas, `pg_locks` completo, etc.

Medido sobre los fixtures golden (que solo tienen 12-14 statements):

| Fixture | Statements | Tamaño total |
|---|---|---|
| `postgres_snapshot.json` | 12 | 49 KB |
| `mysql_snapshot.json` | 14 | 37 KB |

Los docs afirman "**~8 KB por snapshot; 10 s × N bases = ~70 MB/día/base**". Con una base real de 5000 statements el orden es **MB por snapshot** (≈ 2,5 MB solo la sección `statements` linealizando el golden), lo que implica ~20 GB/día/base, y **sin consumidor** la cola `q_analyze_job` crece sin límite. Igual: memoria del proceso (se materializa todo en `list[dict]` antes de validar).

**Sugerencia:** `LIMIT` en statements (o enviar solo candidatos + agregados), omitir `columns`/`indexes`/`tables` si el consumidor no los usa en cada vuelta (mandarlos por cambio, no cada 10 s), y recalcular la cifra de retención de PGMQ con una medida real.

**Parcial en `bf13020`:** docs corregidos — `PIPELINE_FLOW.md:143` y `TESTING.md:76` reemplazan el `~8 KB` (falso ~5×) por el rango medido 37–49 KB en 12-14 statements y la receta `SELECT count(*), pg_size_pretty(avg(pg_column_size(message)))`. Observabilidad: `main._log_payload_size` — DEBUG de bytes siempre, `WARNING` único por db_id al cruzar `PAYLOAD_WARN_BYTES` (1 MB), patrón de transición de `runner._report_state`. **No** se añadió `LIMIT`/omisión de secciones: truncar rompería la validación del consumidor y la retención de la cola es responsabilidad suya; la decisión quedó registrada en el propio `PIPELINE_FLOW.md:143`.

**Nota (ronda M-6 a M-12):** el polling de **10 s** (`runner.py:40` `interval_from_env`, fijado por `test_runner.py:52`) es el default pensado para **pruebas y sandbox**. Para producción la cadencia prevista es **30–60 s** — se deja como decisión de deploy, sin tocar el código ni el contrato.

---

### MEDIA-4 — Timestamps con zona horaria "anulados" sin convertir a UTC

**Dónde:** `stages/normalize.py:52,63`, `models/snapshot.py:13`

```python
row[field] = ts.replace(tzinfo=None).isoformat(...)
```

`replace(tzinfo=None)` no convierte: **descarta el offset**. `counters_epoch` de Postgres llega como `timestamptz` (psycopg2 lo renderiza en la TZ del cliente = UTC en el contenedor), mientras que MySQL `FIRST_SEEN` es `DATETIME` naive en la TZ del servidor. Si el servidor MySQL no está en UTC, las dos marcas de tiempo "se ven igual" pero no lo son. Esto contradice el propio comentario (`normalize.py:40-46`: "el consumidor puede compararlas entre motores sin parsear dos formatos").

**Sugerencia:** `ts.astimezone(timezone.utc).replace(tzinfo=None)` en el lado aware, y forzar/ documentar `time_zone='+00:00'` en la sesión de MySQL.

**Resuelto en `bf13020`:** helper único `_to_naive_utc` en `models/snapshot.py` (aware → UTC naive; el naive sin tocarse), aplicado en `_to_iso` (cubre `counters_epoch`, `transaction_start_time`, `last_index_scan`, `stats_reset`) y en `normalize_statement_epochs`/`normalize_active_query_timestamps`. El lado MySQL queda fijado a UTC por el `time_zone='+00:00'` del `init_command` de A-2 — las dos mitades del docstring ahora se cumplen. Tests: `normalize_statement_epochs` no tenía cobertura — ahora 2 casos, más casos de offset `-05:00` en active_queries y en `_to_iso` (`test_normalize.py`, `test_snapshot.py`).

---

### MEDIA-5 — Un error de recolección se publica como "sin datos"

**Dónde:** `stages/collect.py:20` + `models/snapshot.py:37` (`ListOrNone` convierte `None` → `[]`)

Si falla la query de `locks`, el payload llega con `locks: []`. El consumidor no puede distinguir "la base no tiene locks" de "no pude leer los locks". Combinado con A-1: el único fallo que se nota es el de `statements`, y se nota porque **no** llega nada. Además el snapshot no tiene **timestamp de captura** (ver MEDIA-11).

**Nota (ronda M-6 a M-12, sin cambio de contrato):** se verificó en el código que los fallos por permisos **sí quedan en el log**: `CollectStage` captura por sección, hace `rollback()`, deja `stats[key] = None` y emite `logger.error(f"{dialect} | collect_telemetry | query={key} | {error del driver}")` — un `permission denied` del rol monitor aparece ahí con su mensaje. Sigue sin añadirse `collect_errors` ni `captured_at` (decisión del usuario, ver MEDIA-11).

---

### MEDIA-6 — El log y el docstring anuncian un fallback que no existe

**Dónde:** `config/registered.py:12,126,133,145,151` vs `main.py:84-87`

Cinco sitios dicen `"fallback a ENGINES"` y el docstring dice "main() cae a los ENGINES fijos del sandbox". `main.py` **no hace ningún fallback**: devuelve `None` y no extrae nada (`TESTING.md:159` lo reconoce explícitamente). Quien opere el daemon leerá "fallback a ENGINES" en el log y asumirá que sí se está extrayendo del par sandbox.

**Sugerencia:** cambiar los mensajes a `"sin targets registrados | no se extrae nada"` (y el docstring), o implementar el fallback si era la intención.

**Resuelto (ronda M-6 a M-12):** el docstring del módulo y los 4 mensajes de `config/registered.py` ya dicen `"no se extrae nada"` y ninguno anuncia un fallback; el test `test_registered_targets.py` fija el texto. La **implementación de un fallback real** (e.g. hacia los ENGINES fijos del sandbox) queda **pendiente explícitamente**: el docstring de `registered.py` apunta a este hallazgo, y `main()` y `main_sandbox.py` siguen siendo rutas separadas.

---

### MEDIA-7 — Lógica de normalización duplicada (dos fuentes de verdad)

- Transformaciones hechas **dos veces**: `normalize_locks` / `normalize_blocking_pids` / `normalize_statement_epochs` (etapa) y los validadores `_to_bool` / `_to_int_list` / `_to_iso` (pydantic) hacen lo mismo con distinto código (`stages/normalize.py:57-79` vs `models/snapshot.py:11-32`). Si cambia uno y no el otro, o el snapshot viaja mal, o se rechaza.
- `_base_operation` está **copiado idéntico** en `stages/explain_normalizer.py:190` y `:337`.
- Construcción del engine MySQL duplicada: `connections.get_connection_mysql` vs `registered._engine` (mismos `pool_size/max_overflow/pre_ping`, pueden divergir; ya divergen en Postgres: el target no tiene `pre_ping`).
- `anonimize_query_text` (`canonicalizers.py:43-51`) es en la práctica un **no-op**: `select_candidates_to_explain` ya copia `query_text` con `{**statement, ...}`, así que "restaurar"lo desde `statements` escribe el mismo valor.

**Sugerencia:** que un lado sea la única fuente (misma función usada por etapa y modelo), extraer `_base_operation` compartido, y borrar o renombrar `anonimize_query_text` (ver MEDIA-10).

**Resuelto (parcial, ronda M-6 a M-12):** `_base_operation` pasó a una **función de módulo única** en `stages/explain_normalizer.py` (las dos copias idénticas estaban en las clases Postgres/MySQL); `anonimize_query_text` se **eliminó** — era un no-op confirmado (los candidatos copian `query_text` con `{**statement}`). Se quitaron sus tests (2 en `test_canonicalizers.py` + `TestQueryTextIsRestored` en `test_normalize_stage.py`), el mutante que la protegía y la mención en `PIPELINE_FLOW.md`. **Quedan abiertas** las otras dos duplicaciones: `normalize_locks`/`normalize_blocking_pids`/`normalize_statement_epochs` (etapa) vs los validadores pydantic (`_to_bool`/`_to_int_list`/`_to_iso`), y la construcción del engine (MySQL con `pool_size=1` rodada dos veces; Postgres divergiendo en `pre_ping`).

---

### MEDIA-8 — `connections.py` arma URLs con f-string; `registered.py` usa `URL.create` por exactamente el motivo que este ignora

**Dónde:** `config/connections.py:24,42,67` vs `config/registered.py:86-102`

`registered.py` comenta: *"URL.create en vez de f-string: la password viene de Fernet y puede contener '@', ':' o '/' que romperían el parseo"*. Las tres conexiones de `connections.py` (incluida la de la **cola**, la más crítica) siguen con f-string. Si `QUERYLENS_PASSWORD` o `MONITOR_*_PASSWORD` contienen caracteres especiales, la URL se parsea mal y el pipeline no puede ni arrancar/enecolar.

**Sugerencia:** migrar las tres a `URL.create` (mismo patrón ya probado por `test_registered_targets.py:70`).

**Resuelto (ronda M-6 a M-12):** helper `_make_url` único en `config/connections.py` que usa `URL.create` (cada componente viaja por separado), usado por las tres factorías y por `registered._target_url`. Test de regresión: `test_password_with_special_characters_survives_round_trip` compara `url.password` contra el valor original con `@:` `?` `/` (la f-string habría truncado al driver). MySQL conserva el `database=""` (sin default, para el `USE` del EXPLAIN).

---

### MEDIA-9 — La "inestabilidad" de MySQL no es desviación estándar, y el mismo selector significa cosas distintas por motor

**Dónde:** `collectors/mysql/collector.py:44-57` vs `collectors/postgres/queries.py:38`

- Fórmula MySQL: `stddev = (max_time - mean) / sqrt(count)` — no es una desviación estándar, es un heurístico.
- `if max_time > mean * 1000: coeff = None` descarta **justo los outliers más extremos** (una query con un pico de 1001× la media se ignora; la de 999× se marca). Si la intención es filtrar el calentamiento inicial, el corte está mal ubicado.
- Postgres usa `stddev_exec_time` real. `select_unstable_statements` aplica el mismo umbral (`coeff > 2`) a dos métricas con semánticas distintas.

**Sugerencia:** documentar la fórmula como aproximación en el propio código, alinear los umbrales por motor o usar percentiles (p95/p50) que ambos motores exponen de forma homogénea.

**Resuelto como documentación (ronda M-6 a M-12):** la fórmula se documenta como **heurística, no medida** en `collectors/mysql/collector.py` (docstring de `calculate_stddev_coeff`, incluido el guard `max > mean*1000` como descarte de outliers por orden de magnitud) y en `references/004_decisiones_contrato.md` (sección nueva `stddev_time_ms / coeff_of_variation`). **No se cambiaron valores ni umbrales** (decisión con el usuario); la parte de "el mismo umbral `coeff > 2` para semánticas distintas" sigue documentada como está.

---

### MEDIA-10 — Candidatos sin límite, y "anonimización" que no anonimiza

- `selectors.py:9,13`: `unstable_statements` y `disk_spill_statements` no tienen top-N (solo `high_impact` corta a 10). Una base con miles de queries inestables genera miles de `EXPLAIN` por ciclo, sobre la misma conexión, sin timeout (ver A-2).
- Privacidad: `canonicalizers.anonimize_query_text` no anonimiza nada — el payload lleva `query_text` crudo con literales reales (Postgres) y `QUERY_SAMPLE_TEXT` con **valores concretos** (MySQL), y `canonicalize_query` degrada a `" ".join(query_text.split())` en cualquier fallo de parseo (`canonicalizers.py:27-28`), dejando el literal tal cual. Si el producto habla de anonimización, el nombre miente; si no, al menos renombrar la función y documentar que la cola contiene texto de consultas con datos reales (GDPR/retención).

  **Precisión (fix M-10) + revertida (decisión de privacidad):** al momento de redactar este hallazgo, la afirmación MySQL era **incorrecta**: `StatementRow` no declaraba `query_sample_text` y `ConfigDict(extra="ignore")` lo descartaba en la validación, así que el sample **no viajaba** en el JSON encolado (verificado releyendo mensajes reales de la cola). El fix M-10 lo **declaró** (`str | None`) y el payload empezó a llevarlo con literales reales. Eso fue un **error, no una corrección**: el requerimiento del proyecto es no conservar información sensible o real sobre las queries, y el sample hacía exactamente eso fuera del contrato. **Revertido:** `query_sample_text` volvió a ser solo flujo interno (lo usan `mark_explainable` y el `EXPLAIN FORMAT=JSON` de MySQL en memoria); no es campo de `StatementRow`, no sale de la cola (solo viajan los textos normalizados con placeholders) y al escribir los goldens se descarta de cada fila (`ci/regenerate_goldens.py`). Documentado como decisión en `references/004_decisiones_contrato.md`. La otra mitad de MEDIA-10 — la parte "top-N" (`selectors.py:9,13` — `unstable_statements` y `disk_spill_statements` sin límite) — **se mantiene sin top-N por decisión del usuario (ronda M-6 a M-12):** por simplicidad y porque en la práctica la mayoría de esas queries no terminan seleccionadas para `EXPLAIN` (el dedupe por `query_id` en `select_candidates_to_explain` las filtra); si algún día el volumen lo exige, se agrega el corte acotado de la misma forma que `high_impact`.

---

### MEDIA-11 — Contrato del consumidor: sin timestamp de captura y sin dialecto

**Dónde:** `models/snapshot.py:146-159`, `stages/enrich.py:3`, nota en `PIPELINE_FLOW.md:184`

- No hay campo de fecha/hora del snapshot: con una cola que puede crecer días, el consumidor no sabe cuándo se capturó (solo, si acaso, por `msg_id`/`enqueued_at` de PGMQ).
- El dialecto se eliminó del payload a propósito, y en el sandbox **ambos motores** encolan con `db_id = "querylens-db-01"` (`enrich.py:3`), así que la única forma de distinguir el motor es por el tipo de `query_id` (int vs hex). Es una inferencia frágil para un contrato.

**Sugerencia:** `captured_at` (UTC) y `source_dialect` en el payload; son dos campos que no rompen "unicidad del evento".

---

### MEDIA-12 — Transacción abierta sobre el target durante todo el ciclo

**Dónde:** `orchestrator.py:20` (`with engine.connect()` envuelve collect + explain)

En Postgres, collect + candidatos + N `EXPLAIN` corren dentro de **una sola transacción de solo lectura** que se abre cada 10 s: mantiene activo un snapshot que puede retrasar el vacuum (horizonte de `xmin`) en bases con escritura intensiva. En MySQL (REPEATABLE READ, autocommit off) retiene historial de filas. Además, cualquier `rollback` por error en `ExplainStage` descarta también el `SET LOCAL search_path` del resto de la iteración (se re-emas, pero solo si la iteración sobrevive).

**Sugerencia:** commit tras el collect (o `SET TRANSACTION READ ONLY` + tiempo acotado), y en Postgres usar `SET LOCAL` dentro de la transacción del explain únicamente.

**Resuelto (ronda M-6 a M-12):** `orchestrator.py` hace `conn.commit()` **justo tras el collect** (la transacción de lectura del target se cierra ya: no retiene snapshot/vacuum en Postgres ni undo/MVCC en MySQL durante el EXPLAIN y el encolado). `ExplainStage` envuelve contexto + EXPLAIN en **`with active.begin()` por candidato** (`BEGIN → SET LOCAL/USE → EXPLAIN → COMMIT`; `rollback` en fallo). En Postgres el `SET LOCAL` queda confinado a esa transacción (se deshace al commit); en MySQL el `USE` es estado de sesión y sobrevive al commit — mismo contrato de antes, sin la fuga de search_path al siguiente candidato. Tests: `test_collect_commits_the_read_transaction_before_explain` (orden commit-vs-EXPLAIN), `test_postgres_commits_the_short_transaction_per_candidate`, `test_postgres_explain_error_rolls_back_the_short_transaction`. Mutantes `orchestrator: no commit tras collect` y `explain: sin transaccion corta por candidato` añadidos y muertos.

---

### MEDIA-13 — MySQL: el `USE` no se resetea si `schema_name` viene vacío

**Dónde:** `stages/explain.py:31-37`

Postgres emite `SET LOCAL search_path TO DEFAULT` cuando no hay schema; MySQL devuelve `None` y **no emite nada**, dejando el `USE` del candidato anterior (el `USE` es estado de sesión y sobrevive a `rollback`). Si algún candidato llega sin `schema_name`, su `EXPLAIN` corre contra el esquema anterior. Hoy es de baja probabilidad (MySQL siempre trae `schema_name` del performance_schema), pero es una asimetría sin propósito.

**Resuelto (fix de alcance por servidor):** en MySQL un candidato sin contexto de schema (`schema_name` nulo **y** `database_name` nulo) se **salta con warning**, en vez de heredar el `USE` de un candidato anterior que armaría el plan contra el schema equivocado. Si tiene `database_name` pero no `schema_name`, se usa ese como contexto del `USE`. Cambió el test `test_mysql_skips_use_when_no_schema` (ahora el EXPLAIN ni siquiera se emite) y se añadió el de fallback por `database_name`.

---

### BAJAS

| # | Dónde | Hallazgo |
|---|---|---|
| B-1 | `querylens_database/init.sql:2` | `CREATE ROLE postgres WITH SUPERUSER LOGIN` crea un superusuario extra sin password; el comentario habla de `pg_partman`, que no se usa en el repo. Si `POSTGRES_USER=postgres`, la sentencia falla y depende de que `psql` continúe tras el error. |
| B-2 | `querylens_database/registered_databases.sql:28` | `database_identifier VARCHAR(32) UNIQUE` (corrección al informe previo: es `VARCHAR(32)`, no `VARCHAR(23)`) ya crea un índice y además se crea `idx_registered_databases_identifier`: índice duplicado (escritura/almacenamiento gratis cada INSERT/UPDATE). |
| B-3 | `querylens_database/*` + `docker-compose.yml:15-16` | Los DDL van en `docker-entrypoint-initdb.d`, solo corren en el **primer** arranque del volumen: cualquier cambio posterior requiere migración manual (hoy no hay tooling). Orden accidental: `02_registered_databases.sql` se ejecuta antes que `init.sql` (`'0' < 'i'`); funciona porque la tabla no depende de pgmq. |
| B-4 | `config/registered.py:138` | `with get_connection_querylens_db().connect()` crea un **engine nuevo por ciclo (cada 10 s)** y nunca lo dispone. SQLAlchemy lo cierra por GC, pero es inconsistente con el `dispose()` cuidadoso de `run_engine` y genera churn de conexiones a la cola. |
| B-5 | `main.py:36-37` | `target_engine` se crea **antes** del `try`: si `get_connection_querylens_db()` lanzara, ese engine no se dispone (hoy `create_engine` casi no lanza, pero es la misma estructura que A-8). |
| B-6 | `config/registered.py:157` + `logger.py:57` | **Resuelto-implícito (Q1, `b890bdf`):** con `QL_LOG_LEVEL=DEBUG` el `logger.debug("N base(s) activa(s)")` sí se emite (`_level_from_env` fija el nivel por proceso); con el default INFO no aparece, que era la intención del comentario. Matiz abierto: `runner.py:34` sigue fijando DEBUG a su propio logger sin consultar el env (N-3 de la ronda 2). |
| B-7 | `main.py:63` | `print(...)` en el camino caliente en vez del logger (los docs lo enuncian como diseño, pero en contenedor ensucia stdout y no tiene timestamp/nivel). |
| B-8 | `requirements.txt` | 7 dependencias **sin versión fijada** (`sqlalchemy`, `pydantic`, `cryptography`...): builds no reproducibles; un release mayor de pydantic/sqlalchemy puede romper la validación en producción sin tocar código. |
| B-9 | `docker-compose.yml:11,38` | Puertos publicados en todas las interfaces (`0.0.0.0`): Postgres (5432) y auth (8000). Para despliegue, bindear a `127.0.0.1:puerto`. |
| B-10 | `docker-compose.yml:26-29,69-72` | `auth-service` y `pipeline` comparten el mismo rol (`QUERYLENS_USER`) y la misma clave: ese rol es a la vez dueño de la cola **y** lector de `encrypted_password`. Menor privilegio: rol separado para el pipeline con `SELECT` solo sobre `registered_databases` + `pgmq.send`. |
| B-11 | `stages/normalize.py:83` | `explain.get("canonical_plan", []).get(...)` — si faltara la clave, `.get` se llamaría sobre una `list` (hoy inalcanzable: el normalizador siempre la escribe; usar `{}` o `.get(..., {})` como default). |
| B-12 | `stages/explain.py:53` | `is_single_statement` descarta cualquier query con `;` dentro de un literal o comentario (falso "multi-statement"): se pierden EXPLAINs válidos de forma silenciosa (solo warning). |

---

## Riesgo de seguridad del `EXPLAIN` interpolado — evaluado, bajo

`stages/explain.py:71,79` concatenan el texto de la query dentro de `EXPLAIN ... {query_text}`. Lo revisé con criterio de ataque y concluyo que **no es explotable en la práctica hoy**:

- El candidato se filtra por prefijo (`SELECT/WITH/INSERT/UPDATE/DELETE`, `selectors.py:26-44`) y `is_single_statement` bloquea multi-sentencia (el driver de Postgres ejecutaría varias sentencias en un solo `execute`).
- `EXPLAIN` sin `ANALYZE` no ejecuta la query, y los utility statements destructivos (`DROP`, `TRUNCATE`, `CREATE`) quedan fuera del prefijo permitido.
- Los interpolantes de schema sí van parametrizados/cotejados (`identifier_preparer.quote` en `explain.py:27,35`).

Defense-in-depth que añadiría: aplicar el mismo filtro de prefijo **al texto que realmente se explica** (hoy el filtro corre sobre `query_text`/DIGEST_TEXT, pero MySQL explica `query_sample_text`, que no se valida más allá de `;`), y `SET statement_timeout` antes de los EXPLAIN.

---

## Lo que está bien (verificado, sin hallazgos)

- **Suite:** 466 tests unit/contract pasan en ~2,4 s; hay mutation testing (`ci/mutants.py`), golden masters por motor y tests de arquitectura que bloquean regresiones de patrón. La CI (`.github/workflows/pipeline.yml`) replica fielmente lo que `TESTING.md` documenta.
- **Ciclo de vida:** `dispose()` en `finally` de los dos engines, backoff con anclaje al inicio del ciclo, señales (incluida `SIGBREAK` para Windows), rotación de logs con degradación a stderr si el directorio no es escribible — todo testeado.
- **Seguridad básica:** `.env` y `venv/` ignorados y **sin historia de secretos en git** (133 archivos trackeados, ninguno con `.env`); contraseñas de targets cifradas con Fernet y manejadas con `URL.create` (probado con passwords que contienen `@` y `:`); `SELECT` de la cola parametrizado (`enqueue.py`); `sqlalchemy.text()` en todas las queries dinámicas.
- **Autoobservación:** Postgres excluye el rol monitor por `userid`; MySQL filtra su propia huella (`PIPELINE_FINGERPRINT`) con test de contrato — sin esto el pipeline se explicaría a sí mismo.
- **Aislamiento:** una base que no conecta no tumba a las demás (`run_targets`), una fila indecifrable no tumba a las demás (`_build_target`), un snapshot inválido no se encola.
- **DDL:** `connection_fingerprint UNIQUE` evita duplicados aunque los campos estén cifrados; `is_active` filtrado en SQL (con test que lo atrapa).
- **Docs:** `PIPELINE_FLOW.md` y `TESTING.md` están notoriamente al día con el código y declaran sus propias limitaciones (cola sin consumidor, ausencia de gate, ventana de medición corta). Las únicas divergencias que encontré son las citadas en MEDIA-6 y A-3.

---

## Prioridad sugerida

1. **M-6** (log/docstring anuncian un fallback que no existe) — engaña al operador sobre si hay extracción.
2. **M-5** (error de recolección se publica como "sin datos") — el consumidor no distinguirá "vacío" de "no leí"; atado a decidir `captured_at`/`collect_errors`.
3. **A-3 restante** (LIMIT o envío por cambio de `columns`/`indexes`/`tables`) — antes de que haya consumidor y cola con días de retención.
4. **M-8, M-7** (f-strings en URLs; normalización duplicada en dos fuentes de verdad).
5. **M-11, M-12, M-13** (contrato del consumidor; transacción abierta sobre el target; `USE` sin reset).
6. Lo demás (M-9, M-10, B-1 a B-12, defensa en profundidad del `EXPLAIN`), según avance el proyecto (B-10/B-9 son de despliegue, no de desarrollo).

---

## Plan de acción consolidado (verificación 2026-10-08)

### Contexto clave: dos entry points, un solo pipeline
| Entry point | Qué es | `db_id` | Intervalo | Destino |
|-------------|--------|---------|-----------|---------|
| `main_sandbox.py` | Sandbox / CI / `measure_overhead` | Hardcodeado `"querylens-db-01"` | N/A (una corrida) | **No va a producción** |
| `main.py` | Producción | `database_identifier` de `registered_databases` (pisa constante ANTES de validar) | N/A (una corrida) | Llamado por `runner.py` |
| `runner.py` | Daemon producción | Hereda de `main.py` | `EXTRACT_INTERVAL_S` (default 10s = **pruebas**; prod 30-60s via env) | **Proceso que se despliega** |

**El consumidor de `q_analyze_job` NO está en este repo** — es un servicio externo separado (los tests simulan consumo con `pgmq.read`, ver `test_e2e_main.py:172`). El pipeline solo produce.

---

### CAJA 1 — CRÍTICOS: Rompen funcionalidad, seguridad, pérdida de datos HOY

| # | Hallazgo | Archivo:línea | Acción concreta | Estado |
|---|----------|---------------|-----------------|--------|
| **C-1** | `statements=None` crashea `selectors.py:5` y `mysql/collector.py:49` | `stages/selectors.py:5`, `collectors/mysql/collector.py:49` | `or []` en `select_high_impact_time_statements`, `select_unstable_statements`, `select_disk_spill_indicator`, `calculate_stddev_coeff` | ✅ **RESUELTO** |
| **C-2** | Excepción genérica en `load_registered_targets` disfraza fallos reales como "sin bases" | `config/registered.py:149-155` | Diferenciar `ProgrammingError` (tabla no existe) vs `OperationalError` (falla real) | ✅ **RESUELTO** |
| **C-3** | `canonicalize_query` fallback devuelve texto crudo con literales | `stages/canonicalizers.py:27-28` | **Inválido** — código ya devuelve `"Not available"` | 🚫 **INVÁLIDO** |
| **C-4** | Engine de cola recreado cada 10s sin `dispose()` | `config/registered.py:147` | Cachear engine singleton (`_get_queue_engine` + `_reset_queue_engine`) | ✅ **RESUELTO** |
| **C-5** | `target_engine` creado antes del `try` — leak si falla cola | `main.py:58-64` | Mover creación dentro del `try` | ✅ **RESUELTO** |
| **C-6** | `LOCKS_QUERY` no filtra locks del propio pipeline (MySQL y PG) | `collectors/mysql/queries.py:115-126`, `collectors/postgres/queries.py:49-59` | Añadir filtro por usuario/rol via join a `pg_stat_activity` / `performance_schema.threads` | ✅ **RESUELTO** |
| **C-7** | `is_single_statement` falso positivo con `;` en literal/comentario | `stages/explain.py:14-16` | Máquina de estados que ignora `;` en comillas simples/dobles/dollar-quoted | ✅ **RESUELTO** |

---

### CAJA 2 — IMPORTANTES: Robustez, observabilidad, deuda técnica

| # | Hallazgo | Archivo:línea | Acción |
|---|----------|---------------|--------|
| **I-1** | Normalización duplicada: etapa vs validadores pydantic | `stages/normalize.py:59-82` vs `models/snapshot.py:25-46` | Unificar: que etapa use validadores pydantic (o viceversa). Una sola fuente de verdad. |
| **I-2** | Engine MySQL construido 2 veces; PG diverge en `pool_pre_ping` | `config/connections.py:115-122` vs `config/registered.py:113-122` | Unificar builders: una sola factory por motor, reutilizada por `connections.py` y `registered.py`. |
| **I-3** | `runner.py:34` fija `DEBUG` hardcodeado, ignora `QL_LOG_LEVEL` | `runner.py:34` | Leer `QL_LOG_LEVEL` via `logger._level_from_env()` en lugar de `logging.DEBUG` hardcodeado. |
| **I-4** | `load_dotenv(override=True)` en import de módulo | `config/connections.py:128` | Quitar `override=True`; cargar `.env` solo en entrypoints (`main.py`, `runner.py`, `main_sandbox.py`). |
| **I-5** | `.get` inseguro en `normalize_predicate` | `stages/normalize.py:85` | Cambiar `explain.get("canonical_plan", [])` por `explain.get("canonical_plan", {})`. |
| **I-6** | Deps sin pin en `requirements.txt` (7 deps) | `telemetry_pipeline/requirements.txt:1-7` | Pinnear versiones mínimas compatibles (ej. `sqlalchemy>=2.0,<3.0`, `pydantic>=2.0,<3.0`, etc.). |
| **I-7** | Puertos publicados en `0.0.0.0` | `docker-compose.yml:12,44` | Cambiar a `127.0.0.1:5432` y `127.0.0.1:8000` (o `${DB_HOST:-127.0.0.1}`). |
| **I-8** | Rol compartido auth-service/pipeline | `docker-compose.yml:26-29,69-72` | Crear rol `querylens_pipeline` con `SELECT` en `registered_databases` + `pgmq.send`; separar credenciales. |
| **I-9** | `print()` en camino caliente | `main.py:86` | Cambiar a `logger.info(f"Diccionario encolado con ID: {msg_id}")`. |
| **I-10** | Superusuario `postgres` innecesario en `init.sql` | `querylens_database/init.sql:2` | Eliminar `CREATE ROLE postgres WITH SUPERUSER LOGIN`; `pg_partman` no se usa. |
| **I-11** | Índice duplicado en `registered_databases` | `querylens_database/registered_databases.sql:6,28` | Eliminar `CREATE INDEX idx_registered_databases_identifier` (ya existe por `UNIQUE`). |
| **I-12** | DDL solo en primer arranque, sin migraciones | `docker-compose.yml:15-16` | Documentar que cambios de DDL requieren migración manual; evaluar herramienta (alembic, golang-migrate, SQL puro versionado). |
| **I-13** | Defensa EXPLAIN: sin filtro sobre `query_sample_text` (MySQL) | `stages/explain.py:97-101` | Aplicar filtro de prefijo (`SELECT/WITH/INSERT/UPDATE/DELETE`) también a `query_sample_text` antes de EXPLAIN. |

---

### CAJA 3 — DECISIONES DOCUMENTADAS (by design, no tocar)

| # | Tema | Documentación | Nota |
|---|------|---------------|------|
| **D-1** | Cola sin consumidor → crece sin límite | `PIPELINE_FLOW.md:153`, `references/004_decisiones_contrato.md:100` | Retención = consumidor. `_log_payload_size` avisa a 1MB. |
| **D-2** | Intervalo 10s default (pruebas) | `PIPELINE_FLOW.md:202`, `runner.py:36` | Prod = 30-60s via `EXTRACT_INTERVAL_S` env. |
| **D-3** | Sin `LIMIT` en queries de recolección | `PIPELINE_FLOW.md:153` | Truncar rompería validación consumidor. |
| **D-4** | Sin `captured_at`/`source_dialect`/`collect_errors` | `references/004_decisiones_contrato.md` | Decisión usuario: "snapshot vacío no sirve"; log distingue fallo vs vacío. |
| **D-5** | EXPLAIN con `search_path` reducido | `references/004_decisiones_contrato.md:46-54` | Limitación conocida: refs no calificadas pueden fallar. Reintenta cada ciclo. |
| **D-6** | `db_id` hardcodeado en sandbox | `stages/enrich.py:3`, `main_sandbox.py:22` | **A propósito**: sandbox usa constante; prod la pisa con `database_identifier` real. |

---

### CAJA 4 — FALSOS POSITIVOS (review original se equivocó)

| # | Hallazgo original | Por qué NO es problema |
|---|-------------------|------------------------|
| **FP-1** | MEDIA-11: "sin `source_dialect` ni `captured_at`" | Sandbox hardcodea `db_id` a propósito; prod usa `database_identifier` real. Consumidor agnóstico al motor. |
| **FP-2** | B-6: "`QL_LOG_LEVEL` no cableado" | Resuelto en `b890bdf`/`041b7e2`: compose tiene `QL_LOG_LEVEL`, `logger.py:_level_from_env()` lo lee. Solo `runner.py` lo ignora (I-3). |
| **FP-3** | Mutante `selectors: no inyectar ready_for_explain` en INVÁLIDOS | Corregido en `9027b2f`: 42/42 mutantes muertos en `normalize`, 22/22 en `explain`. |
| **FP-4** | MEDIA-10: "`query_sample_text` viaja en payload" | **Falso**: `StatementRow` no lo declara; `extra="ignore"` lo descarta. Test `test_payload_does_not_carry_query_sample_text` lo verifica. |

---

### Orden de ejecución sugerido

1. ~~**C-1 a C-7** (funcionalidad/seguridad crítica — 1-2 días)~~ ✅ **COMPLETADO**
2. **I-1, I-2, I-3, I-4, I-6** (robustez core — 1 día)
3. **I-5, I-7, I-8, I-9, I-10, I-11, I-12, I-13** (despliegue/calidad — según sprint)
4. **CAJA 3 y 4** — no requieren acción (documentadas / falsas)
