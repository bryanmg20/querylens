# Cómo correr los tests

Suite de tests del pipeline `telemetry_pipeline`: validación de contrato (modelos pydantic contra snapshots reales), tests unitarios de los stages y tests de arquitectura (patrones de diseño).

## Requisitos

- Python 3.11+ (el virtualenv del proyecto: `telemetry_pipeline\venv`).
- Dependencias de **desarrollo** (tests):

```bash
pip install -r telemetry_pipeline/requirements-dev.txt
```

`requirements-dev.txt` incluye `-r requirements.txt` (runtime del pipeline) más `pytest`. No instales solo `requirements.txt` para testear: ahí no va pytest.

La suite corre únicamente sobre fixtures locales (no necesita bases de datos ni PGMQ levantados para unit/contract). Los tests `integration` se saltan solos si los motores no están levantados.

## Ejecutar

El `pytest.ini` vive en la raíz del repositorio (`testpaths = telemetry_pipeline/tests`, `pythonpath = telemetry_pipeline`), así que la suite corre desde cualquier carpeta:

```bash
# Desde la raíz del repo
telemetry_pipeline\venv\Scripts\python -m pytest          # Windows (PS/cmd)
# o en bash:  telemetry_pipeline/venv/bin/python -m pytest

# Desde telemetry_pipeline (idéntico, pytest encuentra la config en la raíz)
cd telemetry_pipeline
venv\Scripts\python -m pytest
```

## Comandos útiles

| Comando | Efecto |
|---------|--------|
| `python -m pytest` | Toda la suite (integración se salta si no hay motores) |
| `python -m pytest -m "not integration"` | Solo unit + contract (lo que corre el CI en PR) |
| `python -m pytest -m unit` | Solo tests unitarios |
| `python -m pytest -m contract` | Solo contrato + arquitectura |
| `python -m pytest -m integration` | Solo integración (necesita motores + PGMQ) |
| `python -m pytest -v` | Verboso (funciones por nombre) |
| `python -m pytest -k "mysql"` | Filtrar por palabra clave |
| `python -m pytest --collect-only` | Listar tests sin ejecutarlos |
| `python main_sandbox.py` | Extrae el par postgres/mysql fijo del sandbox y encola en `analyze_job` |
| `python main.py` | Extrae solo las bases registradas y activas en `registered_databases` |

## Estructura

```
tests/
├── conftest.py                          # fixtures: mysql_snapshot, postgres_snapshot
├── golden/
│   ├── mysql_snapshot.json              # snapshot real capturado en MySQL
│   └── postgres_snapshot.json           # snapshot real capturado en Postgres
├── test_architecture.py                 # patrones de diseño (contract)
├── test_candidates_stage.py             # selección + schema_resolver (unit)
├── test_canonicalizers.py               # canonicalize, anonimización, active (unit)
├── test_collectors_explainable.py       # mark_explainable por motor (unit)
├── test_collect_stage.py                # CollectStage + fallos aislados (unit)
├── test_connections.py                  # conexiones env-driven (unit)
├── test_e2e_main.py                     # main_sandbox + main + cola PGMQ (integration)
├── test_enqueue.py                      # pgmq.send parametrizado (unit)
├── test_explain_normalizer.py           # normalizadores de plan (unit)
├── test_explain_normalizer_variants.py  # variantes de plan PG/MySQL (unit)
├── test_explain_stage.py                # ExplainStage + schema context (unit)
├── test_integration_engines.py          # motores reales + cola (integration)
├── test_logger.py                       # logger degradación (unit)
├── test_measure_overhead.py             # veredicto sobrecosto <5% (unit)
├── test_normalize.py                    # locks, pids, timestamps, predicados (unit)
├── test_normalize_stage.py              # NormalizeStage + hooks (unit)
├── test_orchestrator.py                 # composición de stages (unit)
├── test_queries_contract.py             # SQL vs PIPELINE_FINGERPRINT (contract)
├── test_registered_targets.py           # registered_databases → Target (unit)
├── test_schema_resolver.py              # schema por rol (unit)
├── test_selectors.py                    # selectores de candidatos (unit)
├── test_select_candidates.py            # CandidatesStage end-to-end unit (unit)
├── test_snapshot.py                     # modelos pydantic del snapshot (contract)
└── test_snapshot_contract.py            # round-trip from_snapshot/to_json (contract)
```

## Cobertura de referencia

