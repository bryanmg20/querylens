from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from models import CandidateStatement, Hallazgo, Snapshot, StatementHistory

from .table_aliases import resolve_table_aliases


# Regla AP-07, igual para ambos motores: la query escribio algo en disco
# durante la ultima ventana (su disk_spill_indicator aumento respecto al
# snapshot anterior). No se compara un umbral numerico porque el indicador no
# mide lo mismo en cada motor: bloques de 8 KB en Postgres (temp_blks_written)
# y tablas temporales creadas en disco en MySQL (SUM_CREATED_TMP_DISK_TABLES).
# Lo que si significa lo mismo en los dos es que aumente: hubo spill.
#
# Limitacion: en MySQL los ordenamientos que van a disco (SUM_SORT_MERGE_PASSES)
# no se recolectan todavia, asi que solo se ven las tablas temporales en disco.

# Severidad por el tiempo que la query consumio en la ventana (igual que AP-01):
# se mide igual en ambos motores y es el impacto que usa el informe para priorizar
_HIGH_WINDOW_MS = 60_000
_MEDIUM_WINDOW_MS = 5_000

# Una query sin fila guardada cuyo counters_epoch esta a menos de esto de la hora
# del snapshot empezo a contar dentro de la ventana: todo su acumulado es de esa
# ventana. Es el largo de la ventana de evaluacion del informe (60 s).
_NEW_QUERY_WINDOW = timedelta(seconds=60)

# Limitacion / pendiente: _NEW_QUERY_WINDOW y los cortes de severidad asumen
# snapshots cada 60 s. Con otro intervalo: una query nueva que empezo hace mas
# de 60 s solo se reconoce por la otra condicion (empezo despues del snapshot
# anterior), y los cortes de 60 s / 5 s de tiempo en la ventana se alcanzan mas
# facil en ventanas largas. Por hacer: severidad como proporcion del tiempo
# REAL de la ventana (con captured_at de la fila anterior) y, para la libreta
# vacia, tomar el intervalo de snapshots de la configuracion.

# unidad del indicador segun el motor, solo para la evidencia
_UNIT_POSTGRES = "bloques_8kb"
_UNIT_MYSQL = "tablas_temporales_disco"


@dataclass
class _SpillWindow:
    calls: int
    total_ms: float
    spill: int
    # True si la query aparecio en esta ventana y se uso su acumulado completo
    new_query: bool


# Misma conversion que statement_history.parse_instant; se duplica a proposito
# hasta confirmar AP-07 por separado, como el resto de la logica de ventana.
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


