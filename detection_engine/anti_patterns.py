from collections.abc import Iterable

from models import Hallazgo, Snapshot


DEFAULT_MIN_ESTIMATED_ROWS = 1_000


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


# Detecta consultas cuyo plan real (EXPLAIN) usa full table scan sobre un volumen de filas no trivial
def detect_avoidable_full_scans(
    snapshot: Snapshot,
    min_estimated_rows: int = DEFAULT_MIN_ESTIMATED_ROWS,
) -> list[Hallazgo]:
    findings = []
    # indexa los EXPLAIN por query_id para poder cruzarlos con cada candidato
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }

    for candidate in snapshot.top_impact_queries:
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue

        operations = explain.get("canonical_plan", {}).get("physical_operations", [])
        for operation in operations:
            if operation.get("type") != "scan" or operation.get("access_method") != "full_table_scan":
                continue

            estimated_rows = operation.get("estimated_rows")
            # ignora full scans sobre tablas chicas: el costo de un indice ahi no compensa
            if estimated_rows is not None and estimated_rows < min_estimated_rows:
                continue

            findings.append(
                Hallazgo(
                    antipatron="avoidable_full_scan",
                    severidad="medium",
                    evidencia={
                        "query_id": candidate.query_id,
                        "query_text": candidate.query_text,
                        "canonic_query": candidate.canonic_query,
                        "relation": operation.get("relation"),
                        "predicate": operation.get("predicate"),
                        "estimated_rows": estimated_rows,
                        "mean_time_ms": candidate.mean_time_ms,
                        "total_time_ms": candidate.total_time_ms,
                        "execution_count": candidate.execution_count,
                    },
                    explicacion=(
                        "El plan real de esta consulta (EXPLAIN) usa un escaneo completo de tabla "
                        "en lugar de un indice, sobre un volumen de filas no trivial."
                    ),
                    recomendacion=(
                        "Revisar el predicado de filtro contra los indices existentes en la relacion "
                        "afectada; considerar un indice que lo cubra o reescribir la condicion para "
                        "que sea sargable."
                    ),
                    query_id=candidate.query_id,
                    table_name=operation.get("relation"),
                )
            )
    return findings


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
    }

    if rule is not None:
        return detectors[rule](snapshot)

    return [
        *detect_disk_spill(snapshot),
        *detect_avoidable_full_scans(snapshot, min_estimated_rows),
    ]


# Agrupa una lista de hallazgos en un dict segun su tipo de antipatron
def findings_by_type(findings: Iterable[Hallazgo]) -> dict[str, list[Hallazgo]]:
    # agrupa por antipatron para no repetir esta logica en cada consumidor
    grouped: dict[str, list[Hallazgo]] = {}
    for finding in findings:
        grouped.setdefault(finding.antipatron, []).append(finding)
    return grouped
