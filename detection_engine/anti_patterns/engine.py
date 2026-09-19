from collections.abc import Iterable
from datetime import timedelta

from models import Hallazgo, Snapshot

from .avoidable_full_scan import (
    DEFAULT_MAX_SELECTIVITY,
    DEFAULT_MIN_LIVE_ROWS,
    detect_avoidable_full_scans,
)
from .disk_spill import detect_disk_spill
from .non_sargable_predicate import detect_non_sargable_predicates
from .unused_index import DEFAULT_MIN_STATS_WINDOW, detect_unused_indexes


# Ejecuta un detector puntual por nombre, o todos si no se especifica ninguno
def detect_all(
    snapshot: Snapshot,
    rule: str | None = None,
    min_live_rows: int = DEFAULT_MIN_LIVE_ROWS,
    max_selectivity: float = DEFAULT_MAX_SELECTIVITY,
    min_stats_window: timedelta = DEFAULT_MIN_STATS_WINDOW,
) -> list[Hallazgo]:
    # mapea nombre de regla -> funcion detectora, para poder seleccionarla por nombre
    detectors = {
        "disk_spill": detect_disk_spill,
        "avoidable_full_scan": lambda current_snapshot: detect_avoidable_full_scans(
            current_snapshot,
            min_live_rows,
            max_selectivity,
        ),
        "non_sargable_predicate": detect_non_sargable_predicates,
        "unused_index": lambda current_snapshot: detect_unused_indexes(
            current_snapshot,
            min_stats_window,
        ),
    }

    if rule is not None:
        return detectors[rule](snapshot)

    return [
        *detect_disk_spill(snapshot),
        *detect_avoidable_full_scans(snapshot, min_live_rows, max_selectivity),
        *detect_non_sargable_predicates(snapshot),
        *detect_unused_indexes(snapshot, min_stats_window),
    ]


# Agrupa una lista de hallazgos en un dict segun su tipo de antipatron
def findings_by_type(findings: Iterable[Hallazgo]) -> dict[str, list[Hallazgo]]:
    # agrupa por antipatron para no repetir esta logica en cada consumidor
    grouped: dict[str, list[Hallazgo]] = {}
    for finding in findings:
        grouped.setdefault(finding.antipatron, []).append(finding)
    return grouped
