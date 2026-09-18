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
