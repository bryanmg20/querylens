# Contrato compartido del snapshot: lo que le toca al detection_engine

Rama: `refactor/shared-snapshot-contract`.

Hasta ahora el contrato del snapshot estaba escrito dos veces: en el pipeline con pydantic (`telemetry_pipeline/models/snapshot.py`) y en el detector con dataclasses (`detection_engine/models.py`). El detector lee con `data.get(campo)`, así que cuando una clave cambia de nombre o desaparece no falla nada: el campo queda en `None` y la regla sale por la rama "sin dato". Ya hay campos así (sección 2).

## 1. Qué cambió en el pipeline (ya hecho)

- Los modelos se movieron a un paquete propio, **`contract/querylens_contract/`**, en la raíz del repo. El archivo es el mismo `snapshot.py` de antes, sin cambios de contenido; `__init__.py` exporta todos los modelos (`SnapshotPayload`, `StatementRow`, `StatementCandidate`, `CanonicExplain`, `CanonicalPlan`, `PhysicalOperation`, `ForeignKeyRow`, ...).
- Es instalable: `contract/pyproject.toml` (paquete `querylens-contract`, depende de `pydantic>=2`).
- El pipeline importa desde `querylens_contract.snapshot`. Ya no existe `telemetry_pipeline/models/snapshot.py`.
- Docker: el servicio `pipeline` de `docker-compose.yml` recibe `contract/` como contexto adicional (`additional_contexts: contract: ./contract`) y el Dockerfile hace `COPY --from=contract . /contract` + `pip install /contract`.
- Tests: `pytest.ini` agrega `contract` a `pythonpath`. Para correr el pipeline fuera de Docker: `pip install -e contract` en el venv.

A partir de aquí, **cualquier cambio de forma del snapshot se hace en `contract/querylens_contract/snapshot.py`** y lo ven los dos lados.

## 2. Desfases actuales entre `detection_engine/models.py` y el contrato

Verificados contra `contract/querylens_contract/snapshot.py` y los goldens (`telemetry_pipeline/tests/golden/*.json`).

### 2.1 Claves que el detector lee y el pipeline no envía

| Detector | Qué envía el pipeline | Efecto hoy |
|---|---|---|
| `Snapshot.stats_reset_timestamp[0]["stats_reset"]` (`anti_patterns/unused_index.py:97-99`) | `server_start_timestamp[0]["server_start_time"]` (renombrado en N-2, ver `CODE_REVIEW.md`) | **Bug:** `_stats_window_is_fresh` siempre devuelve `False`. AP-unused_index nunca descarta índices por ventana de estadísticas recién reiniciada. |
| `Snapshot.source` (`main.py:70,92`) | Nada. Se eliminó a propósito (`004_decisiones_contrato.md`, sección "source") | Los logs siempre imprimen `None`. |
| `CandidateStatement.real_query_found` | Nada | Siempre `None`. |

### 2.2 Campos que el pipeline envía y el detector descarta

| Sección | Campos |
|---|---|
| raíz | `foreign_keys` (lista de `ForeignKeyRow`) |
| `statements` | `database_name`, `canonic_query`, `sort_merge_passes`, `query_text_truncated` |
| `top_impact_queries` / `non_explainable_candidates` | `database_name`, `sort_merge_passes`, `query_text_truncated`, `ready_for_explain` |
| `active_queries` | `database_name`, `canonic_query` |
| `locks` | `database_name` |

### 2.3 `canonic_explains` sin tipo

`Snapshot.canonic_explains` es `list[Mapping]` y las reglas navegan el plan a mano (`explain.get("canonical_plan") or {}` ...). Nada valida la forma de `CanonicalPlan` ni el `Literal` de `PhysicalOperation.type`.

## 3. Cambios sugeridos en el detector

Orden sugerido: 3.1 (bug) primero; el resto puede ir en un solo PR.

### 3.1 Corregir `unused_index` (bug)

