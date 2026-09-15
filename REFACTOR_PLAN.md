# REFACTOR PLAN — Modularización de `telemetry_pipeline` (Opción A)

Plan paso a paso para convertir `telemetry_pipeline` en un pipeline de etapas sin romper la versión que funciona. Estrategia: **Strangler Fig + Golden Master** — nunca se borra código vivo hasta que el nuevo produce el mismo JSON.

---

## 0. Decisiones de diseño

### Nombres de carpetas

| Nivel | Nombre | Nota |
|---|---|---|
| Top-level | `telemetry_pipeline/` | **Se mantiene.** Es estándar (OpenTelemetry/Datadog usan "telemetry pipeline") y ya está referenciado en README y REFACTOR. |
| Interno | `stages/` | Sustituye al paquete `pipeline/` que se propuso inicialmente, para evitar `telemetry_pipeline/pipeline/`. |

### Regla de oro (dónde vive la lógica)

- **Lógica compartida** (la usan ambos motores: `select_high_impact_time_statements`, `select_unstable_statements`, `select_disk_spill_indicator`, `select_candidates_to_explain`, `select_explain_ready`, `canonicalize_query`, normalizadores, etc.) → **funciones puras** en módulos de `stages/`, llamadas como `selectors.xxx(stats)`.
- **Lógica específica de motor** (p. ej. `calculate_stddev_coeff`, que solo existe en MySQL porque PG trae `stddev_exec_time` nativo) → **método `self` del collector**, disparado por un **hook de capacidad**. El padre jamás conoce ese método.

### Hook de capacidad: `preprocess_statements`

La etapa `CandidatesStage` llama siempre al hook; el motor que no lo necesita tiene no-op:

```python
# collectors/base.py
class DB_Engine_Collector(ABC):
    def preprocess_statements(self, stats):
        return stats                     # no-op: el padre solo devuelve stats

# collectors/mysql/collector.py
class Mysql_Collector(DB_Engine_Collector):
    def calculate_stddev_coeff(self, stats):       # definida AQUÍ, solo MySQL
        import math
        for stmt in stats.get("statements", []):
            ...
        return stats

    def preprocess_statements(self, stats):        # sobreescribe el hook
        return self.calculate_stddev_coeff(stats)

# stages/candidates.py
from . import selectors

class CandidatesStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats):
        stats = self.collector.preprocess_statements(stats)   # PG: stats; MySQL: stats con stddev
        selectors.select_high_impact_time_statements(stats)
        selectors.select_unstable_statements(stats)           # usa coeff_of_variation ya listo
        selectors.select_disk_spill_indicator(stats)
        selectors.select_candidates_to_explain(stats)
        selectors.select_explain_ready(stats)
        return stats
```

La etapa **no sabe** de motores: solo llama `preprocess_statements(stats)` y las selecciones compartidas.

---

## 1. Estructura objetivo

```
telemetry_pipeline/
├── main.py                  # entrypoint (conmutado al orchestrator en Fase 3)
├── runner.py                # entrypoint nuevo (Fase 2), carril paralelo
├── orchestrator.py          # orquesta las 5 etapas, abre UNA conexión
├── snapshot.py              # contrato del snapshot (dataclasses/schema)
├── enqueue.py               # salida a PGMQ (extraído de main.py)
├── config/
│   ├── connections.py       # sysbench_databases.py + querylens_connection.py
│   └── settings.py          # env vars y constantes
├── stages/
│   ├── selectors.py         # funciones puras de selección (Fase 1.2)
│   ├── canonicalizers.py    # canonicalize_query y clean_* (Fase 1.4)
│   ├── explain_normalizers.py  # estrategias PG/MySQL de plan canónico (Fase 1.5)
│   ├── collect.py           # CollectStage
│   ├── candidates.py        # CandidatesStage
│   ├── explain.py           # ExplainStage
│   ├── normalize.py         # NormalizeStage
│   └── enrich.py            # EnrichStage
├── collectors/
│   ├── __init__.py
│   ├── base.py              # ABC + hooks (preprocess_statements) + helpers compartidos
│   ├── factory.py
│   ├── postgres/
│   │   ├── collector.py     # solo config de motor + hook (si aplica)
│   │   └── queries.py
│   └── mysql/
│       ├── collector.py     # configuración + calculate_stddev_coeff + hook
│       └── queries.py
├── logger.py
├── scripts/
│   └── compare_snapshots.py # diff estructural de JSON (golden master)
├── tests/
│   ├── golden/              # fixtures capturados (commiteados)
│   ├── test_queries_contract.py
│   └── test_snapshot_contract.py
└── dumps/                   # salida de los get_* de depuración (gitignored)
```

