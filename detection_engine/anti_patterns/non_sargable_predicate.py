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


# Regla AP-03 (predicado no sargable). Condiciones de escaneo completo
# identicas a las de AP-02 (escaneo completo evitable, avoidable_full_scan.py),
# copiadas aca porque los detectores son independientes:
#   1. la consulta es un SELECT simple (ver _simple_select_table)
#   2. >= DEFAULT_MIN_WINDOW_CALLS ejecuciones en la ventana (delta desde el
#      snapshot anterior)
#   3. el plan recorre completa la tabla del SELECT (full_table_scan) o un
#      indice entero sin usarlo para filtrar (full_index_scan)
#   4. la tabla tiene >= DEFAULT_MIN_LIVE_ROWS filas vivas
#   5. selectividad REAL de la ventana <= DEFAULT_MAX_SELECTIVITY:
#      (filas devueltas / ejecuciones de la ventana) / live_rows
# Condiciones propias de AP-03:
#   6. el predicado de ese scan tiene una columna de la tabla envuelta en una
#      funcion, una operacion aritmetica o una conversion de tipo, comparada
#      contra un valor con un operador que un indice puede buscar (=, <, <=,
#      >, >=, IN, BETWEEN, IS NULL)
#   7. esa columna es la primera columna de un indice existente: el predicado
#      lo desaprovecha. Sin indice el escaneo completo es inevitable con o sin
#      la funcion, y ese caso ya lo reporta AP-02. Un indice parcial cuenta
#      (se avisa en la explicacion); uno de expresion no cubre la columna base.
#      Si no se puede saber (tabla ambigua entre schemas) no se reporta.
#   8. reescribir las columnas de 6-7 deja que el motor use un indice: un AND
#      usa indice si alguna de sus partes puede; un OR solo si todas sus ramas
#      pueden (medido en los dos motores, ql_sandbox/explain_or). No se reporta
#      si el predicado ya puede usar un indice, ni si otra rama del OR no
#      tiene indice (ese arreglo es de AP-04 y ninguno de los dos alcanza solo;
#      queda para AP-02).
#
# Limitaciones declaradas (las mismas de AP-02):
#   - consultas que no son SELECT simples no se evaluan (joins, subconsultas,
#     UNION, agregados, LIMIT): falso negativo por diseno
#   - sin plan la consulta no se evalua
#   - el promedio de la ventana esconde la minoria selectiva con datos sesgados

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


_ARITHMETIC_TYPES = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.DPipe)

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


def _lookup_by_table(mapping: dict, schema_name: str | None, table_name: str):
    # (valor del match, es_ambiguo). Intenta la clave exacta con schema; si
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


_INDEXABLE_COMPARISONS = (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _is_value(node: exp.Expression | None) -> bool:
    # Un valor: literal, parametro, cast de literal, ANY(..). Lo que referencia
    # otra columna no lo es: columna contra columna (total > id) compara dos
    # valores de la misma fila y ningun indice sirve, con o sin cast.
    return node is not None and node.find(exp.Column) is None


def _wrapped_subtype(side: exp.Expression) -> str | None:
    if _is_function_on_column(side):
        return "function_on_column"
    if _is_arithmetic_on_column(side):
        return "arithmetic_on_column"
    if _is_cast_on_column(side):
        return "implicit_cast"
    return None


def _leaf(node: exp.Expression, lead_columns: set[str] | None) -> tuple:
    # Una condicion: ("leaf", estado, subtipo, columna). Estados:
    #   OK      columna pelada que encabeza un indice: el motor ya puede usarlo
    #   FIX     columna envuelta que encabeza un indice: reescribirla lo
    #           habilita (es lo que reporta AP-03)
    #   OTRA    nada que un indice resuelva (sin indice, LIKE, <>, IS NOT
    #           NULL, NOT, columna contra columna...)
    # Solo cuentan los operadores que un indice puede buscar: =, <, <=, >, >=,
    # IN, BETWEEN e IS NULL. lead_columns None: todas las columnas encabezan un
    # indice.
    side = None
    if isinstance(node, _INDEXABLE_COMPARISONS):
        left, right = _unwrap_paren(node.this), _unwrap_paren(node.expression)
        side = left if _is_value(right) else right if _is_value(left) else None
    elif isinstance(node, exp.In):
        if node.expressions and all(_is_value(item) for item in node.expressions):
            side = _unwrap_paren(node.this)
    elif isinstance(node, exp.Between):
        if _is_value(node.args.get("low")) and _is_value(node.args.get("high")):
            side = _unwrap_paren(node.this)
    elif (
        isinstance(node, exp.Is)
        and not node.args.get("negate")
        and isinstance(_unwrap_paren(node.expression), exp.Null)
    ):
        side = _unwrap_paren(node.this)
    column = side.find(exp.Column) if side is not None else None
    if column is None or (lead_columns is not None and column.name not in lead_columns):
        return ("leaf", "OTRA", None, None)
    if isinstance(side, exp.Column):
        return ("leaf", "OK", None, column.name)
    subtype = _wrapped_subtype(side)
    return ("leaf", "FIX", subtype, column.name) if subtype else ("leaf", "OTRA", None, None)


def _condition_tree(node: exp.Expression, lead_columns: set[str] | None) -> tuple:
    # ("and" | "or", [hijos]) aplanado, o una hoja
    node = _unwrap_paren(node)
    for kind, node_type in (("and", exp.And), ("or", exp.Or)):
        if isinstance(node, node_type):
            children = []
            for child in (node.this, node.expression):
                sub = _condition_tree(child, lead_columns)
                children += sub[1] if sub[0] == kind else [sub]
            return (kind, children)
    return _leaf(node, lead_columns)


def _usable(tree: tuple, with_fix: bool) -> bool:
    # El motor puede usar un indice para esta parte? Un AND si alguna parte
    # puede; un OR solo si todas sus ramas pueden (BitmapOr / index_merge).
    # Medido en los dos motores: ql_sandbox/explain_or (4.152 combinaciones).
    if tree[0] == "leaf":
        return tree[1] == "OK" or (with_fix and tree[1] == "FIX")
    results = [_usable(child, with_fix) for child in tree[1]]
    return any(results) if tree[0] == "and" else all(results)


def _has_or(tree: tuple) -> bool:
    return tree[0] == "or" or (tree[0] == "and" and any(_has_or(child) for child in tree[1]))


def _fix_leaves(tree: tuple, in_or: bool = False) -> list[tuple[tuple, bool]]:
    # (hoja FIX, esta dentro de un OR) que hay que arreglar para que esta parte
    # use un indice; se llama solo si hoy no puede y con el arreglo si. En un
    # OR, las de todas las ramas que hoy no pueden; en un AND, las de las
    # partes que se pueden arreglar, prefiriendo las que no tienen OR (un solo
    # arreglo alcanza).
    if tree[0] == "leaf":
        return [(tree, in_or)]
    if tree[0] == "or":
        return [pair for child in tree[1] if not _usable(child, False) for pair in _fix_leaves(child, True)]
    fixable = [child for child in tree[1] if _usable(child, True)]
    chosen = [child for child in fixable if not _has_or(child)] or fixable
    return [pair for child in chosen for pair in _fix_leaves(child, in_or)]


def _detect_predicate_subtypes(
    predicate: str,
    lead_columns: set[str] | None = None,
    dialect: str = "postgres",
) -> list[tuple[str, str | None]]:
    # (subtipo, columna) de las columnas envueltas que, reescritas, dejan que el
    # motor use un indice existente. Nada si el predicado ya puede usar uno o
    # si reescribirlas no alcanza (por ejemplo, otra rama del OR sin indice).
    # Misma regla en los dos motores: una conversion es un cast sobre la
    # columna (el pipeline escribe la implicita de MySQL y quita el (col)::text
    # de las varchar de Postgres, stages/type_conversions.py). LIKE no cuenta:
    # el patron llega redactado y no se sabe si empieza con comodin.
    found: list[tuple[str, str | None]] = []
    for subtype, column, _ in _non_sargable_fixes(predicate, lead_columns, dialect):
        if (subtype, column) not in found:
            found.append((subtype, column))
    return found


def _non_sargable_fixes(
    predicate: str,
    lead_columns: set[str] | None = None,
    dialect: str = "postgres",
) -> list[tuple[str, str, bool]]:
    # (subtipo, columna, dentro de un OR) de cada columna a reescribir
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
    conditions = _condition_tree(tree, lead_columns)
    if _usable(conditions, False) or not _usable(conditions, True):
        return []
    return [(leaf[2], leaf[3], in_or) for leaf, in_or in _fix_leaves(conditions)]


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
            "non_sargable_predicate | no se pudo parsear canonic_query | error=%s | texto=%.200s",
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


def detect_non_sargable_predicates(
    snapshot: SnapshotPayload,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
    min_live_rows: int = DEFAULT_MIN_LIVE_ROWS,
    max_selectivity: float = DEFAULT_MAX_SELECTIVITY,
    min_window_calls: int = DEFAULT_MIN_WINDOW_CALLS,
) -> list[Hallazgo]:
    # Detecta SELECT simples que recorren completa una tabla grande para
    # devolver pocas filas porque el predicado envuelve la columna, y cruza cada
    # hallazgo contra snapshot.indexes para avisar si ya existe un indice que
    # el predicado esta neutralizando, o si la columna aun no tiene ninguno.
    # Solo lee `history`: la fila nueva la guarda main.py despues de todos los detectores.
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
    leading_columns_by_table: dict[tuple[str | None, str], set[str]] = {}
    full_leading_columns_by_table: dict[tuple[str | None, str], set[str]] = {}
    # tabla -> primera columna -> nombres de los indices que empiezan por ella
    index_names_by_table: dict[tuple[str | None, str], dict[str, list[str]]] = {}
    for index in snapshot.indexes:
        lead_column, is_partial = _index_lead_column(index.index_ref)
        if not index.table_name or not lead_column:
            continue
        key = (index.schema_name, index.table_name)
        leading_columns_by_table.setdefault(key, set()).add(lead_column)
        if not is_partial:
            full_leading_columns_by_table.setdefault(key, set()).add(lead_column)
        if index.index_name:
            index_names_by_table.setdefault(key, {}).setdefault(lead_column, []).append(index.index_name)

    def _explicacion_con_indice(subtipo: str, indexes: list[str], only_partial: bool) -> tuple[str, str]:
        # nombra el indice que el predicado desaprovecha y avisa si es solo parcial
        explicacion, recomendacion = _SUBTIPO_MENSAJES[subtipo]
        if indexes:
            explicacion += f" Indice desaprovechado: {', '.join(indexes)}."
        if only_partial:
            explicacion += (
                " El unico indice que cubre esta columna como principal es parcial (tiene "
                "un WHERE): confirmar que esta consulta cae dentro de esa condicion antes "
                "de asumir que ya esta resuelto."
            )
        return explicacion, recomendacion

    for candidate in snapshot.top_impact_queries:
        if candidate.query_id is None:
            continue
        # sin plan la consulta no se evalua
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

        for operation in operations:
            # condicion 3 (AP-02): escaneo completo sobre la tabla del SELECT
            if operation.type != "scan" or operation.access_method not in _FULL_SCAN_METHODS:
                continue
            relation = operation.relation
            # table_name es la tabla real en Postgres (relation puede ser un
            # nombre generado, ej. ql_pt_orders_1); en MySQL llega en None
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

            # condiciones 6 y 7: columnas envueltas que encabezan un indice y
            # cuya reescritura deja usarlo. En un SELECT simple todas las
            # columnas son de la tabla del scan.
            predicate = operation.predicate
            if not predicate:
                continue
            lead_columns, ambiguous = _lookup_by_table(leading_columns_by_table, candidate.schema_name, real_table)
            if ambiguous:
                continue  # no se sabe de cual tabla son los indices
            fixes = _non_sargable_fixes(predicate, lead_columns or set())

            reported = set()
            for subtipo, column_name, in_or in fixes:
                if (subtipo, column_name) in reported:
                    continue
                reported.add((subtipo, column_name))
                only_partial = _only_partial_index_for_column(
                    real_table, column_name, candidate.schema_name,
                    leading_columns_by_table, full_leading_columns_by_table,
                )
                names_by_column, _ = _lookup_by_table(index_names_by_table, candidate.schema_name, real_table)
                indexes = sorted((names_by_column or {}).get(column_name, []))
                explicacion, recomendacion = _explicacion_con_indice(subtipo, indexes, only_partial)
                if in_or:
                    explicacion += (
                        " La condicion esta en una rama de un OR: el motor solo usa indices en un "
                        "OR si todas sus ramas pueden usarlos, y las demas ramas ya pueden."
                    )
                findings.append(
                    Hallazgo(
                        antipatron="non_sargable_predicate",
                        severidad="high" if operation.access_method == "full_table_scan" else "medium",
                        evidencia={
                            "query_id": candidate.query_id,
                            "query_text": candidate.query_text,
                            "canonic_query": candidate.canonic_query,
                            "schema_name": candidate.schema_name,
                            "tables": [table_name],
                            "subtipo": subtipo,
                            "predicate": predicate,
                            "relation": relation,
                            "column": column_name,
                            # indices que empiezan por la columna y el predicado desaprovecha
                            "unused_indexes": indexes,
                            "only_partial_index": only_partial,
                            "dentro_de_or": in_or,
                            "access_method": operation.access_method,
                            "index_name": operation.index_name,
                            "live_rows": live_rows,
                            "rows_per_call": round(rows_per_call, 2),
                            "selectivity": selectivity,
                            # solo informativo: la regla usa las filas reales
                            "estimated_rows": operation.estimated_rows,
                            "window_calls": window.calls,
                            "window_rows": window.rows,
                            "query_new_in_window": window.new_query,
                            "explain_source": explain.explain_source,
                        },
                        explicacion=explicacion,
                        recomendacion=recomendacion,
                        query_id=candidate.query_id,
                        table_name=real_table,
                    )
                )

    return findings
