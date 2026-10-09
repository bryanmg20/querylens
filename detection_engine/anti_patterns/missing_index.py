import re

import sqlglot
from sqlglot import exp

from models import Hallazgo, Snapshot
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Regla AP-04, igual para ambos motores. Condiciones de escaneo completo
# copiadas de AP-02 (avoidable_full_scan.py) y deben quedar IDENTICAS a las
# suyas: si se cambia una alla, se cambia aca en el mismo cambio.
#   1. el plan tiene un full_table_scan
#   2. sobre una tabla con >= DEFAULT_MIN_LIVE_ROWS filas vivas
#   3. estimated_rows / live_rows <= DEFAULT_MAX_SELECTIVITY (sin estimacion, pasa)
# Condiciones propias de AP-04:
#   4. el predicado del scan compara una columna pelada de esa tabla contra un
#      valor, por igualdad (=, IN) o rango (<, <=, >, >=, BETWEEN, LIKE 'abc%'),
#      en una condicion unida por AND en el nivel superior
#   5. ninguna de esas columnas es la primera columna de un indice de la tabla
#
# Criterios de casos borde:
#   - condiciones de join (c = otra.c): no cuentan, el otro lado no es un valor
#   - columna dentro de una funcion o expresion (LOWER(c), c + 0): no cuenta,
#     no se arregla con un indice simple (es terreno de AP-03)
#   - indice parcial cuya primera columna es la filtrada: cuenta como existente
#   - indice de expresion: no cubre la columna base, no cuenta
#
# Limitacion: en MySQL, sin indice ni histograma sobre la columna, `filtered` es
# una constante (10 % para =, 33,33 % para <, 11,11 % para BETWEEN), asi que los
# rangos nunca pasan la condicion 3 aunque devuelvan pocas filas. Misma
# limitacion que AP-02; la mejora (filas reales de la ventana) es para los dos.

DEFAULT_MIN_LIVE_ROWS = 10_000  # mismo valor que AP-02
DEFAULT_MAX_SELECTIVITY = 0.1  # mismo valor que AP-02


# Misma limpieza que non_sargable_predicate.py; se duplica a proposito hasta
# confirmar este detector por separado.
_SUBPLAN_RE = re.compile(r"\((?:hashed\s+)?(?:SubPlan|InitPlan)\s+\d+\)")
_INTERNAL_MARKER_RE = re.compile(r"<[a-zA-Z_]+>\((?:[^<>()]|\([^<>()]*\))*\)")
_MAX_MARKER_PASSES = 10

_RANGE_TYPES = (exp.GT, exp.GTE, exp.LT, exp.LTE)


def _strip_engine_internal_markers(predicate: str) -> str:
    # Reemplaza por NULL las anotaciones internas del motor que no son SQL valido.
    cleaned = _SUBPLAN_RE.sub("NULL", predicate)
    previous = None
    passes = 0
    while previous != cleaned and passes < _MAX_MARKER_PASSES:
        previous = cleaned
        cleaned = _INTERNAL_MARKER_RE.sub("NULL", cleaned)
        passes += 1
    return cleaned


