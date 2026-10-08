# Decisiones de contrato (snapshot)

Decisiones de diseño del contrato de datos que emite el pipeline, validadas contra capturas reales de ambos motores.

## query_id: identidad del statement

- `query_id` identifica el statement dentro de su motor, no una ejecución en curso. Antes (commit `c730da1`) la explicación cruzaba `statements.query_id` contra `active_queries.query_id`, de modo que un candidato solo se explicaba si su query se estaba ejecutando en el momento del snapshot. Ese cruce ya **no aplica**: desde `93beb5e` el plan lo produce el propio motor sin necesitar la query en vivo.
- Cada motor resuelve el plan por su vía nativa, y el hook `mark_explainable` decide:
  - Postgres: `EXPLAIN (GENERIC_PLAN)` (PG16+) sobre `pg_stat_statements.query`, que ya viene con placeholders `$1`. Marca **todos** los candidatos como listos.
  - MySQL: `DIGEST_TEXT` trae `?`, que da error de sintaxis en `EXPLAIN`; se usa `QUERY_SAMPLE_TEXT`, que trae literales reales. Marca solo lo que tenga `query_sample_text` no vacío.
- Verificado en capturas reales (golden): MySQL `{schema}/{digest}` → string (p. ej. `ql_demo/864c221061d4…`, ver sección *alcance por base*); Postgres `queryid` → bigint (p. ej. `-1859038224550094023`). Ambos aparecen en `statements` y en `active_queries` al mismo tiempo, pero esa coincidencia ya no condiciona nada.
- Modelo: `QueryId = Union[str, int, None]`.
- Lo que queda de `init_ready_for_explain` (antes `select_explain_ready`, renombrado porque el nombre original prometía una decisión que no tomaba) es solo inicializar `ready_for_explain=False` y deduplicar por `query_id`; la decisión real la sobrescribe `mark_explainable` justo después, en el mismo `CandidatesStage`.

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
- Limitación: para un rol con varias schemas reales, Postgres explora bajo la primera del `search_path`.
- MySQL: si el candidato trae `schema_name` se usa ese; si no, `database_name`; y si **ninguno** viene, el candidato se **salta con warning** — heredar el `USE` de un candidato anterior armaría el plan contra el schema equivocado (este par de reglas reemplaza la limitación vieja de "no hay reset a sin database" y cierra MEDIA-13).

## Alcance por base: todo el servidor entra, atribuido por fila

- **Antes:** las queries de recolección leen el **servidor completo** (en Postgres `pg_stat_statements` trae filas de todas las bases y `pg_locks`/`pg_stat_activity` son cluster-wide; en MySQL los digests cubren todos los schemas), pero el snapshot se publica bajo un solo `db_id`. No había forma de saber de qué base venía cada fila.
- **Decisión (con el usuario):** no filtrar por base ("que entre todo"), **atribuir por fila**. `database_name` (`str | None`) es campo **declarado** del contrato en `StatementRow`, `LockRow` y `ActiveQueryRow`.
  - Postgres: `pg_stat_statements.dbid → pg_database.datname`, `pg_locks.database → pg_database.datname` (NULL en advisory/txnid locks), `pg_stat_activity.datname`.
  - MySQL: base == schema, sin distinción de niveles: `SCHEMA_NAME` del digest y `object_schema` de `data_locks`.
- **query_id compuesto en MySQL:** la PK real de `events_statements_summary_by_digest` es `(SCHEMA_NAME, DIGEST)` — el mismo digest en dos schemas son **dos filas distintas**. `query_id` copiaba solo el digest y el dedup por `query_id` de `init_ready_for_explain` tiraba una de cada par. Ahora `query_id = CONCAT(schema_name, '/', DIGEST)` (la `/` no puede confundirse: los nombres de schema MySQL son nombres de directorio). Con `schema_name` NULL el `query_id` queda NULL (catch-all sin atribuir).
- **EXPLAIN de un candidato de otra base (Postgres):** una conexión de Postgres no puede cambiar de base con `USE`; el plan solo se arma en el contexto real de la base. `ExplainStage` abre una **conexión dedicada por base** reutilizando las credenciales del target (`engine.url.set(database=db)`, `NullPool`, `postgres_connect_args`), cacheada por base durante el ciclo y con `dispose()` en `finally`. MySQL explica cualquier schema con `USE` en la misma conexión.
- **Limitaciones (documentadas):** la resolución de schema de un candidato extranjero corre contra el catálogo **de la base conectada** (el answer es correcto para la base conectada; no se cambia de catálogo por candidato). `EXPLAIN` sobre otra base requiere que el rol monitor tenga privilegios ahí (en el sandbox `ql_user` es superusuario). Las queries de telemetría se ejecutan contra el servidor completo: el volumen depende de cuántas bases tenga el servidor.
- Verificado en vivo con una base scratch (PG): un statement ejecutado en la 2ª base aparece en `statements` con `database_name` propio y su `EXPLAIN` corre contra esa base. Fijado por contratos de queries, unit de ruteo de `ExplainStage` y tests de snapshot sobre los goldens regenerados.

