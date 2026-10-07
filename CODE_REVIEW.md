# Revisión de código — `telemetry_pipeline` y `querylens_database`

**Fecha:** 2026-10-07
**Alcance:** `telemetry_pipeline/**` (código, tests, docs, Dockerfile/CI), `querylens_database/*.sql` y su integración en `docker-compose.yml`. Fuera de alcance: `auth_service/`, `frontend/`, `ql_sandbox/`.
**Método:** lectura completa de los módulos del pipeline, verificación de afirmaciones de los docs, revisión de la DDL, ejecución de la suite (`466 passed, 21 deselected` — las de integración requieren contenedores) y reproducción de los hallazgos sospechosos con scripts mínimos.
**Contexto:** proyecto en desarrollo. Las severidades son relativas a ese estado: "Alta" = pierde datos o puede parar el daemon en producción.

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

---

### ALTA-2 — Cero timeouts + un solo hilo = una base colgada paraliza toda la extracción

**Dónde:** `config/registered.py:105-114`, `config/connections.py:24,42,67`, `runner.py:99-118`

- `create_engine` sin `connect_timeout` (psycopg2/libpq: **infinito por defecto**), sin `read_timeout`/`connect_timeout` de pymysql (este último sí trae 10 s por defecto), sin `pool_recycle`.
- Ninguna query lleva `SET statement_timeout` / `SET SESSION max_execution_time`.
- `runner` es secuencial por diseño: un target que no resuelve (firewall con DROP de paquetes, DNS colgado, `information_schema.columns` de MySQL con cientos de tablas sobre cargas altas) bloquea el ciclo y **todas las demás bases se quedan sin telemetría**. El backoff del runner no aplica: el ciclo nunca termina.

**Sugerencia:** `connect_timeout` en las URLs (o `connect_args`), `statement_timeout` razonable en el target al abrir la sesión (p.ej. 15-30 s), y/o un tope de duración por target.

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

---

### MEDIA-4 — Timestamps con zona horaria "anulados" sin convertir a UTC

**Dónde:** `stages/normalize.py:52,63`, `models/snapshot.py:13`

```python
row[field] = ts.replace(tzinfo=None).isoformat(...)
```

`replace(tzinfo=None)` no convierte: **descarta el offset**. `counters_epoch` de Postgres llega como `timestamptz` (psycopg2 lo renderiza en la TZ del cliente = UTC en el contenedor), mientras que MySQL `FIRST_SEEN` es `DATETIME` naive en la TZ del servidor. Si el servidor MySQL no está en UTC, las dos marcas de tiempo "se ven igual" pero no lo son. Esto contradice el propio comentario (`normalize.py:40-46`: "el consumidor puede compararlas entre motores sin parsear dos formatos").

**Sugerencia:** `ts.astimezone(timezone.utc).replace(tzinfo=None)` en el lado aware, y forzar/ documentar `time_zone='+00:00'` en la sesión de MySQL.

---

### MEDIA-5 — Un error de recolección se publica como "sin datos"

**Dónde:** `stages/collect.py:20` + `models/snapshot.py:37` (`ListOrNone` convierte `None` → `[]`)

Si falla la query de `locks`, el payload llega con `locks: []`. El consumidor no puede distinguir "la base no tiene locks" de "no pude leer los locks". Combinado con A-1: el único fallo que se nota es el de `statements`, y se nota porque **no** llega nada. Además el snapshot no tiene **timestamp de captura** (ver MEDIA-11).

---

### MEDIA-6 — El log y el docstring anuncian un fallback que no existe

**Dónde:** `config/registered.py:12,126,133,145,151` vs `main.py:84-87`

Cinco sitios dicen `"fallback a ENGINES"` y el docstring dice "main() cae a los ENGINES fijos del sandbox". `main.py` **no hace ningún fallback**: devuelve `None` y no extrae nada (`TESTING.md:159` lo reconoce explícitamente). Quien opere el daemon leerá "fallback a ENGINES" en el log y asumirá que sí se está extrayendo del par sandbox.

**Sugerencia:** cambiar los mensajes a `"sin targets registrados | no se extrae nada"` (y el docstring), o implementar el fallback si era la intención.

---

### MEDIA-7 — Lógica de normalización duplicada (dos fuentes de verdad)

