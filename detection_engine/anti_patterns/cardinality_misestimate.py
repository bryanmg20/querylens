from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp

from models import CandidateStatement, Hallazgo, Snapshot, StatementHistory
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Regla AP-08 (SegundoInforme, Tabla 2), igual para ambos motores: se compara
# la estimacion de filas de salida del EXPLAIN (output_rows del canonical_plan)
# contra las filas por ejecucion REALES de la ultima ventana
# (delta rows_returned / delta execution_count desde el snapshot anterior).
#   q-error = max(estimadas / reales, reales / estimadas) >= 10
#   y el mayor de los dos valores >= 100 filas
#
# Limitacion declarada en el informe: solo se compara la salida final, no los
# nodos intermedios, porque QueryLens no ejecuta EXPLAIN ANALYZE.
#
# Exclusiones (se leen del canonic_query, no del plan: el EXPLAIN JSON de
# MySQL 8.0 no tiene nodo LIMIT):
#   - lo que no es un SELECT (UNION, INSERT, UPDATE, DELETE): en DML las filas
#     "devueltas" no significan lo mismo en ambos motores
#   - LIMIT / OFFSET / FETCH: el corte esconde el error de abajo (informe)
#   - agregacion sin GROUP BY: siempre 1 fila estimada y 1 real (informe)
#   - GROUP BY y DISTINCT: en MySQL el ultimo acceso estima las filas ANTES de
#     agrupar, y compararlas con los grupos devueltos daria falsos positivos.
#     Se excluyen en ambos motores para que la regla sea la misma.
#     Pendiente: agregar esta exclusion a la fila de AP-08 del informe.
#
# Sin minimo de ejecuciones por ventana (el informe no lo pide para AP-08).

_MIN_Q_ERROR = 10
_MIN_ROWS = 100

# Severidad por la magnitud del error: >= 100 son dos ordenes de magnitud
_HIGH_Q_ERROR = 100

# Igual que AP-07: una query sin fila guardada cuyo counters_epoch esta a menos
# de esto de la hora del snapshot empezo a contar dentro de la ventana, asi que
# todo su acumulado es de esa ventana. Es el largo de la ventana del informe (60 s).
# Limitacion / pendiente (la misma de AP-07): asume snapshots cada 60 s; con
# otro intervalo, una query nueva que empezo hace mas de 60 s solo se reconoce
# si empezo despues del snapshot anterior.
_NEW_QUERY_WINDOW = timedelta(seconds=60)


@dataclass
class _RowsWindow:
    calls: int
    rows: int
    # True si la query aparecio en esta ventana y se uso su acumulado completo
    new_query: bool = False


# Misma conversion que statement_history.parse_instant; se duplica a proposito
# hasta confirmar AP-08 por separado, como el resto de la logica de ventana.
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


def _current_counters(snapshot: Snapshot) -> dict[str, tuple[int, int | None, str | None]]:
    # (ejecuciones, filas devueltas, counters_epoch) por query, sumando las filas
    # repetidas igual que la fila guardada en statement_samples: si no, la resta
    # compararia el valor de una sola fila contra la suma de todas
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
        # sin fila anterior: solo se puede medir si la query empezo a contar
        # dentro de esta ventana (ej. tabla recreada en Postgres, que le cambia
        # el queryid). En ese caso todo su acumulado es de esta ventana. Vale si:
        #  - empezo hace menos de una ventana, o
        #  - empezo despues del snapshot anterior (hueco largo entre snapshots)
        # Si no, se evalua en el proximo snapshot, cuando ya hay contra que restar.
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


