from collections.abc import Iterable
from datetime import datetime, timedelta

from models import Hallazgo, Snapshot, StatementHistory

from .avoidable_full_scan import (
    DEFAULT_MAX_SELECTIVITY,
    DEFAULT_MIN_LIVE_ROWS,
    detect_avoidable_full_scans,
)
from .baseline_degradation import detect_baseline_degradation
from .cardinality_misestimate import detect_cardinality_misestimates
from .disk_spill import detect_disk_spill
from .missing_index import detect_missing_indexes
from .non_sargable_predicate import detect_non_sargable_predicates
from .unused_index import DEFAULT_MIN_STATS_WINDOW, detect_unused_indexes


# Reglas que corren cuando no se pide una puntual. Por ahora solo las que no
# usan canonic_explains: la forma de los EXPLAIN esta cambiando en el pipeline.
# Quedan afuera hasta que se estabilice: avoidable_full_scan (AP-02),
# non_sargable_predicate (AP-03), missing_index (AP-04), unused_index (AP-05,
# usa el plan como evidencia de uso de un indice) y cardinality_misestimate (AP-08).
# Se pueden seguir corriendo por nombre con `rule`.
ACTIVE_RULES = (
    "disk_spill",  # AP-07
    "baseline_degradation",  # AP-01
)


# Ejecuta un detector puntual por nombre, o los de ACTIVE_RULES si no se especifica ninguno
def detect_all(
    snapshot: Snapshot,
    rule: str | None = None,
    min_live_rows: int = DEFAULT_MIN_LIVE_ROWS,
    max_selectivity: float = DEFAULT_MAX_SELECTIVITY,
    min_stats_window: timedelta = DEFAULT_MIN_STATS_WINDOW,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
) -> list[Hallazgo]:
    # mapea nombre de regla -> funcion detectora, para poder seleccionarla por nombre
    detectors = {
        "disk_spill": lambda current_snapshot: detect_disk_spill(current_snapshot, history, captured_at),
        "avoidable_full_scan": lambda current_snapshot: detect_avoidable_full_scans(
            current_snapshot,
            min_live_rows,
            max_selectivity,
        ),
        # mismos umbrales que avoidable_full_scan: la base de escaneo completo es identica
        "missing_index": lambda current_snapshot: detect_missing_indexes(
            current_snapshot,
            min_live_rows,
            max_selectivity,
        ),
        "non_sargable_predicate": lambda current_snapshot: detect_non_sargable_predicates(
            current_snapshot,
            history,
        ),
        "unused_index": lambda current_snapshot: detect_unused_indexes(
            current_snapshot,
            min_stats_window,
            history,
        ),
        "baseline_degradation": lambda current_snapshot: detect_baseline_degradation(
            current_snapshot,
            history,
            captured_at,
        ),
        "cardinality_misestimate": lambda current_snapshot: detect_cardinality_misestimates(
            current_snapshot,
            history,
            captured_at,
        ),
    }

    if rule is not None:
        return detectors[rule](snapshot)

    return [finding for name in ACTIVE_RULES for finding in detectors[name](snapshot)]


# Agrupa una lista de hallazgos en un dict segun su tipo de antipatron
def findings_by_type(findings: Iterable[Hallazgo]) -> dict[str, list[Hallazgo]]:
    # agrupa por antipatron para no repetir esta logica en cada consumidor
    grouped: dict[str, list[Hallazgo]] = {}
    for finding in findings:
        grouped.setdefault(finding.antipatron, []).append(finding)
    return grouped
