from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from querylens_contract import SnapshotPayload, StatementCandidate

from models import Hallazgo, StatementHistory

from .table_aliases import resolve_table_aliases


# Regla AP-07, igual para ambos motores: la query escribio algo en disco
# durante la ultima ventana (alguno de sus indicadores de vertido aumento
# respecto al snapshot anterior). No se compara un umbral numerico porque los
# indicadores no miden lo mismo en cada motor:
#   - Postgres: bloques de 8 KB (temp_blks_written), que ya incluyen
#     ordenamientos, hashes y tablas temporales;
#   - MySQL: tablas temporales creadas en disco (SUM_CREATED_TMP_DISK_TABLES) y,
#     por separado, rondas de mezcla de los ordenamientos que fueron a disco
#     (SUM_SORT_MERGE_PASSES). Un ORDER BY que no cabe en sort_buffer_size solo
#     sube el segundo.
# Lo que si significa lo mismo en todos es que aumenten: hubo spill.
#
# Limitacion (MySQL 8.0, medida en ql_sandbox/mediciones): con varias consultas
# derramando a la vez, SUM_CREATED_TMP_DISK_TABLES solo aumenta en algunas
# sesiones (33 de 100 casos con temptable_use_mmap = OFF), y con
# temptable_use_mmap = ON el desborde a archivos mmap no se cuenta. Las merge
# passes si se contaron en todos los casos.

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
_UNIT_SORT = "merge_passes"


@dataclass
class _SpillWindow:
    calls: int
    total_ms: float
    # aumento de disk_spill_indicator en la ventana (None si no se pudo medir)
    spill: int | None
    # aumento de sort_merge_passes (solo MySQL; None en Postgres o si no se pudo medir)
    merge_passes: int | None
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


def _current_counters(snapshot: SnapshotPayload) -> dict[str, tuple[int, float, int | None, int | None, str | None]]:
    # (ejecuciones, tiempo total, spill, merge passes, counters_epoch) por query,
    # sumando las filas repetidas igual que la fila guardada en statement_samples:
    # si no, la resta compararia el valor de una sola fila contra la suma de todas
    counters: dict[str, tuple[int, float, int | None, int | None, str | None]] = {}
    for statement in snapshot.statements:
        if statement.query_id is None or statement.execution_count is None or statement.total_time_ms is None:
            continue
        key = str(statement.query_id)
        calls, total_ms, spill, merges, epoch = counters.get(key, (0, 0.0, None, None, statement.counters_epoch))
        if statement.disk_spill_indicator is not None:
            spill = (spill or 0) + statement.disk_spill_indicator
        if statement.sort_merge_passes is not None:
            merges = (merges or 0) + statement.sort_merge_passes
        counters[key] = (calls + statement.execution_count, total_ms + statement.total_time_ms, spill, merges, epoch)
    return counters


def _spill_window(
    calls: int,
    total_ms: float,
    spill: int | None,
    merges: int | None,
    epoch: datetime | None,
    previous: StatementHistory | None,
    previous_snapshot_at: datetime | None,
    captured_at: datetime,
) -> _SpillWindow | None:
    # Lo que la query escribio a disco en la ultima ventana. None si no se puede medir.
    if spill is None and merges is None:
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
            return _SpillWindow(calls=calls, total_ms=total_ms, spill=spill, merge_passes=merges, new_query=True)
        return None

    # contadores reiniciados (reinicio, reset de estadisticas, entrada desalojada)
    if epoch is not None and previous.counters_epoch is not None and epoch != previous.counters_epoch:
        return None

    delta_calls = calls - previous.execution_count
    delta_total_ms = total_ms - previous.total_time_ms
    if delta_calls < 0 or delta_total_ms < 0:
        return None
    # cada indicador se resta solo si la fila guardada lo tiene (una fila anterior
    # a su columna aun no tiene con que restar); un delta negativo es un reinicio
    delta_spill = _delta(spill, previous.disk_spill_indicator)
    delta_merges = _delta(merges, previous.sort_merge_passes)
    if (delta_spill is not None and delta_spill < 0) or (delta_merges is not None and delta_merges < 0):
        return None
    if delta_spill is None and delta_merges is None:
        return None
    return _SpillWindow(calls=delta_calls, total_ms=delta_total_ms, spill=delta_spill,
                        merge_passes=delta_merges, new_query=False)