## query_sample_text: flujo interno, se descarta del contrato (privacidad)

- `QUERY_SAMPLE_TEXT` de MySQL (`collectors/mysql/queries.py`) trae la consulta **con literales reales**; vive solo en `stats` en memoria: lo usa `mark_explainable` (marca candidato como explicable solo si hay sample) y el `EXPLAIN FORMAT=JSON <sample>` de MySQL. Postgres no produce la columna.
- **No es campo del contrato.** Un `query_sample_text` en el payload **es un bug, no una feature**: el requerimiento del proyecto es no conservar información sensible o real sobre las queries, y el fix de M-10 que lo declaró en `StatementRow` y lo hizo viajar en el JSON encolado fue un error — se revirtió. `ConfigDict(extra="ignore")` lo descarta al validar (`models/snapshot.py`), y es la reja que lo deja fuera: la cola solo ve textos normalizados (`query_text` de Postgres con `$1`, `DIGEST_TEXT` de MySQL con `?`).
- Los goldens se escriben sin la clave (`ci/regenerate_goldens.py` descarta `query_sample_text` de cada fila al volcar), para que el repo tampoco conserve los literales de la batería.
- Todo lo demás que este campo significó se mantiene: `explain_source="sample"` sigue avisando que el plan se armó sobre el texto real de una muestra, y `QUERY_SAMPLE_TEXT` se trunca a `performance_schema_max_digest_text_length` (1024 por defecto) — un texto cortado puede quedar con SQL inválido y no explicarse (no es un fallo de contrato).
- **Privacidad (decisión documentada, ajusta el MEDIA-10 de CODE_REVIEW.md):** la cola PGMQ **no** contiene literales de datos reales: `query_text` (Postgres, normalizado con `$1`) y `DIGEST_TEXT` (MySQL, con `?`) son textos con placeholders; el `QUERY_SAMPLE_TEXT` con valores reales no sale de la memoria del ciclo. Sigue aplicando el GDPR/retención del propio `query_text`. La función `anonimize_query_text` — que "prometía" anonimizar — se **eliminó**: era un no-op (ver MEDIA-7).

## stddev_time_ms / coeff_of_variation: estimación, no medida (M-9)

- MySQL no expone la desviación estándar de la latencia por digest; `calculate_stddev_coeff` (`collectors/mysql/collector.py`) la **estima** con la regla heurística `stddev ≈ (max - mean) / sqrt(count)`, un spread plausible si el máximo se dio en los extremos de la distribución. No debe leerse como desviación estadística real.
- El guard `max_time > mean * 1000` descarta outliers por orden de magnitud (una ejecución aislada lenta): en ese caso `stddev_time_ms` y `coeff_of_variation` quedan en `None` en lugar de ensuciar el ranking.
- **Contrato afectado:** consume el ranking de impacto y queda expuesto en el snapshot; documentado como decisión (M-9 de CODE_REVIEW.md), no se cambian los valores.

## Transacción del target: commit tras collect + EXPLAIN en tx corta (M-12)

- El ciclo no mantiene una transacción abierta sobre el target: `Orchestrator` hace `conn.commit()` **justo tras el collect** (la parte de solo-lectura de telemetría). Antes el `with engine.connect()` de `orchestrator.py` abría una transacción que abarcaba collect + N explíca + encolado: en Postgres retenía un snapshot durante todo el ciclo (horizonte de `xmin`, retrasa vacuum) y en MySQL acumulaba undo/MVCC.
- `ExplainStage` corre cada candidato en una **transacción corta propia**: `BEGIN → SET LOCAL search_path / USE → EXPLAIN → COMMIT` (rollback en fallo). Es lo que vuelve funcional al `SET LOCAL` de Postgres — confinado a la tx del candidato, se deshace al commit — y deja el `USE` de MySQL intacto (estado de sesión, sobrevive al commit).
- No cambia el contrato del snapshot ni el de la cola: es un detalle de la sesión de recolección. Fijado por los tests de `test_explain_stage.py` (commit/rollback por candidato) y `test_orchestrator.py` (commit del orquestador antes del primer EXPLAIN).

