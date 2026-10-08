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
| B-2 | `querylens_database/registered_databases.sql:28` | `database_identifier VARCHAR(23) UNIQUE` ya crea un índice y además se crea `idx_registered_databases_identifier`: índice duplicado (escritura/almacenamiento gratis cada INSERT/UPDATE). |
| B-3 | `querylens_database/*` + `docker-compose.yml:15-16` | Los DDL van en `docker-entrypoint-initdb.d`, solo corren en el **primer** arranque del volumen: cualquier cambio posterior requiere migración manual (hoy no hay tooling). Orden accidental: `02_registered_databases.sql` se ejecuta antes que `init.sql` (`'0' < 'i'`); funciona porque la tabla no depende de pgmq. |
| B-4 | `config/registered.py:138` | `with get_connection_querylens_db().connect()` crea un **engine nuevo por ciclo (cada 10 s)** y nunca lo dispone. SQLAlchemy lo cierra por GC, pero es inconsistente con el `dispose()` cuidadoso de `run_engine` y genera churn de conexiones a la cola. |
| B-5 | `main.py:36-37` | `target_engine` se crea **antes** del `try`: si `get_connection_querylens_db()` lanzara, ese engine no se dispone (hoy `create_engine` casi no lanza, pero es la misma estructura que A-8). |
| B-6 | `config/registered.py:157` + `logger.py:57` | El `logger.debug("N base(s) activa(s)")` jamás se emite: `get_logger` fija INFO en todos los módulos y solo `runner.py:34` baja a DEBUG **su propio** logger. El comentario justifica "porque es DEBUG y runner lo repite cada 10 s", pero no aparece nunca. |
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