- **Contrato (models/snapshot.py)**: cada snapshot de motor debe validar, los `query_id` deben llevar el tipo del motor (str en MySQL, int en Postgres), y el round-trip `from_snapshot → to_json → model_validate_json` debe preservar el payload.
- **Unitarios**: cada stage de selección (top 10, inestables, disk spill, dedupe, `init_ready_for_explain`), canonic de queries con sqlglot y su fallback, normalización de locks/pids/timestamps, limpieza de predicados MySQL, `EXPLAIN` con schema context, conteo de shapes de los normalizadores de plan, y resolución de targets desde `registered_databases` (descifrado, mapeo de engine, filas rotas, aislamiento por target y qué hace `main()` cuando no hay nada registrado).
- **Arquitectura**: estrategias de collector sobre la base común, Factory, registry de normalizadores, firma única `execute` por stage, composición del Orchestrator, y el contrato del Facade `SnapshotPayload`.
- **Integración**: un snapshot consumible por motor en PGMQ, sin claves transitorias en el payload, y aislamiento de motores caídos (un target que no conecta se loguea y el siguiente sigue encolando).

## Actualizar fixtures golden

Si cambia el contrato del snapshot, los fixtures se regeneran capturando de nuevo desde las bases y exportando a `tests/golden/`. Hay un helper en `ci/regenerate_goldens.py`. Un cambio de contrato debe acompañarse de la actualización de fixtures; si un snapshot deja de validar, la suite falla a propósito (golden master).

## Integración continua

`.github/workflows/pipeline.yml` define dos jobs:

| Job | Cuándo | Qué corre | Servicios |
|-----|--------|-----------|-----------|
| `unit` | cada push y PR | `-m "not integration"` | ninguno |
| `integration` | cada **push** (merge a cualquier rama) | suite completa | Postgres 17 + MySQL 8 + PGMQ |

`unit` es el que bloquea el merge en PR. `integration` corre en cada push (cualquier rama) porque levanta tres motores y tarda; los PR solo corren `unit`.

### Por qué las conexiones se parametrizaron

`config/connections.py` lee host, puerto y credenciales del entorno con fallback al sandbox. En CI los servicios corren en puertos internos distintos:

| Variable | Motor | Default |
|----------|-------|---------|
| `MONITOR_PG_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | Postgres de telemetry | `localhost:5432/ql_demo`, `querylens_monitor` |
| `MONITOR_MY_HOST` / `_PORT` / `_USER` / `_PASSWORD` | MySQL de telemetry | `localhost:3307`, `querylens_monitor` |
| `DB_HOST` / `DB_PORT` / `QUERYLENS_DB` / `QUERYLENS_USER` / `QUERYLENS_PASSWORD` | base de la cola | `localhost:5432/ql_demo`, `ql_user` |

MySQL no tiene variable de base a propósito: entra sin base por defecto porque el `EXPLAIN` depende del `USE` que emite `ExplainStage`.

### Targets desde `registered_databases`

Hay dos puntos de entrada y no se mezclan:

| Entrada | Targets | Quién la usa |
|---------|---------|--------------|
| `python main.py` | lo que haya activo en `registered_databases` (select + descifrado Fernet con `AUTH_ENCRYPTION_KEY`) | producción |
| `python main_sandbox.py` | el par fijo `postgres`/`mysql` por entorno (`MONITOR_PG_*` / `MONITOR_MY_*`) | `ql_sandbox`, tests de integración y el job de CI |

`main.py` **no tiene fallback al par fijo**: si la tabla no existe, no hay filas activas o falta la clave, no se extrae nada y queda un `logger.warning`. Por eso los tests de ciclo van contra `main_sandbox.py` (en CI nunca hay tabla registrada) y `test_e2e_main.py` incluye además una prueba que **crea una fila real en la tabla, corre `main.main()` y la borra**, para que la entrada de producción no quede sin cubrir.

Ver qué va a extraer `main.py`:

```bash
docker exec querylens_db psql -U ql_user -d ql_demo \
  -c "SELECT database_identifier, engine, is_active, connection_name FROM registered_databases;"