- Transformaciones hechas **dos veces**: `normalize_locks` / `normalize_blocking_pids` / `normalize_statement_epochs` (etapa) y los validadores `_to_bool` / `_to_int_list` / `_to_iso` (pydantic) hacen lo mismo con distinto código (`stages/normalize.py:57-79` vs `models/snapshot.py:11-32`). Si cambia uno y no el otro, o el snapshot viaja mal, o se rechaza.
- `_base_operation` está **copiado idéntico** en `stages/explain_normalizer.py:190` y `:337`.
- Construcción del engine MySQL duplicada: `connections.get_connection_mysql` vs `registered._engine` (mismos `pool_size/max_overflow/pre_ping`, pueden divergir; ya divergen en Postgres: el target no tiene `pre_ping`).
- `anonimize_query_text` (`canonicalizers.py:43-51`) es en la práctica un **no-op**: `select_candidates_to_explain` ya copia `query_text` con `{**statement, ...}`, así que "restaurar"lo desde `statements` escribe el mismo valor.

**Sugerencia:** que un lado sea la única fuente (misma función usada por etapa y modelo), extraer `_base_operation` compartido, y borrar o renombrar `anonimize_query_text` (ver MEDIA-10).

---

### MEDIA-8 — `connections.py` arma URLs con f-string; `registered.py` usa `URL.create` por exactamente el motivo que este ignora

**Dónde:** `config/connections.py:24,42,67` vs `config/registered.py:86-102`

`registered.py` comenta: *"URL.create en vez de f-string: la password viene de Fernet y puede contener '@', ':' o '/' que romperían el parseo"*. Las tres conexiones de `connections.py` (incluida la de la **cola**, la más crítica) siguen con f-string. Si `QUERYLENS_PASSWORD` o `MONITOR_*_PASSWORD` contienen caracteres especiales, la URL se parsea mal y el pipeline no puede ni arrancar/enecolar.

**Sugerencia:** migrar las tres a `URL.create` (mismo patrón ya probado por `test_registered_targets.py:70`).

---

### MEDIA-9 — La "inestabilidad" de MySQL no es desviación estándar, y el mismo selector significa cosas distintas por motor

**Dónde:** `collectors/mysql/collector.py:44-57` vs `collectors/postgres/queries.py:38`

- Fórmula MySQL: `stddev = (max_time - mean) / sqrt(count)` — no es una desviación estándar, es un heurístico.
- `if max_time > mean * 1000: coeff = None` descarta **justo los outliers más extremos** (una query con un pico de 1001× la media se ignora; la de 999× se marca). Si la intención es filtrar el calentamiento inicial, el corte está mal ubicado.
- Postgres usa `stddev_exec_time` real. `select_unstable_statements` aplica el mismo umbral (`coeff > 2`) a dos métricas con semánticas distintas.

**Sugerencia:** documentar la fórmula como aproximación en el propio código, alinear los umbrales por motor o usar percentiles (p95/p50) que ambos motores exponen de forma homogénea.

---

### MEDIA-10 — Candidatos sin límite, y "anonimización" que no anonimiza

- `selectors.py:9,13`: `unstable_statements` y `disk_spill_statements` no tienen top-N (solo `high_impact` corta a 10). Una base con miles de queries inestables genera miles de `EXPLAIN` por ciclo, sobre la misma conexión, sin timeout (ver A-2).
- Privacidad: `canonicalizers.anonimize_query_text` no anonimiza nada — el payload lleva `query_text` crudo con literales reales (Postgres) y `QUERY_SAMPLE_TEXT` con **valores concretos** (MySQL), y `canonicalize_query` degrada a `" ".join(query_text.split())` en cualquier fallo de parseo (`canonicalizers.py:27-28`), dejando el literal tal cual. Si el producto habla de anonimización, el nombre miente; si no, al menos renombrar la función y documentar que la cola contiene texto de consultas con datos reales (GDPR/retención).

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

---

### MEDIA-13 — MySQL: el `USE` no se resetea si `schema_name` viene vacío

**Dónde:** `stages/explain.py:31-37`

Postgres emite `SET LOCAL search_path TO DEFAULT` cuando no hay schema; MySQL devuelve `None` y **no emite nada**, dejando el `USE` del candidato anterior (el `USE` es estado de sesión y sobrevive a `rollback`). Si algún candidato llega sin `schema_name`, su `EXPLAIN` corre contra el esquema anterior. Hoy es de baja probabilidad (MySQL siempre trae `schema_name` del performance_schema), pero es una asimetría sin propósito.

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

1. **A-1** (defaults o `collect_errors`) — es la diferencia entre "pierdo una sección" y "pierdo el snapshot".
2. **A-2** (timeouts) — es la diferencia entre "una base caída no molesta" y "el daemon se cuelga".
3. **A-3** (límites de payload) — decidirlo antes de que haya consumidor y cola con días de retención.
4. **M-6, M-7, M-8, M-4** — correcciones acotadas y de bajo riesgo.
5. Lo demás, según avance el proyecto (B-10/B-9 son de despliegue, no de desarrollo).
