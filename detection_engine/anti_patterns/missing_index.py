import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp
from querylens_contract import SnapshotPayload

from models import Hallazgo, StatementHistory
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Regla AP-04 (indice ausente), igual para ambos motores. Condiciones de
# escaneo completo identicas a las de AP-02 (escaneo completo evitable,
# avoidable_full_scan.py), copiadas aca porque los detectores son independientes:
#   1. la consulta es un SELECT simple (ver _simple_select_table)
#   2. >= DEFAULT_MIN_WINDOW_CALLS ejecuciones en la ventana (delta desde el
#      snapshot anterior)
#   3. el plan recorre completa la tabla del SELECT (full_table_scan) o un
#      indice entero sin usarlo para filtrar (full_index_scan)
#   4. la tabla tiene >= DEFAULT_MIN_LIVE_ROWS filas vivas
#   5. selectividad REAL de la ventana <= DEFAULT_MAX_SELECTIVITY:
#      (filas devueltas / ejecuciones de la ventana) / live_rows
# La selectividad estimada del plan no sirve aca: Postgres con plan generico no
# conoce el valor, y MySQL, sin indice ni histograma en la columna (justo el
# caso de AP-04), usa constantes (10 % para =, 11,11 % para BETWEEN, 33 % para
# >). En un SELECT simple cada fila devuelta paso el filtro, por eso la real si.
# Condiciones propias de AP-04 (numeradas como antes para no perder la referencia):
#   6. el predicado del scan compara una columna pelada de esa tabla contra un
#      valor, por igualdad (=, IN, IS NULL) o rango (<, <=, >, >=, BETWEEN), en
#      una condicion unida por AND en el nivel superior. IS NOT NULL no cuenta:
#      suele devolver casi toda la tabla y un indice no ayuda
#   7. ninguna de esas columnas es la primera columna de un indice de la tabla
#
# Criterios de casos borde:
#   - condiciones de join (c = otra.c): no cuentan, el otro lado no es un valor
#   - columna dentro de una funcion o expresion (LOWER(c), c + 0): no cuenta,
#     no se arregla con un indice simple (es terreno de AP-03)
#   - indice parcial cuya primera columna es la filtrada: cuenta como existente
#   - indice de expresion: no cubre la columna base, no cuenta
#   - conversion de tipo sobre la columna: no cuenta, el indice no se podria
#     usar (el problema es el tipo del valor, terreno de AP-03):
#       * Postgres la escribe en el plan: un cast que no sea a text descarta la
#         columna; (col)::text solo cuenta si la columna es varchar (es la
#         coercion de Postgres, el indice se usa), no si es char(n) u otro tipo
#       * MySQL no la escribe: se deduce con el tipo de la columna y el valor
#         redactado ($n numero, '$n' texto). Texto/fecha contra numero descarta
#     Si no se puede comprobar (plan generico de Postgres, que no conoce el
#     valor, o tipo de columna desconocido) la columna cuenta igual y el
#     hallazgo sale con conversion_verificada = False
#   - full_index_scan: aparece cuando un indice contiene la columna filtrada
#     pero no empieza por ella (ej. indice (status, total) con WHERE total = ?);
#     un indice que empiece por esa columna si acotaria la busqueda
#
# Limitaciones declaradas (las mismas de AP-02):
#   - consultas que no son SELECT simples no se evaluan (joins, subconsultas,
#     UNION, agregados, LIMIT): falso negativo por diseno
#   - el promedio de la ventana esconde la minoria selectiva con datos sesgados
#   - tabla particionada: el plan recorre las particiones, no la tabla del SELECT
#   - LIKE no cuenta en ningun motor: la redaccion del pipeline borra el patron
#     (Postgres `~~ $1`, MySQL `LIKE '$1'`), asi que no se sabe si empieza con
#     comodin ('%abc', donde un indice no sirve) o con texto fijo ('abc%').
#     Pendiente en el pipeline: redactar conservando los comodines de los
#     extremos ('%$1', '$1%'); con eso se puede volver a contar LIKE cuando el
#     patron no empieza con % o _, y en Postgres recomendar text_pattern_ops
#     (con una collation distinta de C el indice normal no sirve para LIKE).

