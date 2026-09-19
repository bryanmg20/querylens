from collections.abc import Iterable

from models import Hallazgo, Snapshot

from .avoidable_full_scan import DEFAULT_MIN_ESTIMATED_ROWS, detect_avoidable_full_scans
from .disk_spill import detect_disk_spill
from .non_sargable_predicate import detect_non_sargable_predicates


# Ejecuta un detector puntual por nombre, o todos si no se especifica ninguno
def detect_all(
    snapshot: Snapshot,
    rule: str | None = None,
    min_estimated_rows: int = DEFAULT_MIN_ESTIMATED_ROWS,
) -> list[Hallazgo]:
    # mapea nombre de regla -> funcion detectora, para poder seleccionarla por nombre
    detectors = {
        "disk_spill": detect_disk_spill,
        "avoidable_full_scan": lambda current_snapshot: detect_avoidable_full_scans(
            current_snapshot,
            min_estimated_rows,
        ),
        "non_sargable_predicate": detect_non_sargable_predicates,
    }

    if rule is not None:
        return detectors[rule](snapshot)

    return [
        *detect_disk_spill(snapshot),
        *detect_avoidable_full_scans(snapshot, min_estimated_rows),
        *detect_non_sargable_predicates(snapshot),
    ]


# Agrupa una lista de hallazgos en un dict segun su tipo de antipatron
def findings_by_type(findings: Iterable[Hallazgo]) -> dict[str, list[Hallazgo]]:
    # agrupa por antipatron para no repetir esta logica en cada consumidor
    grouped: dict[str, list[Hallazgo]] = {}
    for finding in findings:
        grouped.setdefault(finding.antipatron, []).append(finding)
    return grouped
