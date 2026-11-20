from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlglot
from sqlglot import exp
from querylens_contract import SnapshotPayload

from models import Hallazgo, StatementHistory
from logger import get_logger

from .table_aliases import resolve_table_aliases

logger = get_logger(__name__)


# Regla AP-02 (SegundoInforme, Tabla 2), igual para ambos motores, limitada a
# SELECT simples:
#   1. la consulta es un SELECT simple (ver _simple_select_table)
#   2. >= DEFAULT_MIN_WINDOW_CALLS ejecuciones en la ventana (delta desde el
#      snapshot anterior). Ademas evita re-reportar queries que ya no corren y
#      siguen en top_impact por tiempo acumulado.
#   3. el plan recorre completa esa tabla (full_table_scan) o un indice entero
#      sin usarlo para filtrar (full_index_scan, ver _FULL_SCAN_METHODS)
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
# con ventanas de 10 s (antes 10 en ventanas de 60 s: la misma frecuencia)
DEFAULT_MIN_WINDOW_CALLS = 2

# full_index_scan (MySQL `index`, Postgres Index Scan sin Index Cond) pasa por
# una entrada del indice por cada fila: en un SELECT simple examina toda la
# tabla igual que un full_table_scan. MySQL lo elige en vez de ALL cuando un
# indice cubre las columnas (ej. LOWER(email) = ... con indice en email), asi
# que sin esto la misma consulta se detecta en Postgres y no en MySQL. Solo
# dejaria de leer todo si algo corta antes (LIMIT, MIN/MAX, EXISTS), y eso ya
# lo excluye la condicion 1.
_FULL_SCAN_METHODS = ("full_table_scan", "full_index_scan")

# Igual que AP-07/AP-08: una query sin fila guardada cuyo counters_epoch esta a
# menos de esto de la hora del snapshot empezo a contar dentro de la ventana.
# Asume snapshots cada 10 s (EXTRACT_INTERVAL_S).
_NEW_QUERY_WINDOW = timedelta(seconds=10)

# Riesgo de aplicar la recomendacion (no la gravedad del problema): la
# recomendacion propone un indice o reescribir la condicion.
_RISK = (
    "Un indice nuevo ocupa espacio y encarece cada INSERT, UPDATE y DELETE "
    "sobre la tabla; crearlo en una tabla grande lleva tiempo y, en Postgres "
    "sin CONCURRENTLY, bloquea las escrituras mientras se construye. "
    "Reescribir la condicion cambia el codigo de la aplicacion: hay que "
    "comprobar que devuelve las mismas filas."
)


@dataclass
class _RowsWindow:
    calls: int
    rows: int
    # True si la query aparecio en esta ventana y se uso su acumulado completo
    new_query: bool = False
    # tiempo de base de datos de la sentencia en la ventana (el impacto del
    # hallazgo). None si el snapshot no trae el tiempo. No decide si hay
    # hallazgo: la regla solo mira ejecuciones y filas
    total_ms: float | None = None


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


