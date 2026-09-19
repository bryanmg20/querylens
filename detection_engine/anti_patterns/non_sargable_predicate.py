import re

import sqlglot
from sqlglot import exp

from models import Hallazgo, Snapshot
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Referencias internas de Postgres a subplanes, no son SQL valido
_SUBPLAN_RE = re.compile(r"\((?:hashed\s+)?(?:SubPlan|InitPlan)\s+\d+\)")

# Marcadores internos de MySQL (<in_optimizer>, <cache>, etc.), tampoco SQL
# valido; se reemplaza el marcador completo por NULL, en loop por si vienen anidados
_INTERNAL_MARKER_RE = re.compile(r"<[a-zA-Z_]+>\((?:[^<>()]|\([^<>()]*\))*\)")
_MAX_MARKER_PASSES = 10


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


_COMPARISON_TYPES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)
_ARITHMETIC_TYPES = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.DPipe)

# Tipos cuya comparacion contra un literal numerico sin comillas fuerza a
# convertir la columna (no el literal) en la mayoria de los motores
_NON_NUMERIC_TYPES = {
    "character varying", "varchar", "character", "char", "text",
    "tinytext", "mediumtext", "longtext", "enum", "set", "uuid",
    "date", "time", "datetime", "timestamp",
    "timestamp without time zone", "timestamp with time zone",
    "year",
}

_SUBTIPO_MENSAJES = {
    "function_on_column": (
        "El predicado aplica una funcion sobre la columna (ej. LOWER, DATE, SUBSTRING) "
        "para compararla, por lo que el indice sobre esa columna no se puede usar "
        "salvo que exista un indice de expresion equivalente.",
        "Reescribir la condicion para dejar la columna sin transformar (ej. comparar contra "
        "un rango de fechas en vez de aplicar DATE()) o crear un indice de expresion que "
        "cubra la funcion.",
    ),
    "arithmetic_on_column": (
        "El predicado hace una operacion aritmetica sobre la columna (ej. columna + intervalo) "
        "en vez de aplicarla sobre la constante, lo que invalida el uso directo del indice.",
        "Mover la operacion al lado de la constante (ej. `fecha > now() - interval '1 day'` "
        "en vez de `fecha + interval '1 day' > now()`).",
    ),
    "leading_wildcard": (
        "El patron de LIKE empieza con un comodin ('%...'), lo que impide que un indice B-Tree "
        "acote el rango de busqueda por prefijo.",
        "Si la busqueda es siempre por sufijo, evaluar un indice invertido/trigram (ej. pg_trgm) "
        "o un indice funcional sobre el texto revertido; si es posible, reescribir la busqueda "
        "para que el comodin no sea inicial.",
    ),
    "implicit_cast": (
        "El predicado obliga a convertir el tipo de la columna para poder comparar (cast explicito "
        "sobre la columna en el plan, o una columna de texto/fecha comparada contra un literal "
        "numerico sin comillas), lo que impide el uso directo del indice.",
        "Igualar el tipo del literal al tipo declarado de la columna en la propia consulta, o "
        "revisar si la columna deberia tener el tipo correcto desde el modelo de datos.",
    ),
}


def _is_non_numeric_type(data_type: str | None) -> bool:
    return bool(data_type) and data_type.strip().lower() in _NON_NUMERIC_TYPES


def _has_column(node: exp.Expression) -> bool:
    return node.find(exp.Column) is not None


def _unwrap_paren(node: exp.Expression) -> exp.Expression:
    # Quita parentesis "de mas" (ej. `(salary + bonus) > 1000` llega como
    # Paren(Add(...))), para que los isinstance de abajo no fallen en silencio.
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _is_function_on_column(side: exp.Expression) -> bool:
    # exp.Cast es subclase de exp.Func; se excluye porque un cast se reporta
    # como implicit_cast, no function_on_column
    return isinstance(side, exp.Func) and not isinstance(side, exp.Cast) and _has_column(side)


def _is_arithmetic_on_column(side: exp.Expression) -> bool:
    return isinstance(side, _ARITHMETIC_TYPES) and _has_column(side)


