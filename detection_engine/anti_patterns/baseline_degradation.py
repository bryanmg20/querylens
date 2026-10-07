from datetime import datetime
from statistics import median

from models import Hallazgo, Snapshot, StatementHistory, StatementSample


DEFAULT_MIN_CALLS = 30  # ejecuciones minimas en un intervalo para que su tiempo medio cuente
DEFAULT_BASELINE_WINDOW = 10  # cuantos intervalos validos forman la linea base
DEFAULT_MIN_BASELINE_SAMPLES = 5  # con menos intervalos que esto, no se opina
DEFAULT_MIN_RATIO = 2.0  # el intervalo actual tiene que ser al menos 2x la mediana
DEFAULT_MIN_ROBUST_Z = 3.0  # y alejarse al menos 3 "desvios robustos" (MAD) de ella

# Severidad por tiempo extra consumido en el intervalo (ms), no por el ratio:
# una query de microsegundos que se hace 4x mas lenta pesa menos que una de
# 50ms que se duplica con miles de llamadas
_HIGH_EXTRA_MS = 60_000
_MEDIUM_EXTRA_MS = 5_000

# MySQL arma stats_reset como NOW() - Uptime en segundos enteros, asi que puede
# moverse 1s entre snapshots sin que haya habido reinicio
_STATS_RESET_TOLERANCE_SECONDS = 5

# Factor que hace al MAD comparable con un desvio estandar si los datos fueran normales
_MAD_TO_STDDEV = 1.4826


def _snapshot_stats_reset(snapshot: Snapshot) -> str | None:
    if not snapshot.stats_reset_timestamp:
        return None
    raw = snapshot.stats_reset_timestamp[0].get("stats_reset")
    return str(raw) if raw else None


def _stats_reset_changed(previous: str | None, current: str | None) -> bool:
    # Sin dato de algun lado no se puede afirmar un reinicio; el delta negativo
    # sigue atrapando el caso comun
    if not previous or not current:
        return False
    try:
        delta = abs((datetime.fromisoformat(current) - datetime.fromisoformat(previous)).total_seconds())
    except (ValueError, TypeError):
        return previous != current
    return delta > _STATS_RESET_TOLERANCE_SECONDS


def _aggregate_statements(snapshot: Snapshot) -> dict[str, dict]:
    # pg_stat_statements separa el mismo queryid por userid/toplevel; el
    # snapshot descarta userid, asi que puede llegar repetido. Se suman los
    # contadores para que el delta no dependa de cual fila gano.
    aggregated: dict[str, dict] = {}
    for statement in snapshot.statements:
        if statement.query_id is None:
            continue
        if statement.execution_count is None or statement.total_time_ms is None:
            continue
        key = str(statement.query_id)
        entry = aggregated.setdefault(key, {"statement": statement, "calls": 0, "total_ms": 0.0})
        entry["calls"] += statement.execution_count
        entry["total_ms"] += statement.total_time_ms
    return aggregated


def compute_statement_samples(
    snapshot: Snapshot,
    history: dict[str, StatementHistory],
) -> list[StatementSample]:
    # Arma el sample actual de cada query_id con su intervalo contra el sample
    # anterior. Lo usa el detector y tambien main.py para guardar la serie.
    stats_reset = _snapshot_stats_reset(snapshot)
    samples = []
    for query_id, entry in _aggregate_statements(snapshot).items():
        sample = StatementSample(
            query_id=query_id,
            execution_count=entry["calls"],
            total_time_ms=entry["total_ms"],
            stats_reset=stats_reset,
        )

        previous = history.get(query_id, StatementHistory()).last_sample
        if previous is not None and not _stats_reset_changed(previous.stats_reset, stats_reset):
            delta_calls = sample.execution_count - previous.execution_count
            delta_total_ms = sample.total_time_ms - previous.total_time_ms
            # delta negativo = los contadores se reiniciaron (pg_stat_statements_reset,
            # entrada desalojada, etc.): ese intervalo no se puede medir
            if delta_calls > 0 and delta_total_ms >= 0:
                sample.interval_calls = delta_calls
                sample.interval_mean_ms = delta_total_ms / delta_calls

        samples.append(sample)
    return samples