def _current_counters(snapshot: SnapshotPayload) -> dict[str, tuple[int, int | None, float | None, str | None]]:
    # (ejecuciones, filas devueltas, tiempo total, counters_epoch) por query,
    # sumando las filas repetidas igual que la fila guardada en
    # statement_samples. Misma logica que AP-08.
    counters: dict[str, tuple[int, int | None, float | None, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None:
            continue
        key = str(statement.query_id)
        calls, rows, total_ms, epoch = counters.get(key, (0, None, None, statement.counters_epoch))
        if statement.rows_returned is not None:
            rows = (rows or 0) + statement.rows_returned
        if statement.total_time_ms is not None:
            total_ms = (total_ms or 0.0) + statement.total_time_ms
        counters[key] = (calls + statement.execution_count, rows, total_ms, epoch)
    return counters


def _rows_window(
    calls: int,
    rows: int | None,
    total_ms: float | None,
    epoch: datetime | None,
    previous: StatementHistory | None,
    previous_snapshot_at: datetime | None,
    captured_at: datetime,
) -> _RowsWindow | None:
    # Ejecuciones, filas devueltas y tiempo entre el snapshot anterior y este.
    # None si no se puede medir o si la query no se ejecuto en la ventana. Misma
    # logica que AP-08. El tiempo nunca descarta la ventana: solo es el impacto.
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
            return _RowsWindow(calls=calls, rows=rows, new_query=True, total_ms=total_ms)
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
    delta_ms = None if total_ms is None else max(total_ms - previous.total_time_ms, 0.0)
    return _RowsWindow(calls=delta_calls, rows=delta_rows, total_ms=delta_ms)


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
    # indexa los EXPLAIN por query_id para poder cruzarlos con cada candidato
    explains_by_query_id = {
        explain.query_id: explain
        for explain in snapshot.canonic_explains
        if explain.query_id is not None
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
        calls, rows, total_ms, epoch_raw = counters.get(
            query_id,
            (candidate.execution_count or 0, candidate.rows_returned, candidate.total_time_ms,
             candidate.counters_epoch),
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)
        window = _rows_window(
            calls, rows, total_ms, epoch, history.get(query_id), previous_snapshot_at, captured_at,
        )
        if window is None or window.calls < min_window_calls:
            continue

        alias_map = resolve_table_aliases(candidate.canonic_query)
        operations = explain.canonical_plan.physical_operations
        for operation in operations:
            # condicion 3: full scan sobre la tabla del SELECT
            if operation.type != "scan" or operation.access_method not in _FULL_SCAN_METHODS:
                continue
            relation = operation.relation
            # table_name: tabla real en Postgres (relation puede ser ql_pt_orders_1); None en MySQL
            if (operation.table_name or alias_map.get(relation, relation)) != table_name:
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

            if operation.access_method == "full_index_scan":
                scan_text = (
                    f"La consulta recorre completo un indice de la tabla {table_name} sin usarlo para "
                    f"filtrar (una entrada por cada una de sus {live_rows:,} filas vivas)"
                )
            else:
                scan_text = f"La consulta recorre completa la tabla {table_name} ({live_rows:,} filas vivas)"

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
                        "access_method": operation.access_method,
                        "index_name": operation.index_name,
                        "predicate": operation.predicate,
                        "live_rows": live_rows,
                        "rows_per_call": round(rows_per_call, 2),
                        "selectivity": selectivity,
                        # solo informativo: la regla usa las filas reales
                        "estimated_rows": operation.estimated_rows,
                        "window_calls": window.calls,
                        "window_rows": window.rows,
                        # impacto: tiempo de la sentencia en la ventana
                        "window_time_ms": window.total_ms,
                        "query_new_in_window": window.new_query,
                        "explain_source": explain.explain_source,
                        "mean_time_ms": candidate.mean_time_ms,
                        "total_time_ms": candidate.total_time_ms,
                        "execution_count": candidate.execution_count,
                    },
                    explicacion=(
                        f"{scan_text} "
                        f"y en la ultima ventana devolvio en promedio {rows_per_call:,.0f} filas por "
                        f"ejecucion ({selectivity:.2%} de la tabla) en {window.calls} ejecuciones."
                    ),
                    recomendacion=(
                        "Revisar el predicado de filtro contra los indices existentes en la tabla; "
                        "considerar un indice que lo cubra o reescribir la condicion para que sea sargable."
                    ),
                    query_id=candidate.query_id,
                    table_name=table_name,
                    database_name=candidate.database_name,
                    riesgo=_RISK,
                    impacto_ms=None if window.total_ms is None else round(window.total_ms, 3),
                )
            )
            # un SELECT simple tiene un solo escaneo completo de su tabla (un plan
            # bitmap de Postgres da dos scans, pero son index_lookup)
            break
    return findings
