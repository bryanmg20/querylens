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
├── test_e2e_main.py                     # main + cola PGMQ (integration)
├── test_enqueue.py                      # pgmq.send parametrizado (unit)
├── test_explain_normalizer.py           # normalizadores de plan (unit)
├── test_explain_normalizer_variants.py  # variantes de plan PG/MySQL (unit)
├── test_explain_stage.py                # ExplainStage + schema context (unit)
├── test_integration_engines.py          # motores reales + cola (integration)
├── test_logger.py                       # logger degradación (unit)
├── test_normalize.py                    # locks, pids, timestamps, predicados (unit)
├── test_normalize_stage.py              # NormalizeStage + hooks (unit)
├── test_orchestrator.py                 # composición de stages (unit)
├── test_queries_contract.py             # SQL vs PIPELINE_FINGERPRINT (contract)
├── test_schema_resolver.py              # schema por rol (unit)
├── test_selectors.py                    # selectores de candidatos (unit)
├── test_select_candidates.py            # CandidatesStage end-to-end unit (unit)
├── test_snapshot.py                     # modelos pydantic del snapshot (contract)
└── test_snapshot_contract.py            # round-trip from_snapshot/to_json (contract)
```

## Cobertura de referencia

- **Contrato (models/snapshot.py)**: cada snapshot de motor debe validar, los `query_id` deben llevar el tipo del motor (str en MySQL, int en Postgres), y el round-trip `from_snapshot → to_json → model_validate_json` debe preservar el payload.
- **Unitarios**: cada stage de selección (top 10, inestables, disk spill, dedupe, `init_ready_for_explain`), canonic de queries con sqlglot y su fallback, normalización de locks/pids/timestamps, limpieza de predicados MySQL, `EXPLAIN` con schema context, y conteo de shapes de los normalizadores de plan.
- **Arquitectura**: estrategias de collector sobre la base común, Factory, registry de normalizadores, firma única `execute` por stage, composición del Orchestrator, y el contrato del Facade `SnapshotPayload`.
- **Integración**: un snapshot consumible por motor en PGMQ, sin claves transitorias en el payload, y aislamiento de motores caídos.

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

### Carga de trabajo en CI

`pg_stat_statements` y `performance_schema` arrancan vacíos, y los tests que necesitan candidatos reales se saltan. `telemetry_pipeline/ci/load.py` crea una tabla mínima y genera tráfico antes de correr la suite. No es la batería del sandbox (`ql_sandbox/scripts/battery.sh`), es lo mínimo para que la capa de integración tenga material.

### Correr la capa de integración en local

```bash
cd telemetry_pipeline && python -m pytest -m integration
```

Se salta sola si los contenedores no están levantados. Con `.github/workflows` no es necesario levantarlos: el job lo hace.

## Por qué hay dos requirements

| Archivo | Para quién | Contenido |
|---------|------------|-----------|
| `requirements.txt` | Runtime (correr el pipeline en un servidor) | sqlalchemy, psycopg2, pymysql, sqlglot, python-dotenv, pydantic |
| `requirements-dev.txt` | Desarrollo (tests) | `-r requirements.txt` + `pytest` |

Pytest no vive en `requirements.txt` a propósito: un entorno que solo ejecuta el pipeline no debe instalar el runner de tests.