---

## 2. Fase 0 — Blindaje (0 cambios lógicos)

1. **Rama y tags:**
   ```bash
   git checkout -b refactor/telemetry-pipeline-stages
   git tag baseline/telemetry-pipeline-v1
   ```
2. Añadir `dumps/` a `.gitignore` (junto a `logs/`, `draft/`).
3. **Golden Master:** correr el pipeline actual contra las DBs y guardar fixtures por motor:
   - `tests/golden/postgres_raw.json`, `tests/golden/mysql_raw.json` (stats crudos de `get_stats_complete()`)
   - `tests/golden/postgres_snapshot.json`, `tests/golden/mysql_snapshot.json` (payload encolado)
4. **`scripts/compare_snapshots.py`:** diff estructural de JSON.
   - Determinístico (contrato): claves, tipos, estructura de `canonical_plan`.
   - Volátil (values de tiempo/rows, `msg_id`, `enqueued_at`): comparar contra el mismo corte de tráfico o solo estructura.

**Verificación:** el comparador reporta 0 diferencias estructurales.
**Commit:** `test: add golden master fixtures and snapshot diff script`

---

## 3. Fase 1 — Extracción pura (mover, no reescribir)

Regla: cada método extraído cambia **solo** `self.stats` → parámetro `stats`, y el método viejo queda como wrapper que delega. Correr + diff + commit por módulo.

1. **`config/settings.py` + mover conexiones.** `sysbench_databases.py` + `querylens_connection.py` → `config/connections.py`; `main.py` y `factory.py` actualizan el import. No tocar credenciales hardcodeadas (riesgo aparte, item [C] de REFACTOR).
   - Commit: `refactor: relocate connection helpers into config/connections`
2. **`stages/selectors.py`** ← de `base.py`: `select_high_impact_time_statements`, `select_unstable_statements`, `select_disk_spill_indicator`, `select_candidates_to_explain`, `select_explain_ready`.
   - `calculate_stddev_coeff` **NO** se extrae: queda como método de `Mysql_Collector` + hook `preprocess_statements`.
   - Commit: `refactor: extract selector functions into stages/selectors (behavior-preserving)`
3. **`stages/normalize`** ← de ambos: `_to_number`, `normalize_locks`, `normalize_blocking_pids`, `normalize_active_query_timestamps`, `normalize_predicate`, `clean_mysql_explain_predicate_dynamic`.
4. **`stages/canonicalizers.py`** ← de `base.py`: `canonicalize_query`; ← de ambos: `create_canonic_queries`, `anonimize_query_text`, `normalize_querytext_active`; ← de mysql: `clean_mysql_sintax`.
5. **`stages/explain_normalizers.py`** ← con dos estrategias extraídas tal cual:
   - `PostgresExplainNormalizer` (`normalize_explain`, `_postgres_*`, `_walk_postgres_plan`, `_copy_postgres_fields`)
   - `MysqlExplainNormalizer` (`normalize_explain`, `_mysql_*`, `_walk_mysql_plan`, `_set_mysql_root_cost`)
   - Misma interfaz `normalize(stats) -> list[canonic_explains]`; selección por `collector.source_dialect`.

**Verificación fase:** tras cada sub-extracción, `python main.py` + `compare_snapshots.py` → 0 diffs estructurales. Al terminar: `git tag baseline/telemetry-pipeline-v2`.

---

## 4. Fase 2 — Etapas componibles (código nuevo a un lado)