def _severity(extra_ms: float) -> str:
    if extra_ms >= _HIGH_EXTRA_MS:
        return "high"
    if extra_ms >= _MEDIUM_EXTRA_MS:
        return "medium"
    return "low"


# Detecta queries cuyo tiempo medio del ultimo intervalo se salio de su propia linea base
def detect_baseline_degradation(
    snapshot: Snapshot,
    history: dict[str, StatementHistory] | None = None,
    min_calls: int = DEFAULT_MIN_CALLS,
    baseline_window: int = DEFAULT_BASELINE_WINDOW,
    min_baseline_samples: int = DEFAULT_MIN_BASELINE_SAMPLES,
    min_ratio: float = DEFAULT_MIN_RATIO,
    min_robust_z: float = DEFAULT_MIN_ROBUST_Z,
) -> list[Hallazgo]:
    # sin historia (ej. corriendo el engine contra un snapshot suelto) no hay linea base
    if not history:
        return []

    statements_by_id = {
        query_id: entry["statement"] for query_id, entry in _aggregate_statements(snapshot).items()
    }

    findings = []
    for sample in compute_statement_samples(snapshot, history):
        # intervalo no medible, o con pocas ejecuciones: una sola corrida atipica no es tendencia
        if sample.interval_mean_ms is None or (sample.interval_calls or 0) < min_calls:
            continue

        baseline_values = history[sample.query_id].recent_interval_means[-baseline_window:]
        if len(baseline_values) < min_baseline_samples:
            continue

        baseline_median = median(baseline_values)
        if baseline_median <= 0:
            continue

        ratio = sample.interval_mean_ms / baseline_median
        if ratio < min_ratio:
            continue

        # MAD en vez de desvio estandar: un intervalo atipico en la linea base no
        # la infla. Una query inestable tiene intervalos que saltan (MAD grande,
        # z bajo) y no se reporta: la degradacion exige que la media se mueva,
        # no solo la varianza. Funciona igual en ambos motores porque solo usa
        # total_time y calls (el stddev de MySQL es una aproximacion del collector).
        mad = median(abs(value - baseline_median) for value in baseline_values)
        robust_z = (
            (sample.interval_mean_ms - baseline_median) / (_MAD_TO_STDDEV * mad)
            if mad > 0 else None  # serie plana: el ratio alcanza para decidir
        )
        if robust_z is not None and robust_z < min_robust_z:
            continue

        extra_ms = sample.interval_calls * (sample.interval_mean_ms - baseline_median)
        statement = statements_by_id[sample.query_id]

        findings.append(
            Hallazgo(
                antipatron="baseline_degradation",
                severidad=_severity(extra_ms),
                evidencia={
                    "query_id": statement.query_id,
                    "query_text": statement.query_text,
                    "schema_name": statement.schema_name,
                    "interval_mean_ms": sample.interval_mean_ms,
                    "interval_calls": sample.interval_calls,
                    "baseline_median_ms": baseline_median,
                    "baseline_mad_ms": mad,
                    "baseline_samples": len(baseline_values),
                    "baseline_series_ms": baseline_values,
                    "ratio": ratio,
                    "robust_z": robust_z,
                    "extra_time_ms": extra_ms,
                    "cumulative_mean_time_ms": statement.mean_time_ms,
                },
                explicacion=(
                    f"En el ultimo intervalo esta consulta tardo en promedio {ratio:.1f}x lo que "
                    "suele tardar (mediana de sus intervalos anteriores), sin haber cambiado su "
                    "texto. No es un umbral fijo: se compara contra su propio historial."
                ),
                recomendacion=(
                    "Revisar que cambio desde que la consulta era rapida: plan de ejecucion "
                    "(estadisticas desactualizadas, indice eliminado), volumen de datos, "
                    "contencion por bloqueos o carga concurrente en el servidor."
                ),
                query_id=statement.query_id,
            )
        )
    return findings
