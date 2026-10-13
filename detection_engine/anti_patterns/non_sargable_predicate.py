import re
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp
from querylens_contract import SnapshotPayload

from models import Hallazgo, StatementHistory
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Referencias a subplanes, no son SQL valido: (SubPlan N) de Postgres y de
# MySQL (el pipeline reemplaza ahi la subconsulta copiada por esa etiqueta) y
# (InitPlan N).colM de Postgres 17
_SUBPLAN_RE = re.compile(r"\((?:hashed\s+)?(?:SubPlan|InitPlan)\s+\d+\)(?:\.col\d+)?")

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

# Postgres escribe `(columna)::text` para comparar una columna varchar en casi
# cualquier condicion: es su propia coercion a text y no impide usar el indice.
# Un char(n) -> text si cuenta: ahi el cast cambia el valor (quita los blancos)
_VARCHAR_TYPES = {"character varying", "varchar"}

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


def _is_varchar_text_cast(side: exp.Expression, data_type: str | None) -> bool:
    # `(columna)::text` sobre una columna varchar: no es una conversion que
    # impida el indice (ver _VARCHAR_TYPES). Con tipo desconocido se asume
    # varchar (lo comun), igual que AP-04: si no, los dos detectores darian
    # diagnosticos contradictorios sobre el mismo predicado.
    return (
        isinstance(side, exp.Cast)
        and side.to.is_type(exp.DataType.Type.TEXT)
        and isinstance(_unwrap_paren(side.this), exp.Column)
        and (not data_type or data_type.strip().lower() in _VARCHAR_TYPES)
    )


def _value_is_string(node: exp.Expression | None) -> bool | None:
    # True si el valor es texto, False si es numero, None si no es un valor.
    # MySQL redacta los numeros como $n (sqlglot: Parameter) y los textos como
    # '$n' (Literal de texto); un literal sin redactar tambien se reconoce.
    if node is None:
        return None
    node = _unwrap_paren(node)
    if isinstance(node, exp.Neg):
        node = _unwrap_paren(node.this)
    if isinstance(node, exp.Literal):
        return node.is_string
    if isinstance(node, exp.Parameter):
        return False
    return None


def _bare_column_and_values(comparison: exp.Expression) -> tuple[exp.Column, list[bool]] | None:
    # Si es `columna OP valor` sin funcion/aritmetica/cast, devuelve (columna,
    # [el valor es texto]).
    left, right = _unwrap_paren(comparison.this), _unwrap_paren(comparison.expression)
    if isinstance(left, exp.Column) and _value_is_string(right) is not None:
        return left, [_value_is_string(right)]
    if isinstance(right, exp.Column) and _value_is_string(left) is not None:
        return right, [_value_is_string(left)]
    return None


def _bare_column_and_values_in(node: exp.In) -> tuple[exp.Column, list[bool]] | None:
    # Version de _bare_column_and_values para `columna IN (v1, v2, ...)`.
    target = _unwrap_paren(node.this)
    if not isinstance(target, exp.Column):
        return None
    kinds = [kind for kind in map(_value_is_string, node.expressions) if kind is not None]
    return (target, kinds) if kinds else None


def _bare_column_and_values_between(node: exp.Between) -> tuple[exp.Column, list[bool]] | None:
    # Version de _bare_column_and_values para `columna BETWEEN v1 AND v2`.
    target = _unwrap_paren(node.this)
    if not isinstance(target, exp.Column):
        return None
    kinds = [
        kind for kind in map(_value_is_string, (node.args.get("low"), node.args.get("high")))
        if kind is not None
    ]
    return (target, kinds) if kinds else None


