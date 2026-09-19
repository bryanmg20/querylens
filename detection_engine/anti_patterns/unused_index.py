import re
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp

from models import Hallazgo, Snapshot
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)

# bajo esta ventana, index_scans == 0 no es confiable todavia. Es un timedelta
# (no un numero de dias fijo) para poder acortarlo en pruebas sin esperar dias reales
DEFAULT_MIN_STATS_WINDOW = timedelta(days=7)


# Mismos artefactos internos que en non_sargable_predicate.py (SubPlan/InitPlan de
# Postgres, marcadores <...> de MySQL); se duplica aca hasta confirmar ambos usos
# por separado, igual que se hizo antes con resolve_table_aliases.
_SUBPLAN_RE = re.compile(r"\((?:hashed\s+)?(?:SubPlan|InitPlan)\s+\d+\)")
_INTERNAL_MARKER_RE = re.compile(r"<[a-zA-Z_]+>\((?:[^<>()]|\([^<>()]*\))*\)")
_MAX_MARKER_PASSES = 10


def _strip_engine_internal_markers(predicate: str) -> str:
    cleaned = _SUBPLAN_RE.sub("NULL", predicate)
    previous = None
    passes = 0
    while previous != cleaned and passes < _MAX_MARKER_PASSES:
        previous = cleaned
        cleaned = _INTERNAL_MARKER_RE.sub("NULL", cleaned)
        passes += 1
    return cleaned


def _predicate_columns(predicate: str | None, dialect: str = "postgres") -> set[str]:
    # Columnas referenciadas en el predicado real de un scan, para cruzar con indices.
    if not predicate:
        return set()
    try:
        tree = sqlglot.parse_one(_strip_engine_internal_markers(predicate), read=dialect)
    except Exception as e:
        logger.warning(
            "_predicate_columns | no se pudo parsear predicate | error=%s | predicate=%.200s",
            e, predicate,
        )
        return set()
    return {col.name for col in tree.find_all(exp.Column) if col.name}


# Postgres y MySQL arman index_ref con el mismo formato de DDL sintetico
# ("CREATE [UNIQUE] INDEX nombre ON schema.tabla USING metodo (col1, col2, ...)"),
# pero pg_get_indexdef() puede sumarle INCLUDE (...), WHERE (...) y DESC por
# columna en el mismo texto. Un regex "ultimo parentesis" los confunde con la
# lista de columnas; parsear con sqlglot los separa de forma estructural.
def _parse_index_ref(
    index_ref: str | None,
    dialect: str = "postgres",
) -> tuple[str | None, list[str | None], bool, bool]:
    # (metodo, columnas lider en orden -- None si es una expresion no reconocida,
    # es_unique, es_parcial)
    if not index_ref:
        return None, [], False, False

    try:
        tree = sqlglot.parse_one(index_ref, read=dialect)
    except Exception as e:
        logger.warning(
            "_parse_index_ref | no se pudo parsear index_ref | error=%s | texto=%.200s",
            e, index_ref,
        )
        return None, [], False, False

    index_node = tree.this if isinstance(tree, exp.Create) else tree
    params = index_node.args.get("params") if isinstance(index_node, exp.Index) else None
    if params is None:
        return None, [], False, False

    method_node = params.args.get("using")
    method = method_node.name.lower() if method_node is not None else None

    columns: list[str | None] = []
    for ordered in params.args.get("columns") or []:
        target = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        # una columna de expresion (ej. lower(x)) no cubre la columna base; se deja en None
        columns.append(target.name if isinstance(target, exp.Column) else None)

    is_unique = bool(tree.args.get("unique")) if isinstance(tree, exp.Create) else False
    is_partial = params.args.get("where") is not None
    return method, columns, is_unique, is_partial


def _stats_window_is_fresh(snapshot: Snapshot, min_window: timedelta) -> bool:
    # Si el reset de estadisticas fue hace menos de min_window, index_scans == 0 no prueba nada aun.
    if not snapshot.stats_reset_timestamp:
        return False
    raw = snapshot.stats_reset_timestamp[0].get("stats_reset")
    if not raw:
        return False
    try:
        reset_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if reset_at.tzinfo is None:
        # MySQL no trae offset; se asume UTC, el error de unas horas no afecta un umbral en dias
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - reset_at < min_window


def _used_index_names(snapshot: Snapshot) -> set[str]:
    # Indices citados por su nombre en algun plan real capturado: evidencia directa de uso.
    return {
        operation.get("index_name")
        for explain in snapshot.canonic_explains
        for operation in explain.get("canonical_plan", {}).get("physical_operations", [])
        if operation.get("index_name")
    }


def _collect_query_traffic(snapshot: Snapshot) -> tuple[dict[str, list[dict]], set[str]]:
    # Un solo recorrido de top_impact_queries: que tablas tienen trafico real, y
    # cuales full scans filtran por que columna (para priorizar "no usado" mas abajo).
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }
    full_scan_columns_by_table: dict[str, list[dict]] = {}
    tables_with_traffic: set[str] = set()

    for candidate in snapshot.top_impact_queries:
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue

        alias_map = resolve_table_aliases(candidate.canonic_query)
        for operation in explain.get("canonical_plan", {}).get("physical_operations", []):
            if operation.get("type") != "scan":
                continue
            relation = operation.get("relation")
            if not relation:
                continue
            real_table = alias_map.get(relation, relation)
            tables_with_traffic.add(real_table)

            if operation.get("access_method") != "full_table_scan":
                continue
            full_scan_columns_by_table.setdefault(real_table, []).append({
                "query_id": candidate.query_id,
                "columns": _predicate_columns(operation.get("predicate")),
                "execution_count": candidate.execution_count,
                "mean_time_ms": candidate.mean_time_ms,
            })

    return full_scan_columns_by_table, tables_with_traffic


def _detect_unused(
    snapshot: Snapshot,
    used_index_names: set[str],
    full_scan_columns_by_table: dict[str, list[dict]],
    tables_with_traffic: set[str],
) -> list[Hallazgo]:
    findings = []
    for index in snapshot.indexes:
        if index.index_scans != 0 or index.last_index_scan is not None:
            continue

        _, columns, _, _ = _parse_index_ref(index.index_ref)
        lead_column = columns[0] if columns else None

        # queries reales que escanean completa esta tabla filtrando justo por
        # la columna que este indice cubriria: la evidencia mas fuerte posible
        affected = [
            entry for entry in full_scan_columns_by_table.get(index.table_name, [])
            if lead_column and lead_column in entry["columns"]
        ]

        if affected:
            severidad = "high"
        elif index.table_name in tables_with_traffic:
            severidad = "medium"  # la tabla tiene trafico, pero no via esta columna
        else:
            severidad = "low"  # nadie toca esta tabla en la ventana capturada

        explicacion = (
            "El indice no registra usos: ni en el contador global (index_scans) ni citado "
            "en ninguno de los planes reales capturados."
        )
        if affected:
            explicacion += (
                " Ademas, hay consultas de alto impacto que hacen un escaneo completo de esta "
                "tabla filtrando exactamente por la columna que este indice cubre."
            )

        findings.append(
            Hallazgo(
                antipatron="unused_index",
                severidad=severidad,
                evidencia={
                    "subtipo": "no_usado",
                    "schema_name": index.schema_name,
                    "table_name": index.table_name,
                    "index_name": index.index_name,
                    "index_scans": index.index_scans,
                    "last_index_scan": index.last_index_scan,
                    "index_size_bytes": index.index_size_bytes,
                    "visto_en_planes_reales": index.index_name in used_index_names,
                    "queries_afectadas": [
                        {k: v for k, v in entry.items() if k != "columns"} for entry in affected
                    ] or None,
                },
                explicacion=explicacion,
                recomendacion=(
                    "Revisar por que el planificador no lo usa pese a existir una consulta real que "
                    "se beneficiaria de el; puede requerir ANALYZE o estar mal definido."
                    if affected else
                    "Validar dependencias y la ventana desde el ultimo reset de estadisticas antes de "
                    "considerar eliminarlo."
                ),
                query_id=affected[0]["query_id"] if affected else None,
                table_name=index.table_name,
            )
        )
    return findings


