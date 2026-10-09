from models import Hallazgo, Snapshot

from .table_aliases import resolve_table_aliases


DEFAULT_MIN_LIVE_ROWS = 10_000  # tabla considerada "grande" para este chequeo
DEFAULT_MAX_SELECTIVITY = 0.1  # el predicado deja pasar como maximo el 10% de la tabla


# Detecta full table scan sobre tabla grande + predicado selectivo
def detect_avoidable_full_scans(
    snapshot: Snapshot,
    min_live_rows: int = DEFAULT_MIN_LIVE_ROWS,
    max_selectivity: float = DEFAULT_MAX_SELECTIVITY,
) -> list[Hallazgo]:
    findings = []
    # indexa los EXPLAIN por query_id para poder cruzarlos con cada candidato
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }
    # tamano real de cada tabla, para calcular selectividad mas abajo. Clave con
    # schema para no confundir tablas homonimas de schemas distintos
    live_rows_by_table: dict[tuple[str | None, str], int] = {}
    for table in snapshot.tables:
        if table.table_name and table.live_rows is not None:
            live_rows_by_table[(table.schema_name, table.table_name)] = table.live_rows

    def _live_rows_for(schema_name: str | None, table_name: str | None) -> int | None:
        if not table_name:
            return None
        live_rows = live_rows_by_table.get((schema_name, table_name))
        if live_rows is not None:
            return live_rows
        # el fallback solo aplica si falta el dato de schema de algun lado (None):
        # si los dos son conocidos pero distintos, no es ambiguedad, es certeza
        # de que son tablas distintas -- no hay que puentearlas
        candidates = [
            lr for (sn, tn), lr in live_rows_by_table.items()
            if tn == table_name and (sn is None or schema_name is None)
        ]
        return candidates[0] if len(candidates) == 1 else None

    for candidate in snapshot.top_impact_queries:
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue

        alias_map = resolve_table_aliases(candidate.canonic_query)
        operations = explain.get("canonical_plan", {}).get("physical_operations", [])
        for operation in operations:
            if operation.get("type") != "scan" or operation.get("access_method") != "full_table_scan":
                continue

            relation = operation.get("relation")
            real_table = alias_map.get(relation, relation)

            live_rows = _live_rows_for(candidate.schema_name, real_table)
            # sin dato de la tabla, o tabla chica: el costo de un indice ahi no compensa
            if live_rows is None or live_rows < min_live_rows:
                continue

            # que fraccion de la tabla deja pasar el predicado
            estimated_rows = operation.get("estimated_rows")
            selectivity = estimated_rows / live_rows if estimated_rows is not None else None
            # predicado no selectivo (deja pasar gran parte de la tabla): el seq scan es lo correcto
            if selectivity is not None and selectivity > max_selectivity:
                continue

            findings.append(
                Hallazgo(
                    antipatron="avoidable_full_scan",
                    severidad="medium",
                    evidencia={
                        "query_id": candidate.query_id,
                        "query_text": candidate.query_text,
                        "canonic_query": candidate.canonic_query,
                        "schema_name": candidate.schema_name,
                        "tables": sorted(set(alias_map.values())),
                        "relation": relation,
                        "predicate": operation.get("predicate"),
                        "estimated_rows": estimated_rows,
                        "live_rows": live_rows,
                        "selectivity": selectivity,
                        "mean_time_ms": candidate.mean_time_ms,
                        "total_time_ms": candidate.total_time_ms,
                        "execution_count": candidate.execution_count,
                    },
                    explicacion=(
                        "El plan real de esta consulta (EXPLAIN) hace un escaneo completo sobre una "
                        "tabla grande con un predicado selectivo, en vez de usar un indice."
                    ),
                    recomendacion=(
                        "Revisar el predicado de filtro contra los indices existentes en la relacion "
                        "afectada; considerar un indice que lo cubra o reescribir la condicion para "
                        "que sea sargable."
                    ),
                    query_id=candidate.query_id,
                    table_name=real_table,
                )
            )
    return findings