def _index_lead_column(index_ref: str | None, dialect: str = "postgres") -> tuple[str | None, bool]:
    # (columna lider, es_parcial). Columna en None si no se pudo leer o si es una
    # expresion/opclass/etc (no una columna pelada). Parsea con sqlglot en vez
    # de un regex sobre el texto: un regex de "ultimo parentesis" confunde la
    # lista de columnas con WHERE (indice parcial) o INCLUDE, que pg_get_indexdef()
    # puede sumar al mismo texto de index_ref.
    if not index_ref:
        return None, False

    try:
        tree = sqlglot.parse_one(index_ref, read=dialect)
    except Exception as e:
        logger.warning(
            "_index_lead_column | no se pudo parsear index_ref | error=%s | texto=%.200s",
            e, index_ref,
        )
        return None, False

    index_node = tree.this if isinstance(tree, exp.Create) else tree
    params = index_node.args.get("params") if isinstance(index_node, exp.Index) else None
    columns = params.args.get("columns") if params else None
    if not columns:
        return None, False

    is_partial = params.args.get("where") is not None
    target = columns[0].this if isinstance(columns[0], exp.Ordered) else columns[0]
    lead_column = target.name if isinstance(target, exp.Column) else None
    return lead_column, is_partial


def _lookup_by_table(
    mapping: dict[tuple[str | None, str], set[str]],
    schema_name: str | None,
    table_name: str,
) -> tuple[set[str] | None, bool]:
    # (columnas del match, es_ambiguo). Intenta la clave exacta con schema; si
    # no hay, el fallback solo considera candidatos donde falta el dato de
    # schema de algun lado (None) -- nunca dos schemas conocidos y distintos,
    # ahi no hay ambiguedad, hay certeza de que son tablas distintas.
    exact = mapping.get((schema_name, table_name))
    if exact is not None:
        return exact, False
    candidates = [
        columns for (sn, tn), columns in mapping.items()
        if tn == table_name and (sn is None or schema_name is None)
    ]
    if len(candidates) == 1:
        return candidates[0], False
    return None, len(candidates) > 1


def _index_exists_for_column(
    real_table: str | None,
    column_name: str | None,
    schema_name: str | None,
    leading_columns_by_table: dict[tuple[str | None, str], set[str]],
) -> bool | None:
    # True/False si se pudo determinar, None si falta tabla/columna o el nombre
    # de tabla es ambiguo entre schemas y no se puede saber cual es.
    if not real_table or not column_name:
        return None
    columns, ambiguous = _lookup_by_table(leading_columns_by_table, schema_name, real_table)
    if ambiguous:
        return None
    return column_name in (columns or set())


def _only_partial_index_for_column(
    real_table: str | None,
    column_name: str | None,
    schema_name: str | None,
    leading_columns_by_table: dict[tuple[str | None, str], set[str]],
    full_leading_columns_by_table: dict[tuple[str | None, str], set[str]],
) -> bool:
    # True si la unica cobertura conocida de esta columna es via indice(s) parcial(es)
    # (ninguno completo) -- el WHERE del indice puede no cubrir a esta query igual.
    if not real_table or not column_name:
        return False
    columns, ambiguous = _lookup_by_table(leading_columns_by_table, schema_name, real_table)
    if ambiguous or column_name not in (columns or set()):
        return False
    full_columns, full_ambiguous = _lookup_by_table(full_leading_columns_by_table, schema_name, real_table)
    if full_ambiguous:
        return False  # no se puede confirmar que sea completo, mejor no afirmar "solo parcial"
    return column_name not in (full_columns or set())


def _detect_structural_issues_in_query(
    canonic_query: str | None,
    dialect: str = "postgres",
) -> list[tuple[str, str | None, str | None, str]]:
    # Respaldo sin EXPLAIN: busca function/arithmetic_on_column en el WHERE y
    # en cada JOIN...ON. No detecta implicit_cast (necesita el literal real, que
    # canonic_query reemplaza por $1/?).
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