```

`test_registered_targets.py` cubre el descifrado, el mapeo de engine, el aislamiento de filas rotas y los casos de fallback, con la base falsificada (no necesita contenedores). El detalle de cada decisión está en `PIPELINE_FLOW.md` § *Targets de conexión*.

### Carga de trabajo en CI

`pg_stat_statements` y `performance_schema` arrancan vacíos, y los tests que necesitan candidatos reales se saltan. `telemetry_pipeline/ci/load.py` crea una tabla mínima y genera tráfico antes de correr la suite. No es la batería del sandbox (`ql_sandbox/scripts/battery.sh`), es lo mínimo para que la capa de integración tenga material.

### Correr la capa de integración en local

```bash
cd telemetry_pipeline && python -m pytest -m integration
```

Se salta sola si los contenedores no están levantados. Con `.github/workflows` no es necesario levantarlos: el job lo hace.

#### MySQL se queda sin digests en cada `down`/`up`

Los dos motores guardan sus estadísticas de statements en sitios distintos y no sobreviven igual a un reinicio del sandbox:

| Motor | Dónde viven las stats | ¿Sobreviven a `docker compose down` + `up`? |
|-------|----------------------|----------------------------------------------|
| Postgres | `pg_stat_statements` con `pg_stat_statements.save=on` (default): se escribe en `postgres_data` | **Sí** |
| MySQL | `events_statements_summary_by_digest` en `performance_schema` | **No** |

`performance_schema` es memoria pura: *"Tables in the Performance Schema are in-memory tables that use no persistent on-disk storage. The contents are repopulated beginning at server startup and discarded at server shutdown"* (MySQL 8.0 Ref. Manual, §29). No hay variable que lo arregle (no existe `performance_schema*save*` ni `...dump*`; `SET PERSIST` persiste variables, no estadísticas) y el volumen `ql_sandbox_mysql_data` guarda los datos de `ql_demo`, no el `performance_schema`.

Resultado: tras cada `down`, `up` o `restart`, `test_mysql_statements_expose_query_sample_text` y `test_mysql_statements_exclude_internals` **fallan** con `performance_schema deberia tener digests tras la batería`. `STATEMENTS_QUERY` solo ve la huella del propio pipeline y de la conexión del driver, y su `WHERE` la filtra a propósito (es telemetría interna, no de aplicación). En CI no ocurre: `ci/load.py` genera tráfico antes del pytest.

Para repoblar los digests en local:

```bash
docker exec ql_sysbench bash /scripts/battery.sh 30 0 mysql
```

`battery.sh` **trunca** la tabla de digests al empezar (línea 59) para que log, `pg_stat_statements` y digests compartan ventana de medición: no es un fallo, es el script el que manda.

## Sobrecosto de recolección (PrimerInforme)

El informe exige un sobrecosto **inferior al 5 %** sobre la métrica de rendimiento de la carga observada. La validación es **manual y reproducible** con el sandbox; CI no la gatea (CI solo valida que las extracciones se encolan).

Detalle completo de fases, JSON y limitaciones: [`MEASURE_OVERHEAD.md`](MEASURE_OVERHEAD.md).

### Métrica del veredicto

La carga observada es un **load continuo** (`ql_sandbox/scripts/continuous_load.sh`): while-true con las mismas queries variadas que `battery.sh` contra `sbtest1`. Cada query se **tiena** y se escribe en `/tmp/ql_load_metrics.csv` (`ts_ms,engine,duration_ms,status`). El load **no se detiene** durante la medición.

El veredicto usa la **latencia p95** de esas queries OK, con y sin pipeline, bajo el mismo load:

```
sobrecosto_p95 = (p95_con − p95_sola) / p95_sola × 100

CUMPLE si sobrecosto_p95 < 5.0
```

**No se asume CUMPLE sin datos** (`SIN_DATO` si no hay queries OK suficientes). QPS y CPU del contenedor se reportan como evidencia secundaria; no rescatan el veredicto. Veredicto global: ambos motores deben cumplir.

### Cómo correrlo

```bash
# 1. Sandbox arriba (PG + MySQL + sysbench + cola PGMQ)
docker compose -f ql_sandbox/docker-compose.yml up -d
docker compose up -d

# 2. Dependencias del pipeline
cd telemetry_pipeline
pip install -r requirements-dev.txt

# 3. Medición (perfil rápido ~minutos)
python measure_overhead.py 10 quick      # 10s por ventana, 2 rondas
python measure_overhead.py 20           # 20s por ventana, 3 rondas, pipeline cada 10s
python measure_overhead.py 20 3 10      # measure_s, rondas, cadencia (s)
```

Salida de resumen (formato; los números salen del JSON del run):

```
RESUMEN (veredicto = sobrecosto p95 latencia de la carga)
  postgres   p95 <A>ms -> <B>ms sobrecosto=<X>% | qps=<Y>% | cpu=<Z> pts% rondas=<N> -> <VEREDICTO>
  mysql      ...
  fase B postgres: pipeline=...s pg_monitor=...s mysql_monitor=...s
  fase B mysql: ...
  umbral p95 < 5.0% | GLOBAL: <VEREDICTO>
