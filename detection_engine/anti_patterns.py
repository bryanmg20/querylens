from collections.abc import Iterable

from models import Hallazgo, Snapshot


# Detecta consultas que escribieron datos temporales en disco
def detect_disk_spill(snapshot: Snapshot) -> list[Hallazgo]:
    findings = []
    for candidate in snapshot.top_impact_queries:
        # disk_spill_indicator puede venir None; > 0 significa que hubo escritura a disco
        if (candidate.disk_spill_indicator or 0) <= 0:
            continue

        findings.append(
            Hallazgo(
                antipatron="disk_spill",
                severidad="high",
                evidencia={
                    "query_id": candidate.query_id,
                    "query_text": candidate.query_text,
                    "canonic_query": candidate.canonic_query,
                    "disk_spill_indicator": candidate.disk_spill_indicator,
                    "mean_time_ms": candidate.mean_time_ms,
                    "total_time_ms": candidate.total_time_ms,
                    "execution_count": candidate.execution_count,
                },
                explicacion=(
                    "La consulta escribe datos temporales en disco, lo que puede aumentar la latencia "
                    "y la presion de I/O."
                ),
                recomendacion=(
                    "Revisar el plan y el volumen de datos; evaluar filtros, agrupaciones, ordenamientos "
                    "e indices antes de aumentar memoria global."
                ),
                query_id=candidate.query_id,
            )
        )
    return findings


# Ejecuta un detector puntual por nombre, o todos si no se especifica ninguno
def detect_all(snapshot: Snapshot, rule: str | None = None) -> list[Hallazgo]:
    # mapea nombre de regla -> funcion detectora, para poder seleccionarla por nombre
    detectors = {
        "disk_spill": detect_disk_spill,
    }

    if rule is not None:
        return detectors[rule](snapshot)

    return [*detect_disk_spill(snapshot)]


# Agrupa una lista de hallazgos en un dict segun su tipo de antipatron
def findings_by_type(findings: Iterable[Hallazgo]) -> dict[str, list[Hallazgo]]:
    # agrupa por antipatron para no repetir esta logica en cada consumidor
    grouped: dict[str, list[Hallazgo]] = {}
    for finding in findings:
        grouped.setdefault(finding.antipatron, []).append(finding)
    return grouped