def _unwrap_paren(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _top_level_conjuncts(node: exp.Expression) -> list[exp.Expression]:
    # Condiciones unidas por AND en el nivel superior. Una rama de OR queda
    # como una sola condicion y no se descompone: un indice sobre una sola de
    # sus columnas no resuelve el OR.
    node = _unwrap_paren(node)
    if isinstance(node, exp.And):
        return _top_level_conjuncts(node.this) + _top_level_conjuncts(node.expression)
    return [node]


def _bare_column(side: exp.Expression) -> exp.Column | None:
    # La columna si el lado es una columna sin transformar. Postgres muestra
    # las columnas varchar como `(name)::text` en el Filter: es su propia
    # coercion a text, que no impide usar el indice, asi que se toma como pelada.
    side = _unwrap_paren(side)
    if isinstance(side, exp.Cast) and side.to.is_type(exp.DataType.Type.TEXT):
        side = _unwrap_paren(side.this)
    return side if isinstance(side, exp.Column) and side.name else None


def _is_value(side: exp.Expression | None) -> bool:
    # Un valor: literal, parametro, cast de literal, ANY('{..}'), etc. Todo lo
    # que no referencia columnas (si referencia otra columna es un join).
    return side is not None and side.find(exp.Column) is None


def _is_prefix_pattern(pattern: exp.Expression) -> bool:
    # LIKE 'abc%': el patron no empieza con comodin
    pattern = _unwrap_paren(pattern)
    if isinstance(pattern, exp.Cast):
        pattern = _unwrap_paren(pattern.this)
    return (
        isinstance(pattern, exp.Literal)
        and pattern.is_string
        and bool(pattern.this)
        and pattern.this[0] not in ("%", "_")
    )


def _filtered_columns(
    predicate: str,
    belongs_to_table,
    dialect: str = "postgres",
) -> tuple[list[str], list[str]]:
    # (columnas de igualdad, columnas de rango) del predicado real del scan,
    # en orden de aparicion y sin repetir. Una columna que aparece en los dos
    # queda solo como igualdad.
    try:
        tree = sqlglot.parse_one(_strip_engine_internal_markers(predicate), read=dialect)
    except Exception as e:
        logger.warning(
            "_filtered_columns | no se pudo parsear predicate | error=%s | predicate=%.200s",
            e, predicate,
        )
        return [], []

    equality: list[str] = []
    range_: list[str] = []

    def _add(target: list[str], column: exp.Column | None) -> None:
        if column is None or not belongs_to_table(column):
            return
        if column.name not in target:
            target.append(column.name)

    for condition in _top_level_conjuncts(tree):
        if isinstance(condition, (exp.EQ, *_RANGE_TYPES)):
            target = equality if isinstance(condition, exp.EQ) else range_
            left, right = condition.this, condition.expression
            if _bare_column(left) is not None and _is_value(right):
                _add(target, _bare_column(left))
            elif _bare_column(right) is not None and _is_value(left):
                _add(target, _bare_column(right))
        elif isinstance(condition, exp.In):
            if all(_is_value(item) for item in condition.expressions) and condition.expressions:
                _add(equality, _bare_column(condition.this))
        elif isinstance(condition, exp.Between):
            if _is_value(condition.args.get("low")) and _is_value(condition.args.get("high")):
                _add(range_, _bare_column(condition.this))
        elif isinstance(condition, exp.Like):
            if _is_prefix_pattern(condition.expression):
                _add(range_, _bare_column(condition.this))

    range_ = [column for column in range_ if column not in equality]
    return equality, range_


def _index_lead_column(index_ref: str | None, dialect: str = "postgres") -> str | None:
    # Primera columna del indice, o None si no se pudo leer o si es una
    # expresion (ej. lower(x)), que no cubre la columna base. Misma lectura que
    # non_sargable_predicate._index_lead_column; un indice parcial cuenta igual.
    if not index_ref:
        return None
    try:
        tree = sqlglot.parse_one(index_ref, read=dialect)
    except Exception as e:
        logger.warning(
            "_index_lead_column | no se pudo parsear index_ref | error=%s | texto=%.200s",
            e, index_ref,
        )
        return None

    index_node = tree.this if isinstance(tree, exp.Create) else tree
    params = index_node.args.get("params") if isinstance(index_node, exp.Index) else None
    columns = params.args.get("columns") if params else None
    if not columns:
        return None
    target = columns[0].this if isinstance(columns[0], exp.Ordered) else columns[0]
    return target.name if isinstance(target, exp.Column) else None


def _lookup_by_table(mapping: dict, schema_name: str | None, table_name: str):
    # (valor del match, es_ambiguo). Mismo criterio que AP-02/AP-03: clave
    # exacta con schema; el fallback por nombre solo si falta el schema de
    # algun lado, nunca entre dos schemas conocidos y distintos.
    exact = mapping.get((schema_name, table_name))
    if exact is not None:
        return exact, False
    candidates = [
        value for (sn, tn), value in mapping.items()
        if tn == table_name and (sn is None or schema_name is None)
    ]
    if len(candidates) == 1:
        return candidates[0], False
    return None, len(candidates) > 1


def _suggested_index(table_name: str, equality: list[str], range_: list[str]) -> str:
    columns = ", ".join(equality + range_)
    return f"CREATE INDEX ON {table_name} ({columns})"


# Detecta escaneos completos evitables cuyas columnas filtradas no tienen indice
def detect_missing_indexes(
    snapshot: Snapshot,
    min_live_rows: int = DEFAULT_MIN_LIVE_ROWS,
    max_selectivity: float = DEFAULT_MAX_SELECTIVITY,
) -> list[Hallazgo]:
    findings = []
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }
    # claves con schema para no confundir tablas homonimas de schemas distintos
    live_rows_by_table: dict[tuple[str | None, str], int] = {}
    for table in snapshot.tables:
        if table.table_name and table.live_rows is not None:
            live_rows_by_table[(table.schema_name, table.table_name)] = table.live_rows

    # tabla -> (primeras columnas de sus indices, nombres de sus indices)
    lead_columns_by_table: dict[tuple[str | None, str], set[str]] = {}
    index_names_by_table: dict[tuple[str | None, str], list[str]] = {}
    for index in snapshot.indexes:
        if not index.table_name:
            continue
        key = (index.schema_name, index.table_name)
        lead_columns_by_table.setdefault(key, set())
        if index.index_name:
            index_names_by_table.setdefault(key, []).append(index.index_name)
        lead_column = _index_lead_column(index.index_ref)
        if lead_column:
            lead_columns_by_table[key].add(lead_column)

    for candidate in snapshot.top_impact_queries:
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue

        alias_map = resolve_table_aliases(candidate.canonic_query)
        operations = explain.get("canonical_plan", {}).get("physical_operations", [])
        for operation in operations:
            # condicion 1 (AP-02)
            if operation.get("type") != "scan" or operation.get("access_method") != "full_table_scan":
                continue

            relation = operation.get("relation")
            real_table = alias_map.get(relation, relation)
            if not real_table:
                continue

            # condicion 2 (AP-02)
            live_rows, ambiguous = _lookup_by_table(live_rows_by_table, candidate.schema_name, real_table)
            if ambiguous or live_rows is None or live_rows < min_live_rows:
                continue

            # condicion 3 (AP-02)
            estimated_rows = operation.get("estimated_rows")
            selectivity = estimated_rows / live_rows if estimated_rows is not None else None
            if selectivity is not None and selectivity > max_selectivity:
                continue

            # condicion 4: columnas de esta tabla filtradas contra un valor
            predicate = operation.get("predicate")
            if not predicate:
                continue

            def _belongs_to_table(column: exp.Column) -> bool:
                # sin calificar es de la tabla del scan; calificada, por alias o nombre
                return not column.table or alias_map.get(column.table, column.table) == real_table

            equality, range_ = _filtered_columns(predicate, _belongs_to_table)
            if not equality and not range_:
                continue

            # condicion 5: ninguna es primera columna de un indice de la tabla
            lead_columns, ambiguous = _lookup_by_table(lead_columns_by_table, candidate.schema_name, real_table)
            if ambiguous:
                continue  # no se sabe de cual tabla son los indices
            lead_columns = lead_columns or set()
            if any(column in lead_columns for column in equality + range_):
                continue

            existing_indexes, _ = _lookup_by_table(index_names_by_table, candidate.schema_name, real_table)
            suggested = _suggested_index(real_table, equality, range_)
            findings.append(
                Hallazgo(
                    antipatron="missing_index",
                    severidad="medium",
                    evidencia={
                        "query_id": candidate.query_id,
                        "query_text": candidate.query_text,
                        "canonic_query": candidate.canonic_query,
                        "schema_name": candidate.schema_name,
                        "tables": sorted(set(alias_map.values())),
                        "relation": relation,
                        "predicate": predicate,
                        "equality_columns": equality,
                        "range_columns": range_,
                        "existing_indexes": sorted(existing_indexes or []),
                        "suggested_index": suggested,
                        "estimated_rows": estimated_rows,
                        "live_rows": live_rows,
                        "selectivity": selectivity,
                        "mean_time_ms": candidate.mean_time_ms,
                        "total_time_ms": candidate.total_time_ms,
                        "execution_count": candidate.execution_count,
                    },
                    explicacion=(
                        "La consulta recorre completa una tabla grande para devolver pocas filas, y "
                        "ninguna de las columnas por las que filtra es la primera columna de un "
                        "indice de esa tabla: el motor no tiene un indice con el que acotar la busqueda."
                    ),
                    recomendacion=(
                        f"Evaluar un indice con las columnas de igualdad primero y luego las de rango: "
                        f"{suggested}. Considerar el costo extra en escrituras antes de crearlo."
                    ),
                    query_id=candidate.query_id,
                    table_name=real_table,
                )
            )
    return findings