En `_stats_window_is_fresh`, leer `server_start_timestamp[0]["server_start_time"]`. Ojo con la semántica: es la **hora de arranque del servidor**, no un reset de `pg_stat_*` (ver N-2 en `CODE_REVIEW.md` y `004_decisiones_contrato.md`). Viaja como UTC naive (`"2026-10-13 02:04:12.864023"`), así que el `replace(tzinfo=timezone.utc)` que ya existe sigue siendo correcto para los dos motores.

### 3.2 Consumir el paquete en vez de `models.py`

1. Docker: en el servicio `detection-engine` de `docker-compose.yml` no hace falta contexto adicional, porque su contexto ya es la raíz del repo. En `detection_engine/Dockerfile`, antes de `COPY detection_engine/ .`:
   ```dockerfile
   COPY contract/ /contract/
   RUN pip install --no-cache-dir /contract
   ```
2. Desarrollo local: `pip install -e contract`.
3. Reemplazar la carga del mensaje:
   ```python
   from querylens_contract import SnapshotPayload

   snapshot = SnapshotPayload.model_validate(payload)
   ```
   Si el mensaje no cumple el contrato, esto lanza `pydantic.ValidationError`. Hoy cualquier excepción deja el mensaje en la cola para reintento (`main.py`, `except Exception`), pero un mensaje que no valida no va a validar en el reintento: conviene capturar `ValidationError` aparte, loguearlo y archivarlo para que no se reintente para siempre.
4. Las reglas pasan a usar atributos tipados:
   - `explain["canonical_plan"]["physical_operations"]` → `explain.canonical_plan.physical_operations`
   - `op.get("access_method")` → `op.access_method`
5. `detection_engine/models.py` se queda solo con lo que es del detector: `StatementHistory` y `Hallazgo`. `Statement`, `CandidateStatement`, `TableStat`, `IndexStat`, `ColumnStat`, `ActiveQuery`, `Lock` y `Snapshot` se borran.

Notas de la migración:

- `SnapshotPayload` exige las 8 claves de `CollectStage` (`statements`, `locks`, `active_queries`, `indexes`, `tables`, `columns`, `server_start_timestamp`, `db_id`). Los tests del detector que arman snapshots a mano con pocas claves van a tener que completarlas (listas vacías sirven).
- Los nombres cambian: `TableStat` → `TableRow`, `IndexStat` → `IndexRow`, `ColumnStat` → `ColumnRow`, `ActiveQuery` → `ActiveQueryRow`, `Lock` → `LockRow`, `Statement` → `StatementRow`, `CandidateStatement` → `StatementCandidate`.
- Los modelos de pydantic no son `Mapping`: donde hoy se hace `dict(row)` o `row.get(...)`, usar `row.model_dump()` o el atributo.
- `query_id` es `str` en MySQL e `int` en Postgres (`QueryId = Union[str, int, None]`), igual que hoy.

### 3.3 Test de contrato contra los goldens

Aunque se haga 3.2, conviene un test en el detector que valide los goldens del pipeline y corra el engine sobre ellos:

```python
import json
from pathlib import Path

import pytest
from querylens_contract import SnapshotPayload

GOLDEN = Path(__file__).resolve().parents[2] / "telemetry_pipeline/tests/golden"


@pytest.mark.parametrize("name", ["postgres_snapshot.json", "mysql_snapshot.json"])
def test_detector_reads_pipeline_goldens(name):
    payload = json.loads((GOLDEN / name).read_text())
    snapshot = SnapshotPayload.model_validate(payload)
    # correr las reglas sobre snapshot: no deben lanzar excepciones
```

Los goldens se regeneran con `telemetry_pipeline/ci/regenerate_goldens.py` sobre motores reales, así que este test se cae en cuanto el detector deja de entender lo que el pipeline produce.

## 4. Qué no cambia

- El JSON que viaja por `analyze_job` es el mismo byte a byte: esta rama solo movió el código que lo produce.
- Las correcciones pendientes de `008_correcciones_detector.md` (D1–D5) siguen vigentes y son independientes de esto.