def _delta(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return current - previous


def _severity(window_ms: float) -> str:
    if window_ms >= _HIGH_WINDOW_MS:
        return "high"
    if window_ms >= _MEDIUM_WINDOW_MS:
        return "medium"
    return "low"


def _unit(candidate: StatementCandidate) -> str:
    # Postgres entrega queryid como entero y MySQL el DIGEST como texto; mientras
    # los dos snapshots compartan db_id, es la forma de saber el motor
    return _UNIT_POSTGRES if isinstance(candidate.query_id, int) else _UNIT_MYSQL


# Detecta consultas que escribieron datos temporales en disco en la ultima ventana
def detect_disk_spill(
    snapshot: SnapshotPayload,
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
        calls, total_ms, spill, merges, epoch_raw = counters.get(
            query_id,
            (candidate.execution_count or 0, candidate.total_time_ms or 0.0,
             candidate.disk_spill_indicator, candidate.sort_merge_passes, candidate.counters_epoch),
        )
        epoch = _parse_instant(candidate.counters_epoch or epoch_raw)

        window = _spill_window(
            calls, total_ms, spill, merges, epoch, history.get(query_id), previous_snapshot_at, captured_at,
        )
        if window is None:
            continue
        spilled_table = (window.spill or 0) > 0
        spilled_sort = (window.merge_passes or 0) > 0
        if not (spilled_table or spilled_sort):
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
                    # MySQL: ordenamientos que fueron a disco en la ventana
                    "window_sort_merge_passes": window.merge_passes,
                    "sort_merge_passes_unit": _UNIT_SORT if window.merge_passes is not None else None,
                    # que vertio. Solo MySQL lo separa (sort_merge_passes llega en
                    # None en Postgres, cuyo temp_blks_written mezcla todo)
                    "tipo_vertido": _kind(spilled_table, spilled_sort, window.merge_passes is not None),
                    "window_calls": window.calls,
                    # impacto: tiempo de la query en la ventana (define la severidad)
                    "window_time_ms": window.total_ms,
                    "query_new_in_window": window.new_query,
                    # acumulados desde counters_epoch, como referencia
                    "disk_spill_indicator": spill,
                    "sort_merge_passes": merges,
                    "execution_count": calls,
                    "total_time_ms": total_ms,
                    "mean_time_ms": candidate.mean_time_ms,
                    "counters_epoch": candidate.counters_epoch or epoch_raw,
                },
                explicacion=_explanation(spilled_table, spilled_sort),
                recomendacion=_recommendation(spilled_table, spilled_sort),
                query_id=candidate.query_id,
                table_name=", ".join(tables) if tables else None,
            )
        )
    return findings


def _kind(spilled_table: bool, spilled_sort: bool, engine_separates: bool) -> str:
    if not engine_separates:
        # Postgres: ordenamientos, hashes y tablas temporales en un solo indicador
        return "sin_detalle"
    if spilled_table and spilled_sort:
        return "tabla_temporal_y_ordenamiento"
    return "ordenamiento" if spilled_sort else "tabla_temporal"


def _explanation(spilled_table: bool, spilled_sort: bool) -> str:
    if spilled_sort and not spilled_table:
        return (
            "En la ultima ventana la consulta ordeno filas usando el disco: el ordenamiento no cupo "
            "en el buffer de ordenamiento, asi que se escribio por partes en archivos temporales y "
            "despues se mezclo, lo que aumenta la latencia y la presion de I/O."
        )
    return (
        "En la ultima ventana la consulta escribio datos temporales en disco: una "
        "agrupacion u ordenamiento no cupo en la memoria de trabajo, lo que aumenta la "
        "latencia y la presion de I/O."
    )


def _recommendation(spilled_table: bool, spilled_sort: bool) -> str:
    if spilled_sort and not spilled_table:
        return (
            "Evaluar un indice que entregue las filas ya ordenadas por las columnas del ORDER BY, "
            "reducir las filas que se ordenan (filtros, LIMIT) o, si el ordenamiento es inevitable, "
            "aumentar sort_buffer_size solo en la sesion que lo necesita."
        )
    return (
        "Revisar el plan y el volumen de datos; evaluar filtros, agrupaciones, ordenamientos "
        "e indices antes de aumentar memoria global."
    )
