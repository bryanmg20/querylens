from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp

from models import Hallazgo, Snapshot, StatementHistory
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Regla AP-02 (SegundoInforme, Tabla 2), igual para ambos motores, limitada a
# SELECT simples:
#   1. la consulta es un SELECT simple (ver _simple_select_table)
#   2. >= DEFAULT_MIN_WINDOW_CALLS ejecuciones en la ventana (delta desde el
#      snapshot anterior). Ademas evita re-reportar queries que ya no corren y
#      siguen en top_impact por tiempo acumulado.
#   3. el plan recorre completa esa tabla (full_table_scan)
#   4. la tabla tiene >= DEFAULT_MIN_LIVE_ROWS filas vivas
#   5. devuelve <= DEFAULT_MAX_SELECTIVITY de las filas recorridas:
#      (filas devueltas / ejecuciones de la ventana) / live_rows
#
# Por que solo SELECT simples: en un SELECT de una sola tabla sin agregacion,
# agrupamiento ni corte, cada fila que pasa el WHERE se devuelve, asi que las
# filas devueltas por ejecucion son las filas que deja pasar el filtro. Eso da
# la selectividad REAL sin depender de la estimacion del plan (generico en
# Postgres, `filtered` constante en MySQL sin indice ni histograma). En joins,
# agregados, GROUP BY, DISTINCT o LIMIT las filas devueltas ya no dicen cuantas
# pasaron el filtro de cada tabla, y la estimacion por tabla no es confiable.
#
# Limitaciones declaradas:
#   - consultas que no son SELECT simples no se evaluan (falso negativo por diseno)
#   - el promedio de la ventana esconde la minoria selectiva con datos sesgados:
#     si la mayoria de las ejecuciones devuelve muchas filas, no se detecta
#   - el plan sigue haciendo falta para saber que hubo full scan; en Postgres
#     sin plan de auto_explain se usa el generico
#   - tabla particionada: el plan recorre las particiones, no la tabla del
#     SELECT, y no se detecta

DEFAULT_MIN_LIVE_ROWS = 10_000  # tabla considerada "grande" para este chequeo
DEFAULT_MAX_SELECTIVITY = 0.1  # devuelve como maximo el 10% de las filas recorridas
DEFAULT_MIN_WINDOW_CALLS = 10

# Igual que AP-07/AP-08: una query sin fila guardada cuyo counters_epoch esta a
# menos de esto de la hora del snapshot empezo a contar dentro de la ventana.
# Asume snapshots cada 60 s (ventana del informe).
_NEW_QUERY_WINDOW = timedelta(seconds=60)


@dataclass
class _RowsWindow:
    calls: int
    rows: int
    # True si la query aparecio en esta ventana y se uso su acumulado completo
    new_query: bool = False


# Misma conversion que statement_history.parse_instant; se duplica a proposito
# hasta confirmar AP-02 por separado, como el resto de la logica de ventana.
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
    # repetidas igual que la fila guardada en statement_samples. Misma logica que AP-08.
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
    # no se puede medir o si la query no se ejecuto en la ventana. Misma logica que AP-08.
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
            "avoidable_full_scan | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return None

    if not isinstance(select, exp.Select):
        return None
    # cualquier otro SELECT adentro es una subconsulta (WHERE, SELECT, FROM)
    if any(node is not select for node in select.find_all(exp.Select)):
        return None
    # sin subconsultas, buscar en todo el arbol es buscar en esta consulta. Por
    # tipo de nodo y no por args: los nombres de args cambian entre versiones
    # de sqlglot (ej. "with_") y FETCH se guarda dentro de "limit"
    if select.find(exp.With, exp.Join, exp.Limit, exp.Offset, exp.Fetch, exp.Group, exp.Having, exp.Distinct):
        return None
    for aggregate in select.find_all(exp.AggFunc):
        if aggregate.find_ancestor(exp.Window) is None:
            return None

    tables = list(select.find_all(exp.Table))
    if len(tables) != 1 or not tables[0].name:
        return None
    return tables[0].name


# Detecta SELECT simples que recorren completa una tabla grande para devolver
# una fraccion chica de sus filas
def detect_avoidable_full_scans(
    snapshot: Snapshot,
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
    # indexa los EXPLAIN por query_id para poder cruzarlos con cada candidato
    explains_by_query_id = {
        explain.get("query_id"): explain
        for explain in snapshot.canonic_explains
        if explain.get("query_id") is not None
    }
    # tamano real de cada tabla. Clave con schema para no confundir tablas
    # homonimas de schemas distintos
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
        if candidate.query_id is None:
            continue
        explain = explains_by_query_id.get(candidate.query_id)
        if explain is None:
            continue

        # condicion 1
        table_name = _simple_select_table(candidate.canonic_query)
        if table_name is None:
            continue

        # condicion 2
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
        operations = explain.get("canonical_plan", {}).get("physical_operations", [])
        for operation in operations:
            # condicion 3: full scan sobre la tabla del SELECT
            if operation.get("type") != "scan" or operation.get("access_method") != "full_table_scan":
                continue
            relation = operation.get("relation")
            if alias_map.get(relation, relation) != table_name:
                continue

            # condicion 4
            live_rows = _live_rows_for(candidate.schema_name, table_name)
            if live_rows is None or live_rows < min_live_rows:
                continue

            # condicion 5: selectividad real de la ventana
            rows_per_call = window.rows / window.calls
            selectivity = rows_per_call / live_rows
            if selectivity > max_selectivity:
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
                        "tables": [table_name],
                        "relation": relation,
                        "predicate": operation.get("predicate"),
                        "live_rows": live_rows,
                        "rows_per_call": round(rows_per_call, 2),
                        "selectivity": selectivity,
                        # solo informativo: la regla usa las filas reales
                        "estimated_rows": operation.get("estimated_rows"),
                        "window_calls": window.calls,
                        "window_rows": window.rows,
                        "query_new_in_window": window.new_query,
                        "explain_source": explain.get("explain_source"),
                        "mean_time_ms": candidate.mean_time_ms,
                        "total_time_ms": candidate.total_time_ms,
                        "execution_count": candidate.execution_count,
                    },
                    explicacion=(
                        f"La consulta recorre completa la tabla {table_name} ({live_rows:,} filas vivas) "
                        f"y en la ultima ventana devolvio en promedio {rows_per_call:,.0f} filas por "
                        f"ejecucion ({selectivity:.2%} de la tabla) en {window.calls} ejecuciones."
                    ),
                    recomendacion=(
                        "Revisar el predicado de filtro contra los indices existentes en la tabla; "
                        "considerar un indice que lo cubra o reescribir la condicion para que sea sargable."
                    ),
                    query_id=candidate.query_id,
                    table_name=table_name,
                )
            )
            # un SELECT simple tiene un solo scan de su tabla
            break
    return findings