1. **`snapshot.py`** — contrato explícito (dataclasses o dict-schema) con los campos de REFACTOR: `db_id`, `snapshot_ts`, `server_generation`, `query_id`, `query_key`, `process_id`, `canonic_explains`, `locks`, `active_queries`, `statements`, `tables`, `indexes` + `build_snapshot(stats, collector)`. **Escribe a `collector.stats` en memoria** para que `get_stats()` siga devolviendo lo mismo.
2. **`stages/collect.py`** — `CollectStage.execute()`: loop de `collector.queries` con `conn.rollback()` + `stats[key]=None` + log en error (reutilizando el loop ya extraído).
3. **Resto de stages** — `CandidatesStage`, `ExplainStage`, `NormalizeStage`, `EnrichStage`. Cada uno: constructor guarda `self.collector`; `execute(stats) -> stats`.
4. **`orchestrator.py`** — abre **una sola** conexión (igual que el `with self.engine.connect()` actual), la comparte entre Collect y Explain, y ejecuta el orden exacto de `collect_telemetry` actual: `Collect → Candidates → Explain → Normalize → Enrich`.
5. **`enqueue.py`** — extraer de `main.py` las ~10 líneas de PGMQ: `send_to_queue(payload_json) -> msg_id`.
6. **`runner.py`** — entrypoint nuevo con `orchestrator.run_pipeline(collector)` + `enqueue`. **`main.py` NO se toca.**

**Commit:** `feat: add stage-driven orchestrator and runner (parallel to legacy main)`

---

## 5. Fase 3 — Conmutación

1. **Comparación paralela.** `main.py` (viejo) y `runner.py` (nuevo) espalda con espalda por motor; payloads estructuralmente idénticos. Repetir ≥3 corridas por motor.
2. **Conmutar `main.py`** para delegar al orchestrator. Aprovechar para resolver los 3 bugs del REFACTOR: iterar **ambos** colectores, `source` por collector real, y enriquecer con `db_id`/`snapshot_ts`/`server_generation`.
3. **Borrar código muerto.** `git rm` wrappers y métodos sin llamadores. Verificar:
   ```bash
   git grep -n "collect_telemetry\|select_candidates_to_explain\|_walk_postgres_plan\|normalize_locks"
   # → solo debe quedar el código real
   ```

**Commits:** `refactor: switch main.py to stage-driven orchestrator` + `chore: remove duplicated normalization logic from collectors`

---

## 6. Fase 4 — Cierre

1. **Smoke/contract tests** (item [M] de REFACTOR):
   - `tests/test_queries_contract.py` — cada `_QUERY` del colector devuelve las columnas esperadas.
   - `tests/test_snapshot_contract.py` — dado un fixture raw, el snapshot cumple el contrato y `canonical_plan` está normalizado.
2. **Docs.** Actualizar `README.md` (roles de módulos, flujo) y marcar completados los items [M] de la sección "Modularización" de `REFACTOR.md`.
3. **Cierre.** Commit final + `git tag refactor/telemetry-pipeline-done`; merge a `main` si procede.

---

## 7. Rollback en cada fase

- Antes de Fase 2: `git reset --hard baseline/telemetry-pipeline-v2`
- Después de conmutar: `git revert <commit-de-conmutacion>` y vuelve el `main.py` viejo
- `baseline/telemetry-pipeline-v1` = estado que funciona hoy (punto de partida)

---

## 8. Commits sugeridos (estilo Conventional Commits, en inglés)

| Fase | Commit |
|---|---|
| 0 | `test: add golden master fixtures and snapshot diff script` |
| 1.1 | `refactor: relocate connection helpers into config/connections` |
| 1.2 | `refactor: extract selector functions into stages/selectors (behavior-preserving)` |
| 1.3–1.5 | `refactor: extract X into stages/{name} (behavior-preserving)` |
| 2 | `feat: add stage-driven orchestrator and runner (parallel to legacy main)` |
| 3 | `refactor: switch main.py to stage-driven orchestrator` |
| 3 | `chore: remove duplicated normalization logic from collectors` |
| 4 | `docs: update README and REFACTOR checklist after pipeline modularization` |