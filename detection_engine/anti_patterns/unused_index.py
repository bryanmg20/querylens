import re
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp

from models import Hallazgo, Snapshot, StatementHistory
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)

# bajo esta ventana, index_scans == 0 no es confiable todavia. Es un timedelta
# (no un numero de dias fijo) para poder acortarlo en pruebas sin esperar dias reales
DEFAULT_MIN_STATS_WINDOW = timedelta(minutes=1)


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


def _collect_query_traffic(
    snapshot: Snapshot,
) -> tuple[dict[tuple[str | None, str], list[dict]], set[tuple[str | None, str]]]:
    # Un solo recorrido de top_impact_queries: que tablas tienen trafico real, y
    # cuales full scans filtran por que columna (para priorizar "no usado" mas abajo).
    # Clave (schema_name, tabla): schema_name es el de la query completa
    # (candidate.schema_name), no por tabla individual dentro de ella.
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }
    full_scan_columns_by_table: dict[tuple[str | None, str], list[dict]] = {}
    tables_with_traffic: set[tuple[str | None, str]] = set()

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
            key = (candidate.schema_name, real_table)
            tables_with_traffic.add(key)

            if operation.get("access_method") != "full_table_scan":
                continue
            full_scan_columns_by_table.setdefault(key, []).append({
                "query_id": candidate.query_id,
                "columns": _predicate_columns(operation.get("predicate")),
                "execution_count": candidate.execution_count,
                "mean_time_ms": candidate.mean_time_ms,
            })

    return full_scan_columns_by_table, tables_with_traffic