DEFAULT_MIN_LIVE_ROWS = 10_000  # mismo valor que AP-02
DEFAULT_MAX_SELECTIVITY = 0.1  # mismo valor que AP-02
DEFAULT_MIN_WINDOW_CALLS = 10  # mismo valor que AP-02

# Igual que AP-02: full_index_scan en un SELECT simple pasa por todas las filas
# (sin LIMIT, MIN/MAX ni EXISTS nada corta la lectura antes)
_FULL_SCAN_METHODS = ("full_table_scan", "full_index_scan")

# Igual que AP-02: una query sin fila guardada cuyo counters_epoch esta a menos
# de esto de la hora del snapshot empezo a contar dentro de la ventana. Asume
# snapshots cada 60 s (ventana del informe).
_NEW_QUERY_WINDOW = timedelta(seconds=60)


# Misma limpieza que non_sargable_predicate.py; se duplica a proposito hasta
# confirmar este detector por separado.
_SUBPLAN_RE = re.compile(r"\((?:hashed\s+)?(?:SubPlan|InitPlan)\s+\d+\)(?:\.col\d+)?")
_INTERNAL_MARKER_RE = re.compile(r"<[a-zA-Z_]+>\((?:[^<>()]|\([^<>()]*\))*\)")
_MAX_MARKER_PASSES = 10

_RANGE_TYPES = (exp.GT, exp.GTE, exp.LT, exp.LTE)

# Mismas listas que non_sargable_predicate.py (AP-03), duplicadas a proposito.
# Tipos cuya comparacion contra un numero sin comillas obliga a MySQL a
# convertir la columna (no el valor)
_NON_NUMERIC_TYPES = {
    "character varying", "varchar", "character", "char", "text",
    "tinytext", "mediumtext", "longtext", "enum", "set", "uuid",
    "date", "time", "datetime", "timestamp",
    "timestamp without time zone", "timestamp with time zone",
    "year",
}
# Postgres escribe (columna)::text para comparar una columna varchar: es su
# propia coercion y no impide usar el indice. Con char(n) si lo impide.
_VARCHAR_TYPES = {"character varying", "varchar"}


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


def _value_is_string(node: exp.Expression | None) -> bool | None:
    # True si el valor es texto, False si es numero, None si no se sabe. MySQL
    # redacta los numeros como $n (sqlglot: Parameter) y los textos como '$n'.
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


def _filtered_columns(
    predicate: str,
    belongs_to_table,
    column_type_of=None,
    engine: str = "postgres",
    dialect: str = "postgres",
) -> tuple[list[str], list[str], list[str]]:
    # (columnas de igualdad, columnas de rango, columnas sin verificar) del
    # predicado real del scan, en orden de aparicion y sin repetir. Una columna
    # que aparece en los dos queda solo como igualdad. `column_type_of(nombre)`
    # da el tipo de una columna de la tabla del scan (None si no se sabe);
    # `engine` es el motor del plan ("postgres" o "mysql"). Las columnas con
    # conversion de tipo se descartan; "sin verificar" son las que cuentan pero
    # cuya conversion no se pudo descartar porque falta su tipo.
    try:
        tree = sqlglot.parse_one(_strip_engine_internal_markers(predicate), read=dialect)
    except Exception as e:
        logger.warning(
            "_filtered_columns | no se pudo parsear predicate | error=%s | predicate=%.200s",
            e, predicate,
        )
        return [], [], []

    equality: list[str] = []
    range_: list[str] = []
    unverified: list[str] = []

    def _add(target: list[str], side: exp.Expression, values: list[exp.Expression]) -> None:
        column = _bare_column(side)
        if column is None or not belongs_to_table(column):
            return
        data_type = column_type_of(column.name) if column_type_of else None
        data_type = data_type.strip().lower() if data_type else None
        side = _unwrap_paren(side)
        if isinstance(side, exp.Cast):
            # Postgres: (col)::text, unico cast que _bare_column deja pasar
            if data_type is not None and data_type not in _VARCHAR_TYPES:
                return
        elif engine == "mysql" and data_type in _NON_NUMERIC_TYPES:
            if any(_value_is_string(value) is False for value in values):
                return
        # sin valores (IS NULL) no hay conversion posible que verificar
        if data_type is None and (isinstance(side, exp.Cast) or (engine == "mysql" and values)):
            if column.name not in unverified:
                unverified.append(column.name)
        if column.name not in target:
            target.append(column.name)

    for condition in _top_level_conjuncts(tree):
        if isinstance(condition, (exp.EQ, *_RANGE_TYPES)):
            target = equality if isinstance(condition, exp.EQ) else range_
            left, right = condition.this, condition.expression
            if _bare_column(left) is not None and _is_value(right):
                _add(target, left, [right])
            elif _bare_column(right) is not None and _is_value(left):
                _add(target, right, [left])
        elif isinstance(condition, exp.In):
            if all(_is_value(item) for item in condition.expressions) and condition.expressions:
                _add(equality, condition.this, condition.expressions)
        elif isinstance(condition, exp.Between):
            low, high = condition.args.get("low"), condition.args.get("high")
            if _is_value(low) and _is_value(high):
                _add(range_, condition.this, [low, high])
        elif isinstance(condition, exp.Is) and not condition.args.get("negate"):
            # col IS NULL: el indice guarda los NULL y los busca como una igualdad
            if isinstance(_unwrap_paren(condition.expression), exp.Null):
                _add(equality, condition.this, [])
        # LIKE no cuenta: el patron llega redactado (ver limitaciones arriba)

    range_ = [column for column in range_ if column not in equality]
    unverified = [column for column in unverified if column in equality or column in range_]
    return equality, range_, unverified


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


# Base de escaneo completo: misma logica que avoidable_full_scan.py (AP-02); se
# duplica a proposito porque los detectores son independientes y hasta
# confirmar este detector por separado.

@dataclass
class _RowsWindow:
    calls: int
    rows: int
    # True si la query aparecio en esta ventana y se uso su acumulado completo
    new_query: bool = False


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


def _current_counters(snapshot: SnapshotPayload) -> dict[str, tuple[int, int | None, str | None]]:
    # (ejecuciones, filas devueltas, counters_epoch) por query, sumando las filas
    # repetidas igual que la fila guardada en statement_samples
    counters: dict[str, tuple[int, int | None, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None:
            continue
        key = str(statement.query_id)
        calls, rows, epoch = counters.get(key, (0, None, statement.counters_epoch))
        if statement.rows_returned is not None:
            rows = (rows or 0) + statement.rows_returned
        counters[key] = (calls + statement.execution_count, rows, epoch)
    return counters


def _rows_window(
    calls: int,
    rows: int | None,
    epoch: datetime | None,
    previous: StatementHistory | None,
    previous_snapshot_at: datetime | None,
    captured_at: datetime,
) -> _RowsWindow | None:
    # Ejecuciones y filas devueltas entre el snapshot anterior y este. None si
    # no se puede medir o si la query no se ejecuto en la ventana.
    if rows is None:
        return None

    if previous is None:
        # sin fila anterior: solo se mide si la query empezo a contar dentro de
        # esta ventana (o despues del snapshot anterior); si no, se evalua en el
        # proximo snapshot, cuando ya hay contra que restar
        if epoch is None or calls <= 0:
            return None
        started_in_window = captured_at - epoch <= _NEW_QUERY_WINDOW
        started_after_previous = previous_snapshot_at is not None and epoch > previous_snapshot_at
        if started_in_window or started_after_previous:
            return _RowsWindow(calls=calls, rows=rows, new_query=True)
        return None
    # fila guardada antes de que existiera esta columna: aun no hay con que restar
    if previous.rows_returned is None:
        return None
    # contadores reiniciados (reinicio, reset de estadisticas, entrada desalojada)
    if epoch is not None and previous.counters_epoch is not None and epoch != previous.counters_epoch:
        return None
    delta_calls = calls - previous.execution_count
    delta_rows = rows - previous.rows_returned
    if delta_calls <= 0 or delta_rows < 0:
        return None
    return _RowsWindow(calls=delta_calls, rows=delta_rows)


def _simple_select_table(canonic_query: str | None) -> str | None:
    # Nombre de la unica tabla si la consulta es un SELECT simple, o None si no
    # lo es. SELECT simple: una sola sentencia SELECT (sin UNION ni WITH), una
    # sola tabla en el FROM, sin JOIN, sin subconsultas en ninguna parte, sin
    # LIMIT/OFFSET/FETCH, sin GROUP BY/HAVING/DISTINCT y sin agregados.
    # Las funciones de ventana se aceptan: no cambian la cantidad de filas.
    # canonic_query siempre viene en dialecto postgres.
    if not canonic_query:
        return None
    try:
        select = sqlglot.parse_one(canonic_query, read="postgres")
    except Exception as e:
        logger.warning(
            "missing_index | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return None

    if not isinstance(select, exp.Select):
        return None
    # cualquier otro SELECT adentro es una subconsulta (WHERE, SELECT, FROM)
    if any(node is not select for node in select.find_all(exp.Select)):
        return None
    if select.find(exp.With, exp.Join, exp.Limit, exp.Offset, exp.Fetch, exp.Group, exp.Having, exp.Distinct):
        return None
    for aggregate in select.find_all(exp.AggFunc):
        if aggregate.find_ancestor(exp.Window) is None:
            return None

    tables = list(select.find_all(exp.Table))
    if len(tables) != 1 or not tables[0].name:
        return None
    return tables[0].name


def _column_type_for(
    column_types: dict[tuple[str | None, str, str], str],
    schema_name: str | None,
    table_name: str | None,
    column_name: str | None,
) -> str | None:
    # Mismo criterio de schema que _lookup_by_table; misma funcion que AP-03.
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


def _suggested_index(table_name: str, equality: list[str], range_: list[str]) -> str:
    columns = ", ".join(equality + range_)
    return f"CREATE INDEX ON {table_name} ({columns})"


# Detecta SELECT simples que recorren completa una tabla grande para devolver
# pocas filas, filtrando por columnas que no son la primera de ningun indice
def detect_missing_indexes(
    snapshot: SnapshotPayload,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
    min_live_rows: int = DEFAULT_MIN_LIVE_ROWS,
    max_selectivity: float = DEFAULT_MAX_SELECTIVITY,
    min_window_calls: int = DEFAULT_MIN_WINDOW_CALLS,
) -> list[Hallazgo]:
    # Solo lee `history`: la fila nueva la guarda main.py despues de todos los detectores
    history = history or {}
    captured_at = captured_at or datetime.now(timezone.utc)
    # el snapshot anterior mas reciente de esta base, para reconocer queries nuevas
    previous_snapshot_at = max((row.captured_at for row in history.values()), default=None)
    counters = _current_counters(snapshot)
    findings = []
    explains_by_query_id = {
        explain.query_id: explain
        for explain in snapshot.canonic_explains
        if explain.query_id is not None
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

    # tipo de cada columna, para descartar las comparaciones con conversion
    column_types: dict[tuple[str | None, str, str], str] = {
        (column.schema_name, column.table_name, column.column_name): column.data_type
        for column in snapshot.columns
        if column.table_name and column.column_name
    }

    for candidate in snapshot.top_impact_queries:
        if candidate.query_id is None:
            continue
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue

        # condicion 1 (AP-02)
        table_name = _simple_select_table(candidate.canonic_query)
        if table_name is None:
            continue

        # condicion 2 (AP-02)
        query_id = str(candidate.query_id)
        # contadores desde statements (suma de filas repetidas); si el candidato
        # no aparece ahi, se usan los suyos
        calls, rows, epoch_raw = counters.get(
            query_id,
            (candidate.execution_count or 0, candidate.rows_returned, candidate.counters_epoch),
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)
        window = _rows_window(calls, rows, epoch, history.get(query_id), previous_snapshot_at, captured_at)
        if window is None or window.calls < min_window_calls:
            continue

        alias_map = resolve_table_aliases(candidate.canonic_query)
        operations = explain.canonical_plan.physical_operations
        # Postgres entrega queryid como entero y MySQL el DIGEST como texto (igual que AP-07)
        engine = "mysql" if isinstance(candidate.query_id, str) else "postgres"
        for operation in operations:
            # condicion 3 (AP-02): escaneo completo sobre la tabla del SELECT. Un
            # plan bitmap de Postgres da dos scans de la tabla, pero son index_lookup
            if operation.type != "scan" or operation.access_method not in _FULL_SCAN_METHODS:
                continue
            relation = operation.relation
            # table_name: tabla real en Postgres (relation puede ser ql_pt_orders_1); None en MySQL
            real_table = operation.table_name or alias_map.get(relation, relation)
            if real_table != table_name:
                continue

            # condicion 4 (AP-02)
            live_rows, ambiguous = _lookup_by_table(live_rows_by_table, candidate.schema_name, real_table)
            if ambiguous or live_rows is None or live_rows < min_live_rows:
                continue

            # condicion 5 (AP-02): selectividad real de la ventana
            rows_per_call = window.rows / window.calls
            selectivity = rows_per_call / live_rows
            if selectivity > max_selectivity:
                continue

            # condicion 6: columnas de esta tabla filtradas contra un valor
            predicate = operation.predicate
            if not predicate:
                continue

            def _belongs_to_table(column: exp.Column, relation=relation, real_table=real_table) -> bool:
                # sin calificar es de la tabla del scan; calificada, por su alias.
                # Descarta las condiciones de join que MySQL pone en el scan de la tabla interna
                if not column.table or column.table == relation:
                    return True
                # el scan tiene un alias de la query: otro calificador es otra
                # lectura, aunque sea de la misma tabla (self join)
                if relation in alias_map:
                    return False
                # alias que no esta en la query (ej. ql_pt_orders_1 de Postgres): por tabla real
                return alias_map.get(column.table, column.table) == real_table

            def _type_of(column_name: str, real_table=real_table) -> str | None:
                return _column_type_for(column_types, candidate.schema_name, real_table, column_name)

            equality, range_, unverified = _filtered_columns(
                predicate, _belongs_to_table, _type_of, engine,
            )
            if not equality and not range_:
                continue

            # Si se pudo descartar una conversion de tipo sobre las columnas. El
            # plan generico de Postgres no conoce el valor, asi que no muestra
            # la conversion que provocaria (customer_id = 5.0 -> ::numeric)
            unverified_reasons = []
            if explain.explain_source == "generic":
                unverified_reasons.append("plan_generico")
            if unverified:
                unverified_reasons.append("tipo_de_columna_desconocido")

            # condicion 7: ninguna es primera columna de un indice de la tabla
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
                        "tables": [table_name],
                        "relation": relation,
                        "access_method": operation.access_method,
                        "index_name": operation.index_name,
                        "predicate": predicate,
                        "equality_columns": equality,
                        "range_columns": range_,
                        "existing_indexes": sorted(existing_indexes or []),
                        "suggested_index": suggested,
                        "live_rows": live_rows,
                        "rows_per_call": round(rows_per_call, 2),
                        "selectivity": selectivity,
                        # solo informativo: la regla usa las filas reales
                        "estimated_rows": operation.estimated_rows,
                        "window_calls": window.calls,
                        "window_rows": window.rows,
                        "query_new_in_window": window.new_query,
                        "explain_source": explain.explain_source,
                        # False si no se pudo descartar una conversion de tipo
                        # sobre la columna (con conversion el indice no se usaria)
                        "conversion_verificada": not unverified_reasons,
                        "conversion_sin_verificar_por": unverified_reasons,
                        "columnas_sin_tipo": unverified,
                        "mean_time_ms": candidate.mean_time_ms,
                        "total_time_ms": candidate.total_time_ms,
                        "execution_count": candidate.execution_count,
                    },
                    explicacion=(
                        f"La consulta recorre completa la tabla {table_name} ({live_rows:,} filas vivas) "
                        f"y en la ultima ventana devolvio en promedio {rows_per_call:,.0f} filas por "
                        f"ejecucion ({selectivity:.2%} de la tabla) en {window.calls} ejecuciones. "
                        "Ninguna de las columnas por las que filtra es la primera columna de un "
                        "indice de esa tabla: el motor no tiene un indice con el que acotar la busqueda."
                        + (
                            " No se pudo descartar que la consulta convierta el tipo de la columna "
                            "(plan sin los valores reales o tipo de columna desconocido): si el valor "
                            "se envia con otro tipo, el indice no se usaria."
                            if unverified_reasons else ""
                        )
                    ),
                    recomendacion=(
                        f"Evaluar un indice con las columnas de igualdad primero y luego las de rango: "
                        f"{suggested}. Considerar el costo extra en escrituras antes de crearlo."
                    ),
                    query_id=candidate.query_id,
                    table_name=real_table,
                )
            )
            # un SELECT simple tiene un solo escaneo completo de su tabla
            break
    return findings
