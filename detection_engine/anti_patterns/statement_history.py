from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterator

from models import Snapshot, Statement, StatementHistory


# Estado por query que se guarda en public.statement_samples entre un snapshot
# y el siguiente. Ningun detector escribe esta tabla: main.py la carga antes de
# correr los detectores (que solo la leen) y guarda la version nueva despues,
# con next_statement_histories. Por eso el orden de los detectores no importa.

# Solo las ventanas con al menos estas ejecuciones entran a la lista de
# ventanas: es el criterio de linea base de AP-01 (su promedio depende
# demasiado de una sola ejecucion atipica si hay pocas)
WINDOW_MIN_CALLS = 30

# Ventanas que se guardan por query: AP-01 usa solo las ultimas 10, el resto
# queda para la linea de tiempo de la interfaz
HISTORY_WINDOWS = 60

# Una query que no se ejecuto en este tiempo pierde su fila (y su historia)
STALE_AFTER = timedelta(hours=24)


@dataclass
class Window:
    calls: int
    total_ms: float
    mean_ms: float


@dataclass
class _Aggregated:
    statement: Statement
    calls: int
    total_ms: float
    disk_spill: int | None
    rows: int | None


def parse_instant(value) -> datetime | None:
    # counters_epoch llega como texto: Postgres con zona horaria, MySQL sin ella.
    # Se compara como instante y no como texto, para que un cambio de formato
    # en el pipeline (ej. "T" en vez de espacio) no parezca un reinicio.
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    # sin zona (MySQL) se asume UTC: siempre se compara contra valores del mismo motor
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _aggregate_statements(snapshot: Snapshot) -> dict[str, _Aggregated]:
    # pg_stat_statements separa el mismo queryid por userid/toplevel; el
    # snapshot descarta userid, asi que puede llegar repetido. Se suman los
    # contadores para que el delta no dependa de cual fila gano.
    aggregated: dict[str, _Aggregated] = {}
    for statement in snapshot.statements:
        if statement.query_id is None:
            continue
        if statement.execution_count is None or statement.total_time_ms is None:
            continue
        key = str(statement.query_id)
        entry = aggregated.setdefault(key, _Aggregated(statement, 0, 0.0, None, None))
        entry.calls += statement.execution_count
        entry.total_ms += statement.total_time_ms
        if statement.disk_spill_indicator is not None:
            entry.disk_spill = (entry.disk_spill or 0) + statement.disk_spill_indicator
        if statement.rows_returned is not None:
            entry.rows = (entry.rows or 0) + statement.rows_returned
    return aggregated


def _window_since(
    previous: StatementHistory | None,
    calls: int,
    total_ms: float,
    epoch: datetime | None,
) -> Window | None:
    # Ventana = lo que paso entre el sample anterior y este. None si no se puede medir.
    if previous is None:
        return None
    # los contadores se reiniciaron (reinicio de MySQL, reset de estadisticas,
    # Postgres desalojo la entrada): la resta mezclaria dos periodos distintos.
    # Sin epoch de algun lado, queda solo el chequeo de delta negativo.
    if epoch is not None and previous.counters_epoch is not None and epoch != previous.counters_epoch:
        return None
    delta_calls = calls - previous.execution_count
    delta_total_ms = total_ms - previous.total_time_ms
    if delta_calls <= 0 or delta_total_ms < 0:
        return None
    return Window(calls=delta_calls, total_ms=delta_total_ms, mean_ms=delta_total_ms / delta_calls)


def evaluate_statements(
    snapshot: Snapshot,
    history: dict[str, StatementHistory],
    captured_at: datetime,
    min_calls: int = WINDOW_MIN_CALLS,
) -> Iterator[tuple[Statement, Window | None, StatementHistory | None, StatementHistory | None]]:
    # Por cada query del snapshot devuelve: su statement, la ventana actual (o
    # None), la fila anterior y la fila nueva a guardar (o None si no hay nada
    # que actualizar). No modifica `history`.
    for query_id, entry in _aggregate_statements(snapshot).items():
        previous = history.get(query_id)
        epoch = parse_instant(entry.statement.counters_epoch)

        # no se ejecuto desde el sample anterior: no hay nada nuevo que guardar.
        # Saltarlo no pierde nada, la proxima resta cubre el mismo periodo.
        if (
            previous is not None
            and entry.calls == previous.execution_count
            and (epoch is None or epoch == previous.counters_epoch)
        ):
            yield entry.statement, None, previous, None
            continue

        window = _window_since(previous, entry.calls, entry.total_ms, epoch)

        window_means = list(previous.window_means_ms) if previous else []
        window_ends = list(previous.window_ends_at) if previous else []
        if window is not None and window.calls >= min_calls:
            window_means = (window_means + [window.mean_ms])[-HISTORY_WINDOWS:]
            window_ends = (window_ends + [captured_at])[-HISTORY_WINDOWS:]

        base = previous or StatementHistory(
            query_id=query_id, captured_at=captured_at, execution_count=0, total_time_ms=0.0,
        )
        updated = replace(
            base,
            captured_at=captured_at,
            execution_count=entry.calls,
            total_time_ms=entry.total_ms,
            disk_spill_indicator=entry.disk_spill,
            rows_returned=entry.rows,
            counters_epoch=epoch,
            window_means_ms=window_means,
            window_ends_at=window_ends,
        )
        yield entry.statement, window, previous, updated


def next_statement_histories(
    snapshot: Snapshot,
    history: dict[str, StatementHistory],
    captured_at: datetime,
) -> list[StatementHistory]:
    # Filas a guardar despues de este snapshot. Lo usa main.py, despues de
    # correr todos los detectores.
    return [
        updated
        for _, _, _, updated in evaluate_statements(snapshot, history, captured_at)
        if updated is not None
    ]