def _column_type_for(
    column_types: dict[tuple[str | None, str, str], str],
    schema_name: str | None,
    table_name: str | None,
    column_name: str | None,
) -> str | None:
    # Mismo criterio que _lookup_by_table (ver ahi el detalle del fallback).
    if not table_name or not column_name:
        return None
    data_type = column_types.get((schema_name, table_name, column_name))
    if data_type is not None:
        return data_type
    candidates = [
        dt for (sn, tn, cn), dt in column_types.items()
        if tn == table_name and cn == column_name and (sn is None or schema_name is None)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _detect_predicate_subtypes(
    predicate: str,
    real_table: str | None,
    schema_name: str | None,
    column_types: dict[tuple[str | None, str, str], str],
    dialect: str = "postgres",
    engine: str = "postgres",
    belongs_to_table=None,
) -> list[tuple[str, str | None]]:
    # Detecta las 3 formas de predicado no sargable en el AST del predicado real del plan.
    # El LIKE con comodin inicial ('%abc') no se detecta: no esta en la regla del
    # informe y el pipeline redacta el patron a $n, asi que no se ve el '%'.
    # `engine` es el motor del plan ("postgres" o "mysql"), no el dialecto con
    # el que se parsea. `belongs_to_table(columna)` dice si una columna es de la
    # tabla del scan: las de otra tabla (condicion de join que MySQL pone en el
    # scan de la tabla interna, o que Postgres pone en el Index Cond de un join
    # parametrizado) no se atribuyen a esta tabla. Sin funcion, todas cuentan.
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

    def _belongs(column: exp.Column) -> bool:
        return belongs_to_table is None or belongs_to_table(column)

    def _type_of(column: exp.Column) -> str | None:
        return _column_type_for(column_types, schema_name, real_table, column.name)

    def _check_side(side: exp.Expression) -> None:
        # Un lado de la condicion: funcion, aritmetica o cast sobre una columna de esta tabla
        column = side.find(exp.Column)
        if column is None or not _belongs(column):
            return
        if _is_function_on_column(side):
            _add("function_on_column", column.name)
        if _is_arithmetic_on_column(side):
            _add("arithmetic_on_column", column.name)
        if _is_cast_on_column(side):
            if engine == "mysql" and side.to.is_type(exp.DataType.Type.DATE):
                # MySQL muestra DATE(col) como CAST(col AS DATE): es la funcion
                # que escribio el usuario, igual que date(col) en Postgres
                _add("function_on_column", column.name)
            elif not _is_varchar_text_cast(side, _type_of(column)):
                _add("implicit_cast", column.name)

    def _check_bare(bare: tuple[exp.Column, list[bool]] | None) -> None:
        # Caso MySQL: la conversion no deja rastro en el plan, se infiere
        # cruzando el tipo real de la columna contra el valor. Solo MySQL
        # redacta con tipo ($n numero, '$n' texto); en Postgres todo valor
        # llega como $n y se tomaria como numero.
        if engine != "mysql" or real_table is None or bare is None:
            return
        column, kinds = bare
        if not _belongs(column):
            return
        if _is_non_numeric_type(_type_of(column)) and any(not is_string for is_string in kinds):
            _add("implicit_cast", column.name)

    # comparaciones binarias: =, <>, >, >=, <, <=, LIKE, ILIKE
    for comparison in tree.find_all(_COMPARISON_TYPES):
        _check_side(_unwrap_paren(comparison.this))
        _check_side(_unwrap_paren(comparison.expression))
        _check_bare(_bare_column_and_values(comparison))

    # exp.In/exp.Between no entran en _COMPARISON_TYPES (su forma no es
    # this/expression), se revisan aparte
    for in_node in tree.find_all(exp.In):
        _check_side(_unwrap_paren(in_node.this))
        _check_bare(_bare_column_and_values_in(in_node))

    for between_node in tree.find_all(exp.Between):
        _check_side(_unwrap_paren(between_node.this))
        _check_bare(_bare_column_and_values_between(between_node))

    return found


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


def _current_calls(snapshot: SnapshotPayload) -> dict[str, tuple[int, str | None]]:
    # (ejecuciones, counters_epoch) por query, sumando filas repetidas igual que
    # la fila guardada en statement_samples
    calls_by_query: dict[str, tuple[int, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None:
            continue
        key = str(statement.query_id)
        calls, epoch = calls_by_query.get(key, (0, statement.counters_epoch))
        calls_by_query[key] = (calls + statement.execution_count, epoch)
    return calls_by_query


def _executed_since_previous_snapshot(
    calls: int,
    epoch: datetime | None,
    previous: StatementHistory | None,
) -> bool:
    # La query corrio entre el snapshot anterior y este? Sin fila anterior se
    # responde que si: es la primera vez que se la ve, no una repeticion.
    if previous is None:
        return True
    # contadores reiniciados: todas sus ejecuciones son posteriores al reinicio
    if epoch is not None and previous.counters_epoch is not None and epoch != previous.counters_epoch:
        return calls > 0
    delta_calls = calls - previous.execution_count
    if delta_calls < 0:
        # reinicio sin epoch (snapshot viejo): mismo criterio
        return calls > 0
    return delta_calls > 0


def detect_non_sargable_predicates(
    snapshot: SnapshotPayload,
    history: dict[str, StatementHistory] | None = None,
) -> list[Hallazgo]:
    # Detecta predicados no sargables en top_impact_queries y cruza cada
    # hallazgo contra snapshot.indexes para avisar si ya existe un indice que
    # el predicado esta neutralizando, o si la columna aun no tiene ninguno.
    # Solo lee `history`: la fila nueva la guarda main.py despues de todos los detectores.
    findings = []
    history = history or {}
    calls_by_query = _current_calls(snapshot)

    explains_by_query_id = {
        explain.query_id: explain
        for explain in snapshot.canonic_explains
        if explain.query_id is not None
    }
    # claves con schema para no confundir tablas homonimas de schemas distintos
    column_types: dict[tuple[str | None, str, str], str] = {
        (column.schema_name, column.table_name, column.column_name): column.data_type
        for column in snapshot.columns
        if column.table_name and column.column_name
    }
    leading_columns_by_table: dict[tuple[str | None, str], set[str]] = {}
    full_leading_columns_by_table: dict[tuple[str | None, str], set[str]] = {}
    for index in snapshot.indexes:
        lead_column, is_partial = _index_lead_column(index.index_ref)
        if not index.table_name or not lead_column:
            continue
        key = (index.schema_name, index.table_name)
        leading_columns_by_table.setdefault(key, set()).add(lead_column)
        if not is_partial:
            full_leading_columns_by_table.setdefault(key, set()).add(lead_column)

    def _explicacion_con_indice(subtipo: str, index_exists: bool | None, only_partial: bool) -> tuple[str, str]:
        # avisa si la columna ya tiene indice (se neutraliza) o aun no tiene ninguno.
        # index_exists solo reconoce indices sobre columnas peladas, no de expresion,
        # asi que el aviso de "no existe" queda con esa salvedad en vez de afirmarlo.
        explicacion, recomendacion = _SUBTIPO_MENSAJES[subtipo]
        if index_exists is False:
            explicacion += (
                " No se detecto un indice sobre la columna simple que la cubra como "
                "columna principal (esta deteccion no reconoce indices de expresion: si "
                "ya existe uno equivalente a este predicado, revisar eso antes que nada). "
                "Conviene corregir el predicado antes de crear un indice nuevo, para no "
                "terminar con uno igual de inutilizable."
            )
        elif index_exists and only_partial:
            explicacion += (
                " El unico indice que cubre esta columna como principal es parcial (tiene "
                "un WHERE): confirmar que esta consulta cae dentro de esa condicion antes "
                "de asumir que ya esta resuelto."
            )
        return explicacion, recomendacion

    for candidate in snapshot.top_impact_queries:
        # Solo si la query corrio desde el snapshot anterior: top_impact_queries
        # ordena por tiempo ACUMULADO, asi que una query que ya no se ejecuta
        # puede seguir ahi minutos u horas y se volveria a reportar en cada
        # snapshot. Vale tambien con plan: el pipeline le saca EXPLAIN a todos
        # los candidatos, se hayan ejecutado o no en el ciclo.
        query_id = str(candidate.query_id)
        calls, epoch_raw = calls_by_query.get(
            query_id, (candidate.execution_count or 0, candidate.counters_epoch)
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)
        if not _executed_since_previous_snapshot(calls, epoch, history.get(query_id)):
            continue

        explain = explains_by_query_id.get(candidate.query_id)

        if explain is None:
            # sin EXPLAIN: respaldo por texto, solo 2 subtipos
            tables = sorted(set(resolve_table_aliases(candidate.canonic_query).values()))
            for subtipo, real_table, column_name, fragmento in _detect_structural_issues_in_query(
                candidate.canonic_query
            ):
                index_exists = _index_exists_for_column(
                    real_table, column_name, candidate.schema_name, leading_columns_by_table
                )
                only_partial = _only_partial_index_for_column(
                    real_table, column_name, candidate.schema_name,
                    leading_columns_by_table, full_leading_columns_by_table,
                )
                explicacion, recomendacion = _explicacion_con_indice(subtipo, index_exists, only_partial)
                findings.append(
                    Hallazgo(
                        antipatron="non_sargable_predicate",
                        severidad="medium",  # sin plan no sabemos si hay full table scan
                        evidencia={
                            "query_id": candidate.query_id,
                            "query_text": candidate.query_text,
                            "canonic_query": candidate.canonic_query,
                            "schema_name": candidate.schema_name,
                            "tables": tables,
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

        # con EXPLAIN: predicado real del plan, los 3 subtipos
        alias_map = resolve_table_aliases(candidate.canonic_query)
        operations = explain.canonical_plan.physical_operations
        # Postgres entrega queryid como entero y MySQL el DIGEST como texto (igual que AP-07)
        engine = "mysql" if isinstance(candidate.query_id, str) else "postgres"

        for operation in operations:
            if operation.type != "scan":
                continue

            predicate = operation.predicate
            if not predicate:
                continue

            relation = operation.relation
            # table_name es la tabla real en Postgres (relation puede ser un
            # nombre generado, ej. ql_pt_orders_1); en MySQL llega en None
            real_table = operation.table_name or alias_map.get(relation, relation)

            def _belongs_to_table(column: exp.Column, relation=relation, real_table=real_table) -> bool:
                # sin calificar es de la tabla del scan; calificada, por su alias
                if not column.table or column.table == relation:
                    return True
                # el scan tiene un alias de la query: otro calificador es otra
                # lectura, aunque sea de la misma tabla (self join)
                if relation in alias_map:
                    return False
                # alias que no esta en la query (ej. ql_pt_orders_1 de Postgres): por tabla real
                return alias_map.get(column.table, column.table) == real_table

            subtipos = _detect_predicate_subtypes(
                predicate, real_table, candidate.schema_name, column_types,
                engine=engine, belongs_to_table=_belongs_to_table,
            )

            for subtipo, column_name in subtipos:
                index_exists = _index_exists_for_column(
                    real_table, column_name, candidate.schema_name, leading_columns_by_table
                )
                only_partial = _only_partial_index_for_column(
                    real_table, column_name, candidate.schema_name,
                    leading_columns_by_table, full_leading_columns_by_table,
                )
                explicacion, recomendacion = _explicacion_con_indice(subtipo, index_exists, only_partial)
                findings.append(
                    Hallazgo(
                        antipatron="non_sargable_predicate",
                        severidad="high" if operation.access_method == "full_table_scan" else "medium",
                        evidencia={
                            "query_id": candidate.query_id,
                            "query_text": candidate.query_text,
                            "canonic_query": candidate.canonic_query,
                            "schema_name": candidate.schema_name,
                            "tables": sorted(set(alias_map.values())),
                            "subtipo": subtipo,
                            "predicate": predicate,
                            "relation": relation,
                            "column": column_name,
                            "index_exists": index_exists,
                            "access_method": operation.access_method,
                            "estimated_rows": operation.estimated_rows,
                            "fuente": "plan",
                        },
                        explicacion=explicacion,
                        recomendacion=recomendacion,
                        query_id=candidate.query_id,
                        table_name=real_table,
                    )
                )

    return findings