def _is_cast_on_column(side: exp.Expression) -> bool:
    # side.this es lo de ADENTRO del cast: un cast sobre un literal (this=Literal) no cuenta
    return isinstance(side, exp.Cast) and _has_column(side.this)


def _bare_column_and_literal(comparison: exp.Expression) -> tuple[str, exp.Literal] | None:
    # Si es `columna OP literal` sin funcion/aritmetica/cast, devuelve (columna, literal).
    left, right = _unwrap_paren(comparison.this), _unwrap_paren(comparison.expression)
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
        return left.name, right
    if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
        return right.name, left
    return None


def _bare_column_and_literals_in(node: exp.In) -> tuple[str, list[exp.Literal]] | None:
    # Version de _bare_column_and_literal para `columna IN (lit1, lit2, ...)`.
    target = _unwrap_paren(node.this)
    if not isinstance(target, exp.Column):
        return None
    literals = [item for item in node.expressions if isinstance(item, exp.Literal)]
    return (target.name, literals) if literals else None


def _bare_column_and_literals_between(node: exp.Between) -> tuple[str, list[exp.Literal]] | None:
    # Version de _bare_column_and_literal para `columna BETWEEN lit1 AND lit2`.
    target = _unwrap_paren(node.this)
    if not isinstance(target, exp.Column):
        return None
    literals = [
        bound for bound in (node.args.get("low"), node.args.get("high"))
        if isinstance(bound, exp.Literal)
    ]
    return (target.name, literals) if literals else None


# Postgres y MySQL arman index_ref con el mismo formato de DDL sintetico,
# asi que un solo regex sirve para los dos: "... (col1, col2, ...)" al final
_INDEX_COLUMNS_RE = re.compile(r"\(([^)]+)\)\s*$")


def _index_lead_column(index_ref: str | None) -> str | None:
    # Primera columna del indice, o None si no se pudo leer.
    if not index_ref:
        return None
    match = _INDEX_COLUMNS_RE.search(index_ref)
    if not match:
        return None
    columns = [c.strip().strip('`"') for c in match.group(1).split(",")]
    return columns[0] if columns and columns[0] else None


def _index_exists_for_column(
    real_table: str | None,
    column_name: str | None,
    leading_columns_by_table: dict[str, set[str]],
) -> bool | None:
    # True/False si se pudo determinar, None si falta tabla o columna para decidir.
    if not real_table or not column_name:
        return None
    return column_name in leading_columns_by_table.get(real_table, set())