def _parse_select(canonic_query: str | None) -> exp.Select | None:
    # canonic_query siempre viene en dialecto postgres
    if not canonic_query:
        return None
    try:
        ast = sqlglot.parse_one(canonic_query, read="postgres")
    except Exception as e:
        logger.warning(
            "cardinality_misestimate | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return None
    return ast if isinstance(ast, exp.Select) else None


def _exclusion(select: exp.Select | None) -> str | None:
    # Motivo por el que la consulta no se evalua, o None si se evalua
    if select is None:
        return "no_select"
    if select.args.get("limit") or select.args.get("offset") or select.args.get("fetch"):
        return "limit"
    if select.args.get("group"):
        return "group_by"
    if select.args.get("distinct"):
        return "distinct"
    for aggregate in select.find_all(exp.AggFunc):
        # solo agregaciones de esta consulta: no las de una subconsulta ni las
        # funciones de ventana (COUNT(*) OVER ...), que no cambian la cantidad de filas
        if aggregate.find_ancestor(exp.Select) is select and aggregate.find_ancestor(exp.Window) is None:
            return "aggregate"
    return None


def _filter_columns(select: exp.Select, alias_map: dict[str, str]) -> dict[str, list[str]]:
    # Columnas del WHERE de la consulta principal, agrupadas por tabla real. Se
    # omiten las comparaciones columna contra columna (condiciones de join) y
    # las columnas de subconsultas.
    tables = sorted({
        table.name for table in select.find_all(exp.Table)
        if table.name and table.find_ancestor(exp.Select) is select
    })
    default_table = tables[0] if len(tables) == 1 else None

    where = select.args.get("where")
    columns_by_table: dict[str, list[str]] = {}
    if where is None:
        return columns_by_table
    # bfs=False: en el orden en que aparecen en el WHERE
    for column in where.find_all(exp.Column, bfs=False):
        if column.find_ancestor(exp.Select) is not select:
            continue
        parent = column.parent
        if isinstance(parent, exp.Binary) and isinstance(parent.left, exp.Column) and isinstance(parent.right, exp.Column):
            continue
        table = alias_map.get(column.table, column.table) if column.table else default_table
        if not table or not column.name:
            continue
        columns = columns_by_table.setdefault(table, [])
        if column.name not in columns:
            columns.append(column.name)
    return columns_by_table


def _is_postgres(candidate: CandidateStatement) -> bool:
    # Postgres entrega queryid como entero y MySQL el DIGEST como texto; mientras
    # los dos snapshots compartan db_id, es la forma de saber el motor
    return isinstance(candidate.query_id, int)


def _qualified(schema_name: str | None, table: str) -> str:
    return f"{schema_name}.{table}" if schema_name else table


def _recommendation(candidate: CandidateStatement, columns_by_table: dict[str, list[str]], tables: list[str]) -> str:
    schema = candidate.schema_name
    if not columns_by_table:
        # el error no viene de un filtro sobre columnas sino de los joins
        targets = ", ".join(_qualified(schema, table) for table in tables) or "las tablas de la consulta"
        if _is_postgres(candidate):
            return (
                f"Actualizar las estadisticas con ANALYZE sobre {targets}. Si el error persiste, "
                "revisar las columnas de los joins: estadisticas extendidas (CREATE STATISTICS) "
                "sobre columnas relacionadas de una misma tabla ayudan al optimizador a estimar la union."
            )
        return (
            f"Actualizar las estadisticas con ANALYZE TABLE sobre {targets}. Si el error persiste, "
            "crear histogramas (ANALYZE TABLE ... UPDATE HISTOGRAM ON ...) sobre las columnas de los joins."
        )

    parts = []
    for table, columns in columns_by_table.items():
        target = _qualified(schema, table)
        column_list = ", ".join(columns)
        if _is_postgres(candidate):
            if len(columns) >= 2:
                parts.append(
                    f"Ejecutar ANALYZE {target} por si las estadisticas estan desactualizadas. "
                    f"Si el error persiste, las columnas {column_list} probablemente estan correlacionadas "
                    "y el optimizador las trata como independientes: crear estadisticas extendidas con "
                    f"CREATE STATISTICS {table}_{'_'.join(columns)}_stats (dependencies, ndistinct, mcv) "
                    f"ON {column_list} FROM {target}; y luego ANALYZE {target};"
                )
            else:
                parts.append(
                    f"Ejecutar ANALYZE {target} por si las estadisticas estan desactualizadas. "
                    "Si el error persiste, aumentar el detalle de las estadisticas de la columna con "
                    f"ALTER TABLE {target} ALTER COLUMN {columns[0]} SET STATISTICS 1000; "
                    f"y luego ANALYZE {target};"
                )
        else:
            text = (
                f"Ejecutar ANALYZE TABLE {target} por si las estadisticas estan desactualizadas. "
                "Si el error persiste, crear histogramas sobre las columnas del filtro con "
                f"ANALYZE TABLE {target} UPDATE HISTOGRAM ON {column_list} WITH 100 BUCKETS;"
            )
            if len(columns) >= 2:
                text += (
                    " Los histogramas son por columna y no capturan la correlacion entre columnas: "
                    f"si {column_list} estan correlacionadas, un indice compuesto sobre ({column_list}) "
                    "le permite al optimizador estimar el filtro completo leyendo el indice."
                )
            parts.append(text)
    return " ".join(parts)


# Detecta consultas cuya estimacion de filas del EXPLAIN difiere en al menos un
# orden de magnitud de las filas que devolvieron en la ultima ventana
def detect_cardinality_misestimates(
    snapshot: Snapshot,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
) -> list[Hallazgo]:
    # Solo lee `history`: la fila nueva la guarda main.py despues de todos los detectores
    history = history or {}
    captured_at = captured_at or datetime.now(timezone.utc)
    # el snapshot anterior mas reciente de esta base, para reconocer queries nuevas
    previous_snapshot_at = max((row.captured_at for row in history.values()), default=None)
    counters = _current_counters(snapshot)
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }

    findings = []
    for candidate in snapshot.top_impact_queries:
        if candidate.query_id is None:
            continue
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue
        # el snapshot llega sin validar: un plan viejo (sin output_rows), un
        # EXPLAIN sin filas (ej. "Impossible WHERE" en MySQL) o un valor no
        # numerico se saltan en vez de romper el job completo
        estimates = (explain.get("canonical_plan") or {}).get("estimates") or {}
        try:
            estimated_rows = float(estimates.get("output_rows"))
        except (TypeError, ValueError):
            continue

        select = _parse_select(candidate.canonic_query)
        if _exclusion(select) is not None:
            continue

        query_id = str(candidate.query_id)
        # contadores desde statements (suma de filas repetidas); si el candidato
        # no aparece ahi, se usan los suyos
        calls, rows, epoch_raw = counters.get(
            query_id,
            (candidate.execution_count or 0, candidate.rows_returned, candidate.counters_epoch),
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)
        window = _rows_window(
            calls, rows, epoch, history.get(query_id), previous_snapshot_at, captured_at,
        )
        if window is None:
            continue

        actual_rows = window.rows / window.calls
        # como minimo 1 fila de cada lado: una consulta que no devolvio nada (o
        # una estimacion de 0) no puede dividir
        estimated_for_ratio = max(float(estimated_rows), 1.0)
        actual_for_ratio = max(actual_rows, 1.0)
        q_error = max(estimated_for_ratio / actual_for_ratio, actual_for_ratio / estimated_for_ratio)
        if q_error < _MIN_Q_ERROR or max(estimated_for_ratio, actual_for_ratio) < _MIN_ROWS:
            continue

        alias_map = resolve_table_aliases(candidate.canonic_query)
        tables = sorted({
            table.name for table in select.find_all(exp.Table)
            if table.name and table.find_ancestor(exp.Select) is select
        })
        columns_by_table = _filter_columns(select, alias_map)
        direction = "subestimacion" if actual_for_ratio > estimated_for_ratio else "sobreestimacion"

        if len(columns_by_table) == 1:
            table_name = next(iter(columns_by_table))
        elif len(tables) == 1:
            table_name = tables[0]
        else:
            table_name = ", ".join(tables) if tables else None

        findings.append(
            Hallazgo(
                antipatron="cardinality_misestimate",
                severidad="high" if q_error >= _HIGH_Q_ERROR else "medium",
                evidencia={
                    "query_id": candidate.query_id,
                    "query_text": candidate.query_text,
                    "canonic_query": candidate.canonic_query,
                    "schema_name": candidate.schema_name,
                    "tables": tables,
                    "filter_columns": columns_by_table,
                    # estimacion del EXPLAIN: filas de salida de la consulta
                    "estimated_rows": estimated_rows,
                    # realidad en la ventana: filas por ejecucion
                    "actual_rows_per_call": round(actual_rows, 2),
                    "q_error": round(q_error, 2),
                    "direccion": direction,
                    "window_calls": window.calls,
                    "window_rows": window.rows,
                    "query_new_in_window": window.new_query,
                },
                explicacion=(
                    f"El optimizador estimo {estimated_rows:g} filas para esta consulta y en la ultima "
                    f"ventana devolvio en promedio {actual_rows:,.0f} filas por ejecucion (q-error "
                    f"{q_error:,.0f}, {direction}). Con una estimacion tan lejana el optimizador puede "
                    "elegir un plan inadecuado, por ejemplo un nested loop pensado para pocas filas o "
                    "una reserva de memoria insuficiente. Solo se compara la salida final de la consulta, "
                    "no cada paso del plan, porque QueryLens no ejecuta EXPLAIN ANALYZE."
                ),
                recomendacion=_recommendation(candidate, columns_by_table, tables),
                query_id=candidate.query_id,
                table_name=table_name,
            )
        )

    return findings