def _detect_redundant(snapshot: Snapshot, used_index_names: set[str]) -> list[Hallazgo]:
    # Estructural: agrupa por tabla y compara columnas lider como prefijo. No
    # depende de trafico para detectar, solo para calibrar la severidad.
    by_table: dict[str, list] = {}
    for index in snapshot.indexes:
        if index.table_name and index.index_ref:
            by_table.setdefault(index.table_name, []).append(index)

    findings = []
    for table_name, indexes in by_table.items():
        for narrow in indexes:
            narrow_method, narrow_columns, is_unique, narrow_is_partial = _parse_index_ref(narrow.index_ref)
            # sin columnas, o con una columna de expresion sin resolver (None):
            # no se puede comparar el prefijo con seguridad
            if not narrow_columns or any(column is None for column in narrow_columns):
                continue

            wider = None
            wider_is_partial = False
            for wide in indexes:
                if wide is narrow:
                    continue
                wide_method, wide_columns, _, wide_is_partial = _parse_index_ref(wide.index_ref)
                if (
                    wide_method == narrow_method
                    and len(wide_columns) > len(narrow_columns)
                    and wide_columns[: len(narrow_columns)] == narrow_columns
                ):
                    wider = wide
                    wider_is_partial = wide_is_partial
                    break
            if wider is None:
                continue

            seen_in_use = narrow.index_name in used_index_names
            # el WHERE de un indice parcial no se verifica contra la query: solo se
            # avisa la ambiguedad, no se intenta resolver si de verdad aplica
            partial_risk = narrow_is_partial or wider_is_partial

            explicacion = (
                f"Las columnas de este indice son un prefijo exacto de '{wider.index_name}', que ya "
                "lo cubre para cualquier busqueda que este pueda resolver."
            )
            if seen_in_use:
                explicacion += (
                    " Sin embargo, el planificador si lo eligio en planes reales capturados "
                    "(puede ser mas liviano para esas consultas); revisar antes de eliminar."
                )
            if narrow_is_partial:
                explicacion += (
                    " Este indice es parcial (tiene WHERE): puede ser mucho mas chico y rapido "
                    "para esa condicion especifica que el indice ancho que lo 'cubre'."
                )
            if wider_is_partial:
                explicacion += (
                    " Ademas, el indice que lo cubre tambien es parcial: puede no cubrir todas "
                    "las filas que este si cubre, confirmar las condiciones de ambos antes de "
                    "asumir cobertura total."
                )

            findings.append(
                Hallazgo(
                    antipatron="unused_index",
                    # con evidencia real de uso el hallazgo pesa mas (mismo criterio que
                    # no_usado: mas evidencia de trafico real = mas severidad)
                    severidad="medium" if seen_in_use else "low",
                    evidencia={
                        "subtipo": "redundante",
                        "schema_name": narrow.schema_name,
                        "table_name": table_name,
                        "index_name": narrow.index_name,
                        "index_ref": narrow.index_ref,
                        "cubierto_por": wider.index_name,
                        "cubierto_por_index_ref": wider.index_ref,
                        "visto_en_planes_reales": seen_in_use,
                        "narrow_es_parcial": narrow_is_partial,
                        "cubierto_por_es_parcial": wider_is_partial,
                        # separado de la severidad: que tan seguro es actuar sobre el DROP
                        "confianza_eliminar": "baja" if (seen_in_use or partial_risk) else "alta",
                        "es_unique": is_unique,
                    },
                    explicacion=explicacion,
                    recomendacion=(
                        "No eliminar sin revisar: impone una restriccion de unicidad, no solo "
                        "acelera lecturas."
                        if is_unique else
                        "Candidato a eliminar; validar antes que ninguna consulta dependa de su "
                        "tamaño mas chico (ej. index-only scans)."
                    ),
                    table_name=table_name,
                )
            )
    return findings


def detect_unused_indexes(
    snapshot: Snapshot,
    min_stats_window: timedelta = DEFAULT_MIN_STATS_WINDOW,
) -> list[Hallazgo]:
    used_index_names = _used_index_names(snapshot)
    findings = _detect_redundant(snapshot, used_index_names)

    if _stats_window_is_fresh(snapshot, min_stats_window):
        return findings  # "no usado" todavia no es confiable en este snapshot

    full_scan_columns_by_table, tables_with_traffic = _collect_query_traffic(snapshot)
    findings += _detect_unused(snapshot, used_index_names, full_scan_columns_by_table, tables_with_traffic)
    return findings