def _detect_structural_issues_in_query(
    canonic_query: str | None,
    dialect: str = "postgres",
) -> list[tuple[str, str | None, str | None, str]]:
    # Respaldo sin EXPLAIN: busca function/arithmetic_on_column en el WHERE y
    # en cada JOIN...ON. No detecta implicit_cast/leading_wildcard (necesitan el
    # literal real, que canonic_query reemplaza por $1/?).
    if not canonic_query:
        return []

    try:
        tree = sqlglot.parse_one(canonic_query, read=dialect)
    except Exception as e:
        logger.warning(
            "_detect_structural_issues_in_query | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return []

    # WHERE y cada ON de JOIN son igual de sargables/no sargables
    where = tree.find(exp.Where)
    join_conditions = [
        join.args["on"] for join in tree.find_all(exp.Join) if join.args.get("on") is not None
    ]
    roots = ([where.this] if where is not None else []) + join_conditions
    if not roots:
        return []

    alias_map = resolve_table_aliases(canonic_query, dialect)
    found: list[tuple[str, str | None, str | None, str]] = []

    for root in roots:
        for comparison in root.find_all(_COMPARISON_TYPES):
            left, right = _unwrap_paren(comparison.this), _unwrap_paren(comparison.expression)
            subtipo = None
            column_node = None

            for side in (left, right):
                if _is_function_on_column(side):
                    subtipo = "function_on_column"
                    column_node = side.find(exp.Column)
                    break
                if _is_arithmetic_on_column(side):
                    subtipo = "arithmetic_on_column"
                    column_node = side.find(exp.Column)
                    break

            if subtipo is None:
                continue

            column_name = column_node.name if column_node is not None else None
            real_table = (
                alias_map.get(column_node.table)
                if column_node is not None and column_node.table
                else None
            )
            found.append((subtipo, real_table, column_name, comparison.sql(dialect="postgres")))

    return found


def _detect_predicate_subtypes(
    predicate: str,
    real_table: str | None,
    column_types: dict[tuple[str, str], str],
    dialect: str = "postgres",
) -> list[tuple[str, str | None]]:
    # Detecta las 4 formas de predicado no sargable en el AST del predicado real del plan.
    try:
        tree = sqlglot.parse_one(_strip_engine_internal_markers(predicate), read=dialect)
    except Exception as e:
        # si esto se dispara seguido, hay un marcador interno nuevo que
        # _strip_engine_internal_markers todavia no reconoce
        logger.warning(
            "_detect_predicate_subtypes | no se pudo parsear predicate ni siquiera tras limpiar "
            "marcadores internos conocidos | error=%s | predicate=%.200s",
            e, predicate,
        )
        return []

    found: list[tuple[str, str | None]] = []

    def _add(subtipo: str, column_name: str | None) -> None:
        pair = (subtipo, column_name)
        if pair not in found:
            found.append(pair)

    # comparaciones binarias: =, <>, >, >=, <, <=, LIKE, ILIKE
    for comparison in tree.find_all(_COMPARISON_TYPES):
        left, right = _unwrap_paren(comparison.this), _unwrap_paren(comparison.expression)

        if isinstance(comparison, (exp.Like, exp.ILike)):
            if isinstance(right, exp.Literal) and right.is_string and right.this.startswith("%"):
                left_column = left.find(exp.Column)
                _add("leading_wildcard", left_column.name if left_column is not None else None)

        for side in (left, right):
            column_in_side = side.find(exp.Column)
            column_name = column_in_side.name if column_in_side is not None else None
            if _is_function_on_column(side):
                _add("function_on_column", column_name)
            if _is_arithmetic_on_column(side):
                _add("arithmetic_on_column", column_name)
            if _is_cast_on_column(side):
                _add("implicit_cast", column_name)

        if not any(subtipo == "implicit_cast" for subtipo, _ in found) and real_table is not None:
            # caso MySQL: la conversion no deja rastro en el AST, se infiere
            # cruzando el tipo real de la columna contra el literal
            bare = _bare_column_and_literal(comparison)
            if bare is not None:
                column_name, literal = bare
                is_non_numeric = _is_non_numeric_type(column_types.get((real_table, column_name)))
                if is_non_numeric and not literal.is_string:
                    _add("implicit_cast", column_name)

    # exp.In/exp.Between no entran en _COMPARISON_TYPES (su forma no es
    # this/expression), se revisan aparte
    for in_node in tree.find_all(exp.In):
        target = _unwrap_paren(in_node.this)
        target_column = target.find(exp.Column)
        target_column_name = target_column.name if target_column is not None else None

        if _is_function_on_column(target):
            _add("function_on_column", target_column_name)
        if _is_arithmetic_on_column(target):
            _add("arithmetic_on_column", target_column_name)
        if _is_cast_on_column(target):
            _add("implicit_cast", target_column_name)

        if not any(subtipo == "implicit_cast" for subtipo, _ in found) and real_table is not None:
            bare_in = _bare_column_and_literals_in(in_node)
            if bare_in is not None:
                column_name, literals = bare_in
                is_non_numeric = _is_non_numeric_type(column_types.get((real_table, column_name)))
                if is_non_numeric and any(not literal.is_string for literal in literals):
                    _add("implicit_cast", column_name)

    for between_node in tree.find_all(exp.Between):
        target = _unwrap_paren(between_node.this)
        target_column = target.find(exp.Column)
        target_column_name = target_column.name if target_column is not None else None

        if _is_function_on_column(target):
            _add("function_on_column", target_column_name)
        if _is_arithmetic_on_column(target):
            _add("arithmetic_on_column", target_column_name)
        if _is_cast_on_column(target):
            _add("implicit_cast", target_column_name)

        if not any(subtipo == "implicit_cast" for subtipo, _ in found) and real_table is not None:
            bare_between = _bare_column_and_literals_between(between_node)
            if bare_between is not None:
                column_name, literals = bare_between
                is_non_numeric = _is_non_numeric_type(column_types.get((real_table, column_name)))
                if is_non_numeric and any(not literal.is_string for literal in literals):
                    _add("implicit_cast", column_name)

    return found


def detect_non_sargable_predicates(snapshot: Snapshot) -> list[Hallazgo]:
    # Detecta predicados no sargables en top_impact_queries y cruza cada
    # hallazgo contra snapshot.indexes para avisar si ya existe un indice que
    # el predicado esta neutralizando, o si la columna aun no tiene ninguno.
    findings = []

    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }
    column_types = {
        (column.table_name, column.column_name): column.data_type
        for column in snapshot.columns
        if column.table_name and column.column_name
    }
    leading_columns_by_table: dict[str, set[str]] = {}
    for index in snapshot.indexes:
        lead_column = _index_lead_column(index.index_ref)
        if index.table_name and lead_column:
            leading_columns_by_table.setdefault(index.table_name, set()).add(lead_column)

    def _explicacion_con_indice(subtipo: str, index_exists: bool | None) -> tuple[str, str]:
        # avisa si la columna ya tiene indice (se neutraliza) o aun no tiene ninguno
        explicacion, recomendacion = _SUBTIPO_MENSAJES[subtipo]
        if index_exists is False:
            explicacion += (
                " Ademas, la columna no tiene hoy un indice que la cubra como columna "
                "principal: conviene corregir el predicado antes de crear uno, para no "
                "terminar con un indice igual de inutilizable."
            )
        return explicacion, recomendacion

    for candidate in snapshot.top_impact_queries:
        explain = explains_by_query_id.get(candidate.query_id)

        if explain is None:
            # sin EXPLAIN: respaldo por texto, solo 2 subtipos
            for subtipo, real_table, column_name, fragmento in _detect_structural_issues_in_query(
                candidate.canonic_query
            ):
                index_exists = _index_exists_for_column(real_table, column_name, leading_columns_by_table)
                explicacion, recomendacion = _explicacion_con_indice(subtipo, index_exists)
                findings.append(
                    Hallazgo(
                        antipatron="non_sargable_predicate",
                        severidad="medium",  # sin plan no sabemos si hay full table scan
                        evidencia={
                            "query_id": candidate.query_id,
                            "query_text": candidate.query_text,
                            "canonic_query": candidate.canonic_query,
                            "subtipo": subtipo,
                            "predicate": fragmento,
                            "relation": real_table,
                            "column": column_name,
                            "index_exists": index_exists,
                            "access_method": None,
                            "estimated_rows": None,
                            "fuente": "query_text",
                        },
                        explicacion=explicacion,
                        recomendacion=recomendacion,
                        query_id=candidate.query_id,
                        table_name=real_table,
                    )
                )
            continue

        # con EXPLAIN: predicado real del plan, los 4 subtipos
        alias_map = resolve_table_aliases(candidate.canonic_query)
        operations = explain.get("canonical_plan", {}).get("physical_operations", [])

        for operation in operations:
            if operation.get("type") != "scan":
                continue

            predicate = operation.get("predicate")
            if not predicate:
                continue

            relation = operation.get("relation")
            real_table = alias_map.get(relation, relation)
            subtipos = _detect_predicate_subtypes(predicate, real_table, column_types)

            for subtipo, column_name in subtipos:
                index_exists = _index_exists_for_column(real_table, column_name, leading_columns_by_table)
                explicacion, recomendacion = _explicacion_con_indice(subtipo, index_exists)
                findings.append(
                    Hallazgo(
                        antipatron="non_sargable_predicate",
                        severidad="high" if operation.get("access_method") == "full_table_scan" else "medium",
                        evidencia={
                            "query_id": candidate.query_id,
                            "query_text": candidate.query_text,
                            "canonic_query": candidate.canonic_query,
                            "subtipo": subtipo,
                            "predicate": predicate,
                            "relation": relation,
                            "column": column_name,
                            "index_exists": index_exists,
                            "access_method": operation.get("access_method"),
                            "estimated_rows": operation.get("estimated_rows"),
                            "fuente": "plan",
                        },
                        explicacion=explicacion,
                        recomendacion=recomendacion,
                        query_id=candidate.query_id,
                        table_name=real_table,
                    )
                )

    return findings
