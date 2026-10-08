from datetime import datetime, timezone
from statistics import median

from models import Hallazgo, Snapshot, StatementHistory

from .statement_history import WINDOW_MIN_CALLS, evaluate_statements


# Regla AP-01 del SegundoInforme (Tabla 2): huella con >= 30 ejecuciones en la
# ventana cuya latencia media es >= 2,0 veces la mediana de las 10 ventanas
# previas y al menos 1 ms mayor en valor absoluto. La lista de ventanas la
# arma statement_history.py con el mismo minimo de ejecuciones.
DEFAULT_MIN_CALLS = WINDOW_MIN_CALLS
DEFAULT_BASELINE_WINDOW = 10
DEFAULT_MIN_RATIO = 2.0
DEFAULT_MIN_ABS_INCREASE_MS = 1.0

# Severidad por tiempo extra consumido en la ventana (ms), no por el ratio:
# una query de 2ms que se duplica con 40 llamadas pesa menos que una de 50ms
# que se duplica con miles. Con ventanas de 60s, 60.000ms extra equivale a un
# nucleo de CPU ocupado todo el minuto solo por la degradacion.
_HIGH_EXTRA_MS = 60_000
_MEDIUM_EXTRA_MS = 5_000

# Limitacion / pendiente: estos cortes, el minimo de 30 ejecuciones y las 10
# ventanas de linea base asumen snapshots cada 60 s (la ventana del informe).
# Con otro intervalo cambian de significado: cada 5 min, 60 s de tiempo extra
# es mas facil de alcanzar (mas hallazgos high), 30 ejecuciones es un umbral 5
# veces mas bajo y la linea base cubre 50 min en vez de 10. Por hacer: medir la
# severidad como proporcion del tiempo REAL de la ventana (segundos de base de
# datos por segundo de ventana, con captured_at de la fila anterior) en vez de
# ms absolutos; el minimo de ejecuciones y las 10 ventanas son reglas del
# informe para 60 s y habria que revisarlas con el sandbox si cambia el intervalo.

# Limitacion conocida: una query que siempre fue erratica (ventanas que saltan
# entre 1 y 9ms, por ejemplo) puede pasar el ratio y el minimo absoluto sin
# haberse degradado de verdad, porque la regla no mira cuanto varia la query
# normalmente. Una mejora posible es exigir ademas un z robusto
# (valor - mediana) / (1.4826 * MAD) >= 3, que descarta lo que esta dentro de
# la variacion habitual de esa query. No se usa porque no forma parte de la
# regla declarada en el informe; si se agrega, hay que declararlo ahi primero.


def _severity(extra_ms: float) -> str:
    if extra_ms >= _HIGH_EXTRA_MS:
        return "high"
    if extra_ms >= _MEDIUM_EXTRA_MS:
        return "medium"
    return "low"


# Detecta queries cuyo tiempo medio de la ultima ventana se salio de su propia linea base
def detect_baseline_degradation(
    snapshot: Snapshot,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
    min_calls: int = DEFAULT_MIN_CALLS,
    baseline_window: int = DEFAULT_BASELINE_WINDOW,
    min_ratio: float = DEFAULT_MIN_RATIO,
    min_abs_increase_ms: float = DEFAULT_MIN_ABS_INCREASE_MS,
) -> list[Hallazgo]:
    # sin historia (ej. corriendo el engine contra un snapshot suelto) no hay linea base
    if not history:
        return []
    captured_at = captured_at or datetime.now(timezone.utc)

    findings = []
    for statement, window, previous, _ in evaluate_statements(snapshot, history, captured_at, min_calls):
        # ventana no medible o con pocas ejecuciones: una corrida atipica no es tendencia
        if window is None or window.calls < min_calls or previous is None:
            continue

        # la linea base son las ventanas ANTERIORES; exige las 10 completas
        baseline_values = previous.window_means_ms[-baseline_window:]
        if len(baseline_values) < baseline_window:
            continue

        baseline_median = median(baseline_values)
        if baseline_median <= 0:
            continue

        ratio = window.mean_ms / baseline_median
        increase_ms = window.mean_ms - baseline_median
        # el minimo absoluto descarta "4x mas lento" que en realidad son microsegundos
        if ratio < min_ratio or increase_ms < min_abs_increase_ms:
            continue

        extra_ms = window.calls * increase_ms
        timeline = [
            {"at": at.isoformat(), "mean_ms": mean_ms}
            for at, mean_ms in zip(previous.window_ends_at, previous.window_means_ms)
        ] + [{"at": captured_at.isoformat(), "mean_ms": window.mean_ms}]

        findings.append(
            Hallazgo(
                antipatron="baseline_degradation",
                severidad=_severity(extra_ms),
                evidencia={
                    "query_id": statement.query_id,
                    "query_text": statement.query_text,
                    "schema_name": statement.schema_name,
                    "window_mean_ms": window.mean_ms,
                    "window_calls": window.calls,
                    "baseline_median_ms": baseline_median,
                    "baseline_series_ms": baseline_values,
                    "ratio": ratio,
                    "increase_ms": increase_ms,
                    # tiempo de mas que costo la degradacion en la ventana (define la severidad)
                    "extra_time_ms": extra_ms,
                    # impacto segun el informe: tiempo total de la sentencia en la ventana (para priorizar)
                    "impact_ms": window.total_ms,
                    "counters_epoch": statement.counters_epoch,
                    # ultimas ventanas validas + la actual, para la linea de tiempo de la interfaz
                    "timeline": timeline,
                },
                explicacion=(
                    f"En la ultima ventana esta consulta tardo en promedio {window.mean_ms:.2f} ms, "
                    f"{ratio:.1f}x lo que suele tardar ({baseline_median:.2f} ms, mediana de sus "
                    f"{baseline_window} ventanas anteriores), sin haber cambiado su texto. No es un "
                    "umbral fijo: se compara contra su propio historial."
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