def _current_counters(snapshot: Snapshot) -> dict[str, tuple[int, float, int | None, str | None]]:
    # (ejecuciones, tiempo total, spill, counters_epoch) por query, sumando las
    # filas repetidas igual que la fila guardada en statement_samples: si no, la
    # resta compararia el valor de una sola fila contra la suma de todas
    counters: dict[str, tuple[int, float, int | None, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None or statement.total_time_ms is None:
            continue
        key = str(statement.query_id)
        calls, total_ms, spill, epoch = counters.get(key, (0, 0.0, None, statement.counters_epoch))
        if statement.disk_spill_indicator is not None:
            spill = (spill or 0) + statement.disk_spill_indicator
        counters[key] = (calls + statement.execution_count, total_ms + statement.total_time_ms, spill, epoch)
    return counters


def _spill_window(
    calls: int,
    total_ms: float,
    spill: int | None,
    epoch: datetime | None,
    previous: StatementHistory | None,
    previous_snapshot_at: datetime | None,
    captured_at: datetime,
) -> _SpillWindow | None:
    # Lo que la query escribio a disco en la ultima ventana. None si no se puede medir.
    if spill is None:
        return None

    if previous is None:
        # sin fila anterior: solo se puede medir si la query empezo a contar
        # dentro de esta ventana (ej. tabla recreada en Postgres, que le cambia
        # el queryid). En ese caso todo su acumulado es de esta ventana. Vale si:
        #  - empezo hace menos de una ventana: no depende de que haya un snapshot
        #    anterior (base recien creada) ni de cual sea (db_id compartido entre
        #    motores, donde el "anterior" puede ser el del otro motor), o
        #  - empezo despues del snapshot anterior: cubre un hueco largo entre
        #    snapshots (ej. pipeline caido varios minutos)
        if epoch is None:
            return None
        started_in_window = captured_at - epoch <= _NEW_QUERY_WINDOW
        started_after_previous = previous_snapshot_at is not None and epoch > previous_snapshot_at
        if started_in_window or started_after_previous:
            return _SpillWindow(calls=calls, total_ms=total_ms, spill=spill, new_query=True)
        return None

    # fila guardada antes de que existiera esta columna: aun no hay con que restar
    if previous.disk_spill_indicator is None:
        return None
    # contadores reiniciados (reinicio, reset de estadisticas, entrada desalojada)
    if epoch is not None and previous.counters_epoch is not None and epoch != previous.counters_epoch:
        return None

    delta_calls = calls - previous.execution_count
    delta_total_ms = total_ms - previous.total_time_ms
    delta_spill = spill - previous.disk_spill_indicator
    if delta_calls < 0 or delta_total_ms < 0 or delta_spill < 0:
        return None
    return _SpillWindow(calls=delta_calls, total_ms=delta_total_ms, spill=delta_spill, new_query=False)


def _severity(window_ms: float) -> str:
    if window_ms >= _HIGH_WINDOW_MS:
        return "high"
    if window_ms >= _MEDIUM_WINDOW_MS:
        return "medium"
    return "low"


def _unit(candidate: CandidateStatement) -> str:
    # Postgres entrega queryid como entero y MySQL el DIGEST como texto; mientras
    # los dos snapshots compartan db_id, es la forma de saber el motor
    return _UNIT_POSTGRES if isinstance(candidate.query_id, int) else _UNIT_MYSQL


# Detecta consultas que escribieron datos temporales en disco en la ultima ventana
def detect_disk_spill(
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

    findings = []
    for candidate in snapshot.top_impact_queries:
        if candidate.query_id is None:
            continue
        query_id = str(candidate.query_id)

        # contadores desde statements (suma de filas repetidas); si el candidato
        # no aparece ahi, se usan los suyos
        calls, total_ms, spill, epoch_raw = counters.get(
            query_id,
            (candidate.execution_count or 0, candidate.total_time_ms or 0.0,
             candidate.disk_spill_indicator, candidate.counters_epoch),
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)

        window = _spill_window(
            calls, total_ms, spill, epoch, history.get(query_id), previous_snapshot_at, captured_at,
        )
        if window is None or window.spill <= 0:
            continue

        # el spill es de la ejecucion completa (sort/hash), no de una relacion
        # puntual del plan, asi que se listan todas las tablas de la query
        alias_map = resolve_table_aliases(candidate.canonic_query)
        tables = sorted(set(alias_map.values()))

        findings.append(
            Hallazgo(
                antipatron="disk_spill",
                severidad=_severity(window.total_ms),
                evidencia={
                    "query_id": candidate.query_id,
                    "query_text": candidate.query_text,
                    "canonic_query": candidate.canonic_query,
                    "schema_name": candidate.schema_name,
                    "tables": tables,
                    # lo escrito a disco en la ventana, en la unidad de cada motor
                    "window_disk_spill": window.spill,
                    "disk_spill_unit": _unit(candidate),
                    "window_calls": window.calls,
                    # impacto: tiempo de la query en la ventana (define la severidad)
                    "window_time_ms": window.total_ms,
                    "query_new_in_window": window.new_query,
                    # acumulados desde counters_epoch, como referencia
                    "disk_spill_indicator": spill,
                    "execution_count": calls,
                    "total_time_ms": total_ms,
                    "mean_time_ms": candidate.mean_time_ms,
                    "counters_epoch": candidate.counters_epoch or epoch_raw,
                },
                explicacion=(
                    "En la ultima ventana la consulta escribio datos temporales en disco: una "
                    "agrupacion u ordenamiento no cupo en la memoria de trabajo, lo que aumenta la "
                    "latencia y la presion de I/O."
                ),
                recomendacion=(
                    "Revisar el plan y el volumen de datos; evaluar filtros, agrupaciones, ordenamientos "
                    "e indices antes de aumentar memoria global."
                ),
                query_id=candidate.query_id,
                table_name=", ".join(tables) if tables else None,
            )
        )
    return findings
