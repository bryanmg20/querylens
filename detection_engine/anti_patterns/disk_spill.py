from models import Hallazgo, Snapshot

from .table_aliases import resolve_table_aliases


# Detecta consultas que escribieron datos temporales en disco
def detect_disk_spill(snapshot: Snapshot) -> list[Hallazgo]:
    findings = []
    for candidate in snapshot.top_impact_queries:
        # disk_spill_indicator puede venir None; > 0 significa que hubo escritura a disco
        if (candidate.disk_spill_indicator or 0) <= 0:
            continue

        # el spill es de la ejecucion completa (sort/hash), no de una relacion
        # puntual del plan, asi que se listan todas las tablas de la query
        alias_map = resolve_table_aliases(candidate.canonic_query)
        tables = sorted(set(alias_map.values()))

        findings.append(
            Hallazgo(
                antipatron="disk_spill",
                severidad="high",
                evidencia={
                    "query_id": candidate.query_id,
                    "query_text": candidate.query_text,
                    "canonic_query": candidate.canonic_query,
                    "disk_spill_indicator": candidate.disk_spill_indicator,
                    "tables": tables,
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
                table_name=", ".join(tables) if tables else None,
            )
        )
    return findings