JSON: logs/overhead_<timestamp>.json
```

| Exit code | Significado |
|-----------|-------------|
| `0` | CUMPLE (sobrecosto p95 &lt; 5% en ambos motores) |
| `1` | Error o SIN_DATO (sandbox caído / sin queries OK en la ventana) |
| `2` | NO CUMPLE (sobrecosto p95 ≥ 5% en al menos un motor) |

### Metodología (carga continua, aislada por motor)

1. **[A]** Base ociosa: CPU/RAM de los contenedores sin carga.
2. **[B]** Costo absoluto **por motor**: 1 extracción de `main.run_engine(dialect)` sin load + tiempo de servidor del monitor (PG: rol `querylens_monitor`; MySQL: `PIPELINE_FINGERPRINT`). El monitor del motor no medido debe quedar ~0.
3. **[C]** Por motor, de forma aislada (`run_engine` solo de ese motor + load solo de ese motor):
   - Warmup ~8 s del load de ese motor.
   - N rondas con load siempre activo:
     - Limpia el CSV; ventana `MEASURE_S` **sin** pipeline → p50/p95/QPS + CPU/mem.
     - Limpia el CSV; ventana `MEASURE_S` **con** pipeline cada `EXTRACT_EVERY_S` (si cadencia ≥ ventana: 1 disparo a la mitad).
     - `sobrecosto_p95` y deltas secundarios (qps, cpu pts%).
4. Veredicto por motor: promedio de `sobrecosto_p95` de las rondas. Al final se detiene el load.

**Sobrecosto ≤ 0:** en esa ventana la carga no se midió más lenta (puede ser ruido). No prueba que el pipeline sea gratis; prueba que **no se midió frenado ≥5%**.

### Qué reporta además del %

| Fase | Contenido |
|------|-----------|
| A | Base ociosa: CPU/RAM de cada contenedor |
| B | 1 extracción sin load: wall, # statements del monitor, tiempo de servidor, CPU/mem del contenedor del motor |
| C | Por motor, N rondas: p50/p95/QPS/n/failures de carga vs carga+pipeline, sobrecosto p95, sobrecosto QPS/CPU/mem, nº de extracciones |

El JSON queda en `telemetry_pipeline/logs/overhead_<timestamp>.json`:

```json
{
  "method": "continuous_load_per_engine_latency",
  "metric": "latency_p95_pct",
  "umbral_pct": 5.0,
  "verdict": "CUMPLE | NO CUMPLE | SIN_DATO",
  "engines": {
    "postgres": {
      "metric": "latency_p95_pct",
      "p95_load_ms": 506.5,
      "p95_con_ms": 522.1,
      "p95_overhead_pct": 3.1,
      "qps_overhead_pct": 5.1,
      "cpu_overhead_pct": 3.9,
      "verdict": "CUMPLE",
      "round_details": [
        { "round": 1,
          "latency_load": {"n": 87, "failures": 0, "p95_ms": 507.7, "qps": 8.7},
          "latency_con":  {"n": 86, "failures": 0, "p95_ms": 489.8, "qps": 8.6},
          "p95_overhead_pct": -3.5 }
      ]
    }
  }
}
```

Los campos van en `null` cuando no hay dato; el script **no** rellena con ceros ni inventa percentiles.

### Limitaciones conocidas

- **Veredicto = p95 de latencia de la carga**, no CPU. Con contenedor saturado el delta de CPU se pierde en ruido de muestreo.
- **Ventanas cortas = pocos samples.** Con 10 s puede haber pocas queries OK; el p95 se estabiliza con 20–30 s y 3+ rondas. El script avisa si `n < 20`.
- **Ruido entre rondas:** una ronda puede dar ≥5% aunque el promedio sea <5%; el veredicto es el promedio (ver ejemplo real en `MEASURE_OVERHEAD.md`).
- **Fase B CPU suele ir `n/a`:** el sampler de Docker es cada 1.5 s y la extracción dura ~0.3–0.6 s; a menudo no cae sample en la ventana. El wall y el monitor del servidor sí se reportan.
- MySQL sin `digest` poblado: el tiempo del monitor va `null` (solo afecta fase B).
- Requiere Docker con `ql_sysbench` (no corre en el CI de GitHub Actions). `scripts/` está montado en el contenedor, así que `continuous_load.sh` no requiere rebuild.

## Por qué hay dos requirements

| Archivo | Para quién | Contenido |
|---------|------------|-----------|
| `requirements.txt` | Runtime (correr el pipeline en un servidor) | sqlalchemy, psycopg2, pymysql, sqlglot, python-dotenv, pydantic |
| `requirements-dev.txt` | Desarrollo (tests) | `-r requirements.txt` + `pytest` |

Pytest no vive en `requirements.txt` a propósito: un entorno que solo ejecuta el pipeline no debe instalar el runner de tests.
