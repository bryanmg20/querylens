"""Diferencias entre la muestra de un ciclo y la del ciclo anterior.

Funciones puras: writer.py lee la muestra anterior de
pipeline_health.cycle_samples y guarda lo que devuelven. La muestra cruda la
arma stages/engine_counters.py.
"""
from datetime import datetime

from stages.pg_log import LOG_WINDOW_BYTES


def deadlocks_since(prev_value, prev_epoch, value, epoch) -> tuple[int | None, bool]:
    """(abrazos que conto el motor desde la muestra anterior, si el contador
    volvio a cero en el medio). Tras un reinicio solo se sabe el valor actual:
    es una cota inferior de lo que paso entre las dos muestras."""
    if value is None or prev_value is None:
        return None, False
    if epoch != prev_epoch or value < prev_value:
        return value, True
    return value - prev_value, False


def log_bytes_since(prev_files: list[dict] | None, files: list[dict] | None) -> int | None:
    """Bytes que se escribieron en el log entre los dos listados.

    Un archivo que no estaba, o que ahora es mas chico (log_truncate_on_rotation
    reutiliza postgresql-%a.log cada semana), se escribio entero desde cero. Los
    que desaparecieron no suman: lo que tenian ya estaba en el listado anterior."""
    if prev_files is None or files is None:
        return None
    before = {log_file["name"]: int(log_file["size"]) for log_file in prev_files}
    total = 0
    for log_file in files:
        size = int(log_file["size"])
        previous = before.get(log_file["name"])
        if previous is None or size < previous:
            total += size
        else:
            total += size - previous
    return total


def derive(prev: dict | None, sample: dict) -> dict:
    """Columnas calculadas de la muestra. prev es la fila anterior de la misma
    base (None en la primera, o si el servicio la purgo)."""
    if prev is None:
        return {
            "deadlocks_since_prev": None,
            "counter_reset": False,
            "log_bytes_since_prev": None,
            "seconds_since_prev": None,
            "log_window_overrun": None,
        }
    deadlocks, reset = deadlocks_since(
        prev["engine_deadlocks"], prev["counter_epoch"],
        sample["engine_deadlocks"], sample["counter_epoch"],
    )
    log_bytes = log_bytes_since(prev["log_files"], sample["log_files"])
    return {
        "deadlocks_since_prev": deadlocks,
        "counter_reset": reset,
        "log_bytes_since_prev": log_bytes,
        "seconds_since_prev": _seconds(prev["taken_at"], sample["taken_at"]),
        "log_window_overrun": None if log_bytes is None else log_bytes > LOG_WINDOW_BYTES,
    }


def _seconds(before: datetime, after: datetime) -> float:
    return (after - before).total_seconds()