## db_id: la fila registrada manda sobre la constante

- `db_id` nació como constante de código: `EnrichStage` escribía `DB_ID = "querylens-db-01"` en el `stats`, pensado para el par fijo del sandbox. Con el flujo de credenciales, `run_engine(dialect, factory, db_id)` **pisa ese valor con el `database_identifier` de la fila de `registered_databases`** (formato `db_<8hex>`), y lo pisa **antes** de `SnapshotPayload.from_snapshot`: la sustitución entra en la validación y viaja dentro del contrato, no se inyecta después.
- `db_id=None` sigue siendo contrato válido y respeta la constante: es lo que usan `main_sandbox.py`, los tests y el job de CI. No se tocó `EnrichStage`, se sobreescribe encima.
- La identidad del snapshot es lo **único** que sale de la fila hacia el payload. Host, puerto, usuario y password (Fernet) se usan únicamente para abrir la conexión: no hay credenciales, hosts ni `is_active` en el JSON encolado.
- Fijado por `test_run_engine_usa_el_identifier_de_la_fila` (y la constante por `querylens-db-01` en la misma prueba).

## counters_epoch: por statement, no sirve de gate

- Campo por statement (`str | None`, con `BeforeValidator` que normaliza a ISO) con el momento en que arrancó la ventana de contadores: Postgres `stats_since AS counters_epoch` (`collectors/postgres/queries.py`), MySQL `FIRST_SEEN AS counters_epoch` (`collectors/mysql/queries.py`); la normalización a ISO/UTC es común en `stages/normalize.py` (`EPOCH_FIELDS = ("counters_epoch", "minmax_epoch")`).
- **Decisión: no se usa como gate de encolado.** `stats_since`/`FIRST_SEEN` casi no cambian (solo en reset, evict o restart de las stats), mientras los contadores cambian en cada snapshot: usarlo como "ya lo leí" dejaría silenciosas a las bases cuyos contadores se reinician. Por eso se encola siempre y la retención de mensajes ya leídos queda del lado del consumidor.
- Volumen consecuencia de esa decisión: ~8 KB por snapshot ⇒ 8 640 snapshots/día/base a 10 s ≈ 70 MB/día/base en PGMQ.

## index_size_bytes: nivel tabla en MySQL

- `index_size_bytes` se resuelve por índice (`st.index_length` en MySQL). El campo no significa "tamaño de ESTE índice": `index_length` de `information_schema.tables` agrega el almacenamiento de **todos** los índices de la tabla, así que la misma tabla repite el mismo valor en cada fila de su índice (verificado en vivo: PRIMARY y k_1 ambas reportan 212992). No existe fuente directa para el tamaño por índice.
- Documentado como limitación: para comparar tamaños se recomienda usar `index_size_bytes` + `table_name` y deduplicar por tabla, o sentirse advertido de que el valor no es comparación entre índices.

## server_start_timestamp: momento de arranque del servidor

- Renombrado desde `stats_reset_timestamp`/`stats_reset` (ver CODE_REVIEW.md, hallazgo N-2): el valor NO es un reset de contadores. Postgres entrega `pg_postmaster_start_time()` y MySQL `now() - Uptime` — el instante en que arrancó el servidor de base de datos.
- Sigue siendo `str | None` normalizado a ISO/UTC sin offset por `_to_iso`. Se conserva el shape de lista (una fila con un campo) para no tocar el contrato encolado más allá del nombre.

## Validación antes de encolar

- El contrato se valida en la **frontera de emisión**, no al consumir: `SnapshotPayload.from_snapshot(payload)` y solo si pasa va `to_json()` → `pgmq.send`. Un payload inválido se traduce en `return None` + `logger.error(... snapshot_validation ...)` y **no llega a la cola** (no se encola basura para que la arregle otro).
- Como el fallo no lanza, `run_targets()` sigue con el resto de las filas: la validación es por target, igual que la conexión.
- Fijado por `test_run_engine_rechaza_snapshot_invalido_sin_encolar`.
