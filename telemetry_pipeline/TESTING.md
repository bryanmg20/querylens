# Cómo correr los tests

Suite de tests del pipeline `telemetry_pipeline`: validación de contrato (modelos pydantic contra snapshots reales), tests unitarios de los stages y tests de arquitectura (patrones de diseño).

## Requisitos

- Python 3.11+ (el virtualenv del proyecto: `telemetry_pipeline\venv`).
- Dependencias de desarrollo:

```bash
pip install -r requirements.txt   # pytest
```

La suite corre únicamente sobre fixtures locales (no necesita bases de datos ni PGMQ levantados).

## Ejecutar

El `pytest.ini` vive en la raíz del repositorio (`testpaths = telemetry_pipeline/tests`, `pythonpath = telemetry_pipeline`), así que la suite corre desde cualquier carpeta:

```bash
# Desde la raíz del repo
venv\Scripts\python -m pytest          # Windows (PS/cmd)
# o en bash:  .venv/bin/python -m pytest

# Desde telemetry_pipeline (idéntico, pytest encuentra la config en la raíz)
cd telemetry_pipeline
venv\Scripts\python -m pytest
```

## Comandos útiles

| Comando | Efecto |
|---------|--------|
| `python -m pytest` | Toda la suite |
| `python -m pytest tests/test_snapshot_contract.py` | Solo contrato del snapshot |
| `python -m pytest -m unit` | Solo tests unitarios |
| `python -m pytest -m contract` | Solo contrato + arquitectura |
| `python -m pytest -v` | Verboso (funciones por nombre) |
| `python -m pytest -k "mysql"` | Filtrar por palabra clave |
| `python -m pytest --collect-only` | Listar tests sin ejecutarlos |

## Estructura

```
tests/
├── conftest.py                     # fixtures: mysql_snapshot, postgres_snapshot
├── golden/
│   ├── mysql_snapshot.json         # snapshot real capturado en MySQL
│   └── postgres_snapshot.json      # snapshot real capturado en Postgres
├── test_architecture.py            # patrones de diseño y arquitectura  (marker: contract)
├── test_canonicalizers.py          # canonicalize_query, anonimización, active_queries (unit)
├── test_explain_normalizer.py      # normalizadores de plan PG/MySQL       (unit)
├── test_normalize.py               # locks, pids, timestamps, predicados   (unit)
├── test_selectors.py               # selección de candidatos               (unit)
└── test_snapshot_contract.py       # contrato pydantic del snapshot        (contract)
```

## Cobertura de referencia

- **Contrato (models/snapshot.py)**: cada snapshot de motor debe validar, los `query_id` deben llevar el tipo del motor (str en MySQL, int en Postgres), la dependencia `active_queries.query_id ∈ statements.query_id` (usada por `select_explain_ready`), y el round-trip `from_snapshot → to_json → model_validate_json` debe preservar el payload.
- **Unitarios**: cada stage de selección (top 10, inestables, disk spill, dedupe, explain-ready), canonic de queries con sqlglot y su fallback, normalización de locks/pids/timestamps, limpieza de predicados MySQL (cache, backticks, casts preservados), y el conteo de shapes de los normalizadores de plan.
- **Arquitectura**: estrategias de collector sobre la base común, Factory, registry de normalizadores, firma única `execute` por stage, composición del Orchestrator, y el contrato del Facade `SnapshotPayload`.

## Actualizar fixtures golden

Si cambia el contrato del snapshot, los fixtures se regeneran capturando de nuevo desde las bases:

```bash
python main.py    # vuelca el snapshot de cada motor a la cola PGMQ
```

Y se exportan los nuevos JSON a `tests/golden/` (los originals son `snapshot_1.txt`/`snapshot_2.txt`). Un cambio de contrato debe acompañarse de la actualización de fixtures; si un snapshot deja de validar, la suite falla a propósito (golden master).

## Integración continua sugerida

```yaml
# .github/workflows/pipeline.yml (ejemplo)
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r telemetry_pipeline/requirements.txt -r telemetry_pipeline/requirements-dev.txt
      - run: cd telemetry_pipeline && python -m pytest
```

Los tests de integración real (contra bases/cola) quedan como paso futuro: requieren los contenedores `ql_postgres`/`ql_mysql`/PGMQ y son `pytest.mark.integration`.