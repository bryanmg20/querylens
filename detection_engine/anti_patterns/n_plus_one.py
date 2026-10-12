from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp

from models import ForeignKeyColumn, Hallazgo, Snapshot, Statement, StatementHistory
from logger import get_logger

logger = get_logger(__name__)


# Regla AP-06 patron N+1 (SegundoInforme, Tabla 2), igual para ambos motores.
# Una huella "hija" se ejecuta una vez por cada fila que devolvio una huella
# "padre" en la misma ventana:
#   - la hija es un SELECT con igualdad (col = $N) en su WHERE principal sobre
#     las columnas de una clave foranea, en cualquiera de las dos direcciones:
#       uno_a_muchos: la hija filtra por la columna FK (orders WHERE customer_id = ?)
#                     y el padre lee la tabla referenciada (customers)
#       muchos_a_uno: la hija filtra por la columna referenciada (customers WHERE id = ?)
#                     y el padre lee la tabla que tiene la FK (orders)
#   - la hija tiene >= 100 ejecuciones en la ventana
#   - sus ejecuciones coinciden (+-20 %) con las filas que devolvio el padre en
#     la misma ventana
#
# Ajustes respecto al informe (pendiente: llevarlos a la fila de AP-06):
#   - el padre devuelve >= 2 filas por ejecucion: descarta el "1 + 1" normal de
#     una pagina de detalle (leer un pedido y despues su cliente)
#   - las dos ventanas se miden contra el mismo snapshot anterior (o las dos
#     queries son nuevas en esta ventana): si no, se compararian periodos distintos
#   - no se exige ~1 fila por ejecucion en la hija: en uno_a_muchos devuelve
#     todas las filas hijas de cada padre
#   - se excluyen las lecturas con bloqueo (FOR UPDATE / SHARE / KEY SHARE): con
#     pg_stat_statements.track=all Postgres registra el chequeo interno de cada
#     FK como SELECT 1 ... WHERE id = $1 FOR KEY SHARE, una vez por fila escrita
#
# No usa el EXPLAIN: recorre todos los statements, no solo top_impact_queries.

_MIN_CHILD_CALLS = 100
_MATCH_TOLERANCE = 0.20
_MIN_PARENT_ROWS_PER_CALL = 2

# Severidad por las ejecuciones evitables de la hija en la ventana
_HIGH_CHILD_CALLS = 1000

# Igual que AP-07 y AP-08: una query sin fila guardada cuyo counters_epoch esta
# a menos de esto de la hora del snapshot empezo a contar dentro de la ventana.
# Limitacion / pendiente (la misma de AP-07): asume snapshots cada 60 s.
_NEW_QUERY_WINDOW = timedelta(seconds=60)

_UNAVAILABLE = "Not available"


@dataclass
class _RowsWindow:
    calls: int
    rows: int
    # True si la query aparecio en esta ventana y se uso su acumulado completo
    new_query: bool = False


@dataclass
class _Query:
    query_id: str
    statement: Statement
    window: _RowsWindow
    # captured_at de la fila contra la que se resto (None si la query es nueva)
    window_start: datetime | None
    # (schema, tabla) que lee el SELECT principal, en minusculas
    tables: set[tuple[str | None, str]] = field(default_factory=set)
    # columnas con igualdad contra un parametro en el WHERE principal, por tabla
    equalities: dict[tuple[str | None, str], set[str]] = field(default_factory=dict)


@dataclass
class _Relation:
    # una clave foranea (las compuestas agrupadas) con sus columnas en orden
    constraint_name: str
    table: tuple[str | None, str]
    columns: list[str]
    referenced_table: tuple[str | None, str]
    referenced_columns: list[str]


# Misma conversion que statement_history.parse_instant; se duplica a proposito
# hasta confirmar AP-06 por separado, como el resto de la logica de ventana.
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


def _current_counters(snapshot: Snapshot) -> dict[str, tuple[Statement, int, int | None, str | None]]:
    # (primer statement, ejecuciones, filas devueltas, counters_epoch) por query,
    # sumando las filas repetidas igual que la fila guardada en statement_samples
    counters: dict[str, tuple[Statement, int, int | None, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None:
            continue
        key = str(statement.query_id)
        first, calls, rows, epoch = counters.get(key, (statement, 0, None, statement.counters_epoch))
        if statement.rows_returned is not None:
            rows = (rows or 0) + statement.rows_returned
        counters[key] = (first, calls + statement.execution_count, rows, epoch)
    return counters


# Misma ventana que AP-08 (cardinality_misestimate._rows_window), duplicada a proposito
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
        # sin fila anterior: solo se puede medir si la query empezo a contar
        # dentro de esta ventana; entonces todo su acumulado es de esta ventana
        if epoch is None or calls <= 0:
            return None
        started_in_window = captured_at - epoch <= _NEW_QUERY_WINDOW
        started_after_previous = previous_snapshot_at is not None and epoch > previous_snapshot_at
        if started_in_window or started_after_previous:
            return _RowsWindow(calls=calls, rows=rows, new_query=True)
        return None
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


def _lower(value: str | None) -> str | None:
    return value.lower() if value else None


def _parse_select(canonic_query: str | None) -> exp.Select | None:
    # canonic_query siempre viene en dialecto postgres. Solo interesan SELECT:
    # el resto (DML, DDL, utilidades) no puede ser ni padre ni hija.
    if not canonic_query or canonic_query == _UNAVAILABLE:
        return None
    if not canonic_query.lstrip().upper().startswith(("SELECT", "WITH")):
        return None
    try:
        ast = sqlglot.parse_one(canonic_query, read="postgres")
    except Exception as e:
        logger.warning(
            "n_plus_one | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return None
    if not isinstance(ast, exp.Select):
        return None
    # lecturas con bloqueo: el chequeo de FK de Postgres y los SELECT ... FOR UPDATE
    if ast.args.get("locks"):
        return None
    return ast


def _main_tables(select: exp.Select, default_schema: str | None) -> dict[str, tuple[str | None, str]]:
    # alias o nombre -> (schema, tabla) de las tablas del SELECT principal (FROM
    # y JOIN), sin las de subconsultas ni CTE
    tables: dict[str, tuple[str | None, str]] = {}
    for table in select.find_all(exp.Table):
        if not table.name or table.find_ancestor(exp.Select) is not select:
            continue
        key = (_lower(table.db) or _lower(default_schema), table.name.lower())
        tables[table.name.lower()] = key
        if table.alias:
            tables[table.alias.lower()] = key
    return tables


def _is_parameter(node: exp.Expression) -> bool:
    # el canonic_query reemplaza todo literal por $N
    return isinstance(node, (exp.Parameter, exp.Placeholder, exp.Literal)) or (
        not isinstance(node, exp.Column) and node.find(exp.Column) is None
        and node.find(exp.Parameter, exp.Placeholder) is not None
    )


def _equalities(
    select: exp.Select, tables: dict[str, tuple[str | None, str]],
) -> dict[tuple[str | None, str], set[str]]:
    # Columnas comparadas por igualdad contra un parametro en las condiciones del
    # WHERE principal unidas por AND. Un OR o un IN no cuentan: IN (...) es
    # justamente la forma de traer todos los registros de una vez.
    where = select.args.get("where")
    if where is None:
        return {}
    distinct_tables = set(tables.values())
    only_table = next(iter(distinct_tables)) if len(distinct_tables) == 1 else None

    condition = where.this
    conjuncts = list(condition.flatten()) if isinstance(condition, exp.And) else [condition]
    equalities: dict[tuple[str | None, str], set[str]] = {}
    for conjunct in conjuncts:
        while isinstance(conjunct, exp.Paren):
            conjunct = conjunct.this
        if not isinstance(conjunct, exp.EQ):
            continue
        left, right = conjunct.left, conjunct.right
        if isinstance(left, exp.Column) and _is_parameter(right):
            column = left
        elif isinstance(right, exp.Column) and _is_parameter(left):
            column = right
        else:
            continue
        if not column.name:
            continue
        # columna sin tabla: solo se puede asignar si el SELECT lee una sola tabla
        table = tables.get(column.table.lower()) if column.table else only_table
        if table is None:
            continue
        equalities.setdefault(table, set()).add(column.name.lower())
    return equalities


def _relations(foreign_keys: list[ForeignKeyColumn]) -> list[_Relation]:
    # agrupa las filas de foreign_keys por constraint: una compuesta llega como
    # varias filas, una por columna, ordenadas por position
    grouped: dict[tuple, list[ForeignKeyColumn]] = {}
    for row in foreign_keys:
        if not (row.table_name and row.column_name and row.referenced_table_name and row.referenced_column_name):
            continue
        key = (_lower(row.schema_name), row.table_name.lower(), row.constraint_name)
        grouped.setdefault(key, []).append(row)

    relations = []
    for (schema, table, constraint_name), rows in grouped.items():
        rows.sort(key=lambda row: row.position or 0)
        first = rows[0]
        relations.append(
            _Relation(
                constraint_name=constraint_name or "",
                table=(schema, table),
                columns=[row.column_name.lower() for row in rows],
                referenced_table=(_lower(first.referenced_schema_name), first.referenced_table_name.lower()),
                referenced_columns=[row.referenced_column_name.lower() for row in rows],
            )
        )
    return relations


def _same_table(a: tuple[str | None, str], b: tuple[str | None, str]) -> bool:
    # sin schema de algun lado (statement sin schema resuelto) se compara solo el nombre
    if a[1] != b[1]:
        return False
    return a[0] is None or b[0] is None or a[0] == b[0]


def _reads(query: _Query, table: tuple[str | None, str]) -> bool:
    return any(_same_table(own, table) for own in query.tables)


def _filters_by(query: _Query, table: tuple[str | None, str], columns: list[str]) -> bool:
    # todas las columnas de la FK con igualdad en la misma tabla
    return any(
        _same_table(own, table) and set(columns) <= equal_columns
        for own, equal_columns in query.equalities.items()
    )


def _same_window(child: _Query, parent: _Query) -> bool:
    if child.window.new_query or parent.window.new_query:
        return child.window.new_query and parent.window.new_query
    return child.window_start == parent.window_start


def _qualified(table: tuple[str | None, str]) -> str:
    return f"{table[0]}.{table[1]}" if table[0] else table[1]


# Detecta queries que se ejecutan una vez por cada fila que devolvio otra query
# en la misma ventana, unidas por una clave foranea
def detect_n_plus_one(
    snapshot: Snapshot,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
) -> list[Hallazgo]:
    # Solo lee `history`: la fila nueva la guarda main.py despues de todos los detectores
    history = history or {}
    captured_at = captured_at or datetime.now(timezone.utc)
    relations = _relations(snapshot.foreign_keys)
    # sin FK no hay relacion padre-hija que verificar (sysbench no define ninguna)
    if not relations:
        return []
    previous_snapshot_at = max((row.captured_at for row in history.values()), default=None)

    queries: list[_Query] = []
    for query_id, (statement, calls, rows, epoch_raw) in _current_counters(snapshot).items():
        previous = history.get(query_id)
        window = _rows_window(
            calls, rows, _parse_instant(epoch_raw), previous, previous_snapshot_at, captured_at,
        )
        if window is None:
            continue
        # solo se parsea lo que puede ser hija o padre en esta ventana
        could_be_child = window.calls >= _MIN_CHILD_CALLS
        could_be_parent = window.rows >= _MIN_PARENT_ROWS_PER_CALL * window.calls
        if not (could_be_child or could_be_parent):
            continue
        select = _parse_select(statement.canonic_query)
        if select is None:
            continue
        tables = _main_tables(select, statement.schema_name)
        queries.append(
            _Query(
                query_id=query_id,
                statement=statement,
                window=window,
                window_start=None if window.new_query else previous.captured_at,
                tables=set(tables.values()),
                equalities=_equalities(select, tables),
            )
        )

    findings = []
    for child in queries:
        if child.window.calls < _MIN_CHILD_CALLS or not child.equalities:
            continue

        best = None
        for relation in relations:
            # (direccion, tabla filtrada por la hija, columnas filtradas, tabla del padre)
            directions = (
                ("uno_a_muchos", relation.table, relation.columns, relation.referenced_table),
                ("muchos_a_uno", relation.referenced_table, relation.referenced_columns, relation.table),
            )
            for direction, child_table, child_columns, parent_table in directions:
                if not _filters_by(child, child_table, child_columns):
                    continue
                for parent in queries:
                    if parent is child or not _reads(parent, parent_table):
                        continue
                    if parent.window.rows < _MIN_PARENT_ROWS_PER_CALL * parent.window.calls:
                        continue
                    # statements de Postgres es de todo el servidor: las dos en la misma base
                    if parent.statement.database_name != child.statement.database_name:
                        continue
                    if not _same_window(child, parent):
                        continue
                    if parent.window.rows <= 0:
                        continue
                    deviation = abs(child.window.calls - parent.window.rows) / parent.window.rows
                    if deviation > _MATCH_TOLERANCE:
                        continue
                    if best is None or deviation < best[0]:
                        best = (deviation, relation, direction, child_table, child_columns, parent)

        if best is None:
            continue
        deviation, relation, direction, child_table, child_columns, parent = best
        statement = child.statement
        parent_rows_per_call = parent.window.rows / parent.window.calls
        findings.append(
            Hallazgo(
                antipatron="n_plus_one",
                severidad="high" if child.window.calls >= _HIGH_CHILD_CALLS else "medium",
                evidencia={
                    "query_id": statement.query_id,
                    "query_text": statement.query_text,
                    "canonic_query": statement.canonic_query,
                    "schema_name": statement.schema_name,
                    "database_name": statement.database_name,
                    "direccion": direction,
                    "constraint_name": relation.constraint_name,
                    "fk_table": _qualified(relation.table),
                    "fk_columns": relation.columns,
                    "referenced_table": _qualified(relation.referenced_table),
                    "referenced_columns": relation.referenced_columns,
                    "child_table": _qualified(child_table),
                    "child_filter_columns": child_columns,
                    "child_window_calls": child.window.calls,
                    "child_window_rows": child.window.rows,
                    "parent_query_id": parent.statement.query_id,
                    "parent_query_text": parent.statement.query_text,
                    "parent_canonic_query": parent.statement.canonic_query,
                    "parent_window_calls": parent.window.calls,
                    "parent_window_rows": parent.window.rows,
                    "parent_rows_per_call": round(parent_rows_per_call, 2),
                    # |ejecuciones hija - filas padre| / filas padre
                    "desvio": round(deviation, 4),
                    "query_new_in_window": child.window.new_query,
                },
                explicacion=(
                    f"En la ultima ventana esta consulta sobre {_qualified(child_table)} se ejecuto "
                    f"{child.window.calls:,} veces, casi una por cada una de las {parent.window.rows:,} filas "
                    f"que devolvio la consulta padre ({parent.window.calls:,} ejecuciones, "
                    f"{parent_rows_per_call:,.0f} filas cada una), y filtra por la clave foranea "
                    f"{relation.constraint_name} que une ambas tablas. Es el patron N+1: cada consulta es "
                    "rapida, pero se repite una vez por registro en lugar de traerlos todos juntos."
                ),
                recomendacion=(
                    f"Traer las filas de {_qualified(child_table)} junto con la consulta padre, con un JOIN "
                    f"por {', '.join(child_columns)}, o en una sola consulta adicional con "
                    f"WHERE ({', '.join(child_columns)}) IN (...) sobre los valores que devolvio el padre. "
                    "Si la aplicacion usa un ORM con carga perezosa, activar la carga anticipada "
                    "(eager loading / prefetch) para esta relacion."
                ),
                query_id=statement.query_id,
                table_name=child_table[1],
            )
        )

    return findings