# Misma conversion que statement_history.parse_instant; se duplica a proposito
# hasta confirmar este detector por separado, como el resto de la logica de ventana.
def _parse_instant(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _current_counters(snapshot: Snapshot) -> dict[str, tuple[int, float, str | None]]:
    # (ejecuciones, tiempo total, counters_epoch) por query, sumando filas
    # repetidas igual que la fila guardada en statement_samples
    counters: dict[str, tuple[int, float, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None:
            continue
        key = str(statement.query_id)
        calls, total_ms, epoch = counters.get(key, (0, 0.0, statement.counters_epoch))
        counters[key] = (calls + statement.execution_count, total_ms + (statement.total_time_ms or 0.0), epoch)
    return counters


def _window_activity(
    calls: int,
    total_ms: float,
    epoch: datetime | None,
    previous: StatementHistory | None,
) -> tuple[int, float] | None:
    # (ejecuciones, tiempo) desde el snapshot anterior, o None si la query no
    # corrio en ese lapso. Sin fila anterior es la primera vez que se la ve: se
    # toma su acumulado (no es una repeticion). Mismo criterio que AP-03.
    if previous is None:
        return (calls, total_ms) if calls > 0 else None
    # contadores reiniciados: todo lo acumulado es posterior al reinicio
    if epoch is not None and previous.counters_epoch is not None and epoch != previous.counters_epoch:
        return (calls, total_ms) if calls > 0 else None
    delta_calls = calls - previous.execution_count
    if delta_calls < 0:
        # reinicio sin epoch (snapshot viejo): mismo criterio
        return (calls, total_ms) if calls > 0 else None
    if delta_calls == 0:
        return None
    return delta_calls, max(total_ms - previous.total_time_ms, 0.0)


def _filter_columns_by_table(
    canonic_query: str | None,
    columns_by_table: dict[str, set[str]],
) -> dict[str, set[str]]:
    # Tabla real -> columnas por las que la query filtra (WHERE y JOIN ... ON),
    # leidas del canonic_query (dialecto postgres en ambos motores). Una columna
    # sin calificar se asigna a la unica tabla de la query, o a las tablas que la
    # tienen segun snapshot.columns; si no se sabe, a todas.
    if not canonic_query:
        return {}
    try:
        tree = sqlglot.parse_one(canonic_query, read="postgres")
    except Exception as e:
        logger.warning(
            "_filter_columns_by_table | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return {}

    alias_map = resolve_table_aliases(canonic_query)
    tables = sorted(set(alias_map.values()))
    roots = [where.this for where in tree.find_all(exp.Where)] + [
        join.args["on"] for join in tree.find_all(exp.Join) if join.args.get("on") is not None
    ]

    filtered: dict[str, set[str]] = {}
    for root in roots:
        for column in root.find_all(exp.Column):
            if not column.name:
                continue
            if column.table:
                owners = [alias_map[column.table]] if column.table in alias_map else []
            elif len(tables) == 1:
                owners = tables
            else:
                owners = [t for t in tables if column.name in columns_by_table.get(t, set())] or tables
            for owner in owners:
                filtered.setdefault(owner, set()).add(column.name)
    return filtered


def _collect_filter_traffic(
    snapshot: Snapshot,
    history: dict[str, StatementHistory],
    full_scan_columns_by_table: dict[tuple[str | None, str], list[dict]],
) -> dict[tuple[str | None, str], list[dict]]:
    # (schema, tabla) -> queries de top_impact_queries que corrieron desde el
    # snapshot anterior y filtran esa tabla. Son las candidatas a query_id de los
    # hallazgos: la query que toca la tabla donde esta el indice que no sirve.
    columns_by_table: dict[str, set[str]] = {}
    for column in snapshot.columns:
        if column.table_name and column.column_name:
            columns_by_table.setdefault(column.table_name, set()).add(column.column_name)

    counters = _current_counters(snapshot)
    traffic: dict[tuple[str | None, str], list[dict]] = {}
    for candidate in snapshot.top_impact_queries:
        if candidate.query_id is None:
            continue
        query_id = str(candidate.query_id)
        calls, total_ms, epoch_raw = counters.get(
            query_id,
            (candidate.execution_count or 0, candidate.total_time_ms or 0.0, candidate.counters_epoch),
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)
        activity = _window_activity(calls, total_ms, epoch, history.get(query_id))
        if activity is None:
            continue
        window_calls, window_time_ms = activity

        for table, filter_columns in _filter_columns_by_table(candidate.canonic_query, columns_by_table).items():
            key = (candidate.schema_name, table)
            full_scan_columns = {
                column
                for entry in _full_scan_entries_for_table(full_scan_columns_by_table, candidate.schema_name, table)
                if entry["query_id"] == candidate.query_id
                for column in entry["columns"]
            }
            traffic.setdefault(key, []).append({
                "query_id": candidate.query_id,
                "query_text": candidate.query_text,
                "filter_columns": filter_columns,
                # el plan real recorre la tabla completa filtrando por esas columnas
                "full_scan_columns": full_scan_columns,
                "window_calls": window_calls,
                "window_time_ms": window_time_ms,
            })
    return traffic


def _queries_for_table(
    traffic: dict[tuple[str | None, str], list[dict]],
    schema_name: str | None,
    table_name: str,
) -> list[dict]:
    # Mismo criterio de schema que _full_scan_entries_for_table
    exact = traffic.get((schema_name, table_name))
    if exact is not None:
        return exact
    candidates = [
        entries for (sn, tn), entries in traffic.items()
        if tn == table_name and (sn is None or schema_name is None)
    ]
    return candidates[0] if len(candidates) == 1 else []


def _pick_query(queries: list[dict], lead_column: str | None) -> tuple[dict, bool]:
    # (query elegida como query_id, filtra por la columna lider del indice).
    # Primero una que filtre por esa columna (el indice deberia servirle y no se
    # usa), y entre las que empatan la de mas tiempo en la ventana.
    matching = [q for q in queries if lead_column and lead_column in q["filter_columns"]]
    pool = matching or queries
    chosen = max(pool, key=lambda q: (bool(lead_column and lead_column in q["full_scan_columns"]), q["window_time_ms"]))
    return chosen, bool(matching)


def _queries_evidence(queries: list[dict], lead_column: str | None) -> list[dict]:
    return [
        {
            "query_id": q["query_id"],
            "query_text": q["query_text"],
            "filtra_por": sorted(q["filter_columns"]),
            "filtra_por_columna_del_indice": bool(lead_column and lead_column in q["filter_columns"]),
            "scan_completo_por_esa_columna": bool(lead_column and lead_column in q["full_scan_columns"]),
            "window_calls": q["window_calls"],
            "window_time_ms": q["window_time_ms"],
        }
        for q in queries
    ]


def _full_scan_entries_for_table(
    full_scan_columns_by_table: dict[tuple[str | None, str], list[dict]],
    schema_name: str | None,
    table_name: str,
) -> list[dict]:
    exact = full_scan_columns_by_table.get((schema_name, table_name))
    if exact is not None:
        return exact
    candidates = [
        entries for (sn, tn), entries in full_scan_columns_by_table.items()
        if tn == table_name and (sn is None or schema_name is None)
    ]
    return candidates[0] if len(candidates) == 1 else []


def _detect_unused(
    snapshot: Snapshot,
    used_index_names: set[str],
    traffic: dict[tuple[str | None, str], list[dict]],
    redundant_index_names: set[tuple[str | None, str, str]],
) -> list[Hallazgo]:
    findings = []
    for index in snapshot.indexes:
        if index.index_scans != 0 or index.last_index_scan is not None:
            continue
        if not index.table_name:
            continue
        # ya se reporta como redundante en este snapshot: es el diagnostico mas
        # preciso (dice cual indice lo cubre), no se repite como no usado
        if (index.schema_name, index.table_name, index.index_name) in redundant_index_names:
            continue

        _, columns, is_unique, _ = _parse_index_ref(index.index_ref)
        # primarios y unicos se excluyen (informe, AP-05): garantizan que no se
        # repitan valores, asi que no se pueden eliminar aunque nadie los lea
        if is_unique:
            continue
        lead_column = columns[0] if columns else None

        # cada hallazgo necesita una query asociada: una de top_impact_queries que
        # corrio desde el snapshot anterior y filtra esta tabla. Sin ninguna, no
        # se reporta (tampoco se repite en snapshots donde nadie toco la tabla)
        queries = _queries_for_table(traffic, index.schema_name, index.table_name)
        if not queries:
            continue
        chosen, filters_by_lead = _pick_query(queries, lead_column)

        # la query filtra por la columna del indice y aun asi no se usa: deberia
        # servirle y algo lo impide. Si filtra por otra columna, solo comparten tabla
        severidad = "high" if filters_by_lead else "low"

        explicacion = (
            "El indice no registra usos: ni en el contador global (index_scans) ni citado "
            "en ninguno de los planes reales capturados."
        )
        if filters_by_lead:
            explicacion += (
                " La consulta asociada filtra esta tabla justo por la columna que el indice "
                "cubre y aun asi no lo usa."
            )
        else:
            explicacion += (
                " La consulta asociada filtra esta tabla por otras columnas: el indice no le "
                "sirve, pero se mantiene en cada escritura sobre la tabla."
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
                    "index_lead_column": lead_column,
                    "index_scans": index.index_scans,
                    "last_index_scan": index.last_index_scan,
                    "index_size_bytes": index.index_size_bytes,
                    "visto_en_planes_reales": index.index_name in used_index_names,
                    "queries_que_filtran_la_tabla": _queries_evidence(queries, lead_column),
                },
                explicacion=explicacion,
                recomendacion=(
                    "Revisar por que el planificador no lo usa pese a existir una consulta real que "
                    "se beneficiaria de el; puede requerir ANALYZE o estar mal definido."
                    if filters_by_lead else
                    "Validar dependencias y la ventana desde el ultimo reset de estadisticas antes de "
                    "considerar eliminarlo."
                ),
                query_id=chosen["query_id"],
                table_name=index.table_name,
            )
        )
    return findings


def _keeps_over(keeper, keeper_unique: bool, other, other_unique: bool) -> bool:
    # Entre dos indices duplicados exactos, True si se conserva `keeper` y se
    # reporta `other`. Orden fijo para que nunca se reporten los dos: primero el
    # unico (respalda una restriccion), despues el que tiene mas lecturas, y si
    # empatan el de nombre alfabeticamente menor.
    if keeper_unique != other_unique:
        return keeper_unique
    keeper_scans, other_scans = keeper.index_scans or 0, other.index_scans or 0
    if keeper_scans != other_scans:
        return keeper_scans > other_scans
    return (keeper.index_name or "") < (other.index_name or "")


def _detect_redundant(
    snapshot: Snapshot,
    used_index_names: set[str],
    traffic: dict[tuple[str | None, str], list[dict]],
) -> list[Hallazgo]:
    # Estructural: agrupa por (schema, tabla) y compara columnas lider. Un indice
    # es redundante si sus columnas son un prefijo exacto de otro mas ancho, o si
    # es un duplicado exacto de otro (mismas columnas, mismo orden, mismo metodo).
    # Cada IndexStat ya trae su propio schema_name, sin ambiguedad posible aca (a
    # diferencia del trafico de queries, esto no necesita fallback).
    by_table: dict[tuple[str | None, str], list] = {}
    for index in snapshot.indexes:
        if index.table_name and index.index_ref:
            by_table.setdefault((index.schema_name, index.table_name), []).append(index)

    findings = []
    for (schema_name, table_name), indexes in by_table.items():
        # cada hallazgo necesita una query asociada (ver _detect_unused)
        queries = _queries_for_table(traffic, schema_name, table_name)
        if not queries:
            continue
        for narrow in indexes:
            narrow_method, narrow_columns, is_unique, narrow_is_partial = _parse_index_ref(narrow.index_ref)
            # sin columnas, o con una columna de expresion sin resolver (None):
            # no se puede comparar el prefijo con seguridad
            if not narrow_columns or any(column is None for column in narrow_columns):
                continue
            # un indice unico no es redundante (informe, AP-05): respalda una
            # restriccion de unicidad que el otro indice no garantiza
            if is_unique:
                continue

            wider = None
            wider_is_partial = False
            exact_duplicate = False
            for wide in indexes:
                if wide is narrow:
                    continue
                wide_method, wide_columns, wide_is_unique, wide_is_partial = _parse_index_ref(wide.index_ref)
                if wide_method != narrow_method:
                    continue
                if (
                    wide_columns == narrow_columns
                    # dos parciales con WHERE distintos no son duplicados, y el WHERE
                    # no se compara: solo cuentan duplicados exactos sin WHERE
                    and not narrow_is_partial
                    and not wide_is_partial
                    and _keeps_over(wide, wide_is_unique, narrow, is_unique)
                ):
                    wider = wide
                    exact_duplicate = True
                    break
                if (
                    len(wide_columns) > len(narrow_columns)
                    and wide_columns[: len(narrow_columns)] == narrow_columns
                ):
                    wider = wide
                    wider_is_partial = wide_is_partial
                    break
            if wider is None:
                continue

            chosen, _ = _pick_query(queries, narrow_columns[0])
            seen_in_use = narrow.index_name in used_index_names
            # el WHERE de un indice parcial no se verifica contra la query: solo se
            # avisa la ambiguedad, no se intenta resolver si de verdad aplica
            partial_risk = narrow_is_partial or wider_is_partial

            if exact_duplicate:
                explicacion = (
                    f"Este indice es un duplicado exacto de '{wider.index_name}' (mismas columnas, "
                    "mismo orden y mismo metodo): cualquier busqueda que resuelva la resuelve "
                    "tambien el otro, y las escrituras mantienen los dos."
                )
            else:
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
                        "tipo_redundancia": "duplicado_exacto" if exact_duplicate else "prefijo",
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
                        "queries_que_filtran_la_tabla": _queries_evidence(queries, narrow_columns[0]),
                    },
                    explicacion=explicacion,
                    recomendacion=(
                        f"Candidato a eliminar, conservando '{wider.index_name}'."
                        if exact_duplicate else
                        "Candidato a eliminar; validar antes que ninguna consulta dependa de su "
                        "tamaño mas chico (ej. index-only scans)."
                    ),
                    query_id=chosen["query_id"],
                    table_name=table_name,
                )
            )
    return findings


def detect_unused_indexes(
    snapshot: Snapshot,
    min_stats_window: timedelta = DEFAULT_MIN_STATS_WINDOW,
    history: dict[str, StatementHistory] | None = None,
) -> list[Hallazgo]:
    # Solo lee `history`: la fila nueva la guarda main.py despues de todos los detectores
    used_index_names = _used_index_names(snapshot)
    full_scan_columns_by_table, _ = _collect_query_traffic(snapshot)
    traffic = _collect_filter_traffic(snapshot, history or {}, full_scan_columns_by_table)

    findings = _detect_redundant(snapshot, used_index_names, traffic)

    if _stats_window_is_fresh(snapshot, min_stats_window):
        return findings  # "no usado" todavia no es confiable en este snapshot

    redundant_index_names = {
        (f.evidencia["schema_name"], f.evidencia["table_name"], f.evidencia["index_name"]) for f in findings
    }
    findings += _detect_unused(snapshot, used_index_names, traffic, redundant_index_names)
    return findings
