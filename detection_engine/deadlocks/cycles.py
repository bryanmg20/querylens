"""Lo comun de los abrazos mortales (CT-01 a CT-04 y los sin clasificar).

El ciclo sale del informe que el motor escribe al revertir la victima
(snapshot.deadlocks): el abrazo dura lo que tarda el motor en detectarlo y no
aparece en las muestras de active_queries.

Todo lo de aca lee solo el vocabulario comun del contrato (mode, scope,
targets, holds), nunca el del motor. None en un campo es "el motor no lo
informa": cada patron decide que puede afirmar sin ese dato.
"""
import hashlib
from datetime import datetime

from querylens_contract import DeadlockLock, DeadlockReport, DeadlockTransaction

from anti_patterns.statement_history import parse_instant

# Un hueco o una insercion en un hueco no es un bloqueo de la fila: CT-03.
GAP_SCOPES = {"gap", "insert_intention"}

# Lo que frena una insercion en un hueco: el hueco solo o la fila con el hueco
# anterior (next-key). Al final del indice InnoDB bloquea el supremum como
# next-key, sin "gap before rec".
GAP_HOLD_SCOPES = {"gap", "row+gap"}

# CT-04 (Tabla 3): un UPDATE/DELETE sin indice utilizable mantiene >= 10 veces
# mas bloqueos de fila que filas modificadas. Su ciclo se parece a CT-01 o
# CT-02, pero la causa y la recomendacion son otras.
_LOCK_AMPLIFICATION_RATIO = 10

# Cuando la fila de cada espera no sale de los valores de la sentencia sino
# de su direccion fisica en el log (Postgres con log_lock_waits).
_TARGETS_FROM_CTID = (
    "La fila de cada espera sale de su direccion fisica (ctid) en el aviso "
    "de espera del log, no de los valores de la sentencia: dice si las "
    "transacciones esperaban la misma fila o filas distintas, pero no cual."
)

# Severidad: un abrazo aislado lo resuelve el motor y la aplicacion reintenta;
# el mismo patron repetido es un defecto de diseño que ya cuesta transacciones
# revertidas. 3 es el valor inicial a calibrar.
_HIGH_RECURRENCE = 3

# El error que recibe la victima. Es texto para la recomendacion: las reglas
# no dependen del motor.
_DEADLOCK_ERROR = {
    "postgres_log": "SQLSTATE 40P01",
    "innodb_status": "error 1213",
}


def is_cycle(report: DeadlockReport) -> bool:
    """Informe completo de >= 2 transacciones en el que cada una espera a otra
    del mismo informe (con grado de salida >= 1 en un grafo finito hay ciclo)."""
    transactions = report.transactions
    if not report.complete or len(transactions) < 2:
        return False
    if any(trx.waits_for is None for trx in transactions):
        return False
    pids = {trx.process_id for trx in transactions}
    return all(any(pid in pids for pid in trx.blocked_by) for trx in transactions)


def requests_exclusive(lock: DeadlockLock) -> bool:
    """Pide X de la fila (con o sin el hueco) o de la tabla. Sin modo
    informado no se descarta."""
    return lock.mode in ("X", None) and lock.scope not in GAP_SCOPES


def exclusive(lock: DeadlockLock) -> bool:
    return lock.mode == "X" and lock.scope not in GAP_SCOPES


def shared(lock: DeadlockLock) -> bool:
    return lock.mode == "S" and lock.scope not in GAP_SCOPES


def targets_known(report: DeadlockReport) -> bool:
    return all(trx.waits_for.targets for trx in report.transactions)


def holds_known(report: DeadlockReport) -> bool:
    return all(trx.holds is not None for trx in report.transactions)


def holds_on(trx: DeadlockTransaction, wanted: DeadlockLock, kind) -> bool:
    """trx mantiene, en el modo que acepta kind, un bloqueo sobre lo que pide wanted."""
    return any(kind(lock) and set(lock.targets or ()) & set(wanted.targets) for lock in trx.holds)


def holders(report: DeadlockReport, trx: DeadlockTransaction) -> list[DeadlockTransaction]:
    by_pid = {other.process_id: other for other in report.transactions}
    return [by_pid[pid] for pid in trx.blocked_by if pid in by_pid]


def lock_ratio(trx: DeadlockTransaction) -> float | None:
    """Filas bloqueadas por fila modificada, o None si el motor no lo informa."""
    if trx.rows_locked is None:
        return None
    return trx.rows_locked / max(trx.undo_log_entries or 0, 1)


def is_amplified(trx: DeadlockTransaction) -> bool:
    ratio = lock_ratio(trx)
    return ratio is not None and ratio >= _LOCK_AMPLIFICATION_RATIO


def amplified(report: DeadlockReport) -> bool:
    return any(is_amplified(trx) for trx in report.transactions)


def signature_parts(report: DeadlockReport) -> list[tuple[str, str, str, str]]:
    """(huella, tabla, indice, modo) de lo que espera cada transaccion, ordenado:
    no depende de que transaccion fue la victima ni del orden del informe. El
    modo es el comun (X,row), no el del motor."""
    parts = []
    for trx in report.transactions:
        lock = trx.waits_for
        parts.append((
            trx.canonic_query or "",
            qualified(lock.schema_name, lock.table_name) if lock else "",
            (lock.index_name or "") if lock else "",
            f"{lock.mode or ''},{lock.scope or ''}" if lock else "",
        ))
    return sorted(parts)


def deadlock_signature(report: DeadlockReport, antipatron: str) -> str:
    """Con el patron adelante: el mismo par de sentencias puede formar un
    CT-01 un dia y un CT-02 otro, y cada uno cuenta su recurrencia."""
    raw = "\n".join([antipatron, *("\x1f".join(part) for part in signature_parts(report))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def qualified(schema: str | None, table: str | None) -> str:
    if not table:
        return ""
    return f"{schema}.{table}" if schema else table


def tables(report: DeadlockReport) -> list[str]:
    return sorted({
        qualified(trx.waits_for.schema_name, trx.waits_for.table_name)
        for trx in report.transactions if trx.waits_for and trx.waits_for.table_name
    })


def victim(report: DeadlockReport) -> DeadlockTransaction | None:
    return next((trx for trx in report.transactions if trx.victim), None)


def severity(occurrences: int) -> str:
    return "high" if occurrences >= _HIGH_RECURRENCE else "medium"


def where(table_names: list[str], preposition: str = "en") -> str:
    return f" {preposition} {', '.join(table_names)}" if table_names else ""


def resolution(report: DeadlockReport) -> str:
    """Como lo resolvio el motor, para cerrar la explicacion."""
    trx = victim(report)
    who = f"la transaccion de la sesion {trx.process_id}" if trx else "una de ellas"
    return f"El motor lo resolvio revirtiendo {who}, que recibio un error de abrazo mortal y perdio su trabajo."


def recurrence_text(occurrences: int) -> str:
    return f" Es la vez {occurrences} que aparece este mismo patron." if occurrences > 1 else ""


def retry_text(report: DeadlockReport) -> str:
    error = _DEADLOCK_ERROR.get(report.source)
    return (
        "La aplicacion debe reintentar la transaccion revertida al recibir el "
        f"error de abrazo mortal{f' ({error})' if error else ''}."
    )


def evidence(
    report: DeadlockReport,
    signature: str,
    recurrence: dict,
    captured_at: datetime,
    risk: str,
    limitations: list[str],
) -> dict:
    trx_victim = victim(report)
    detected_at = parse_instant(report.detected_at)
    data = {
        "detected_at": report.detected_at,
        "source": report.source,
        "database_name": report.database_name,
        "transactions": [
            {
                "process_id": trx.process_id,
                "transaction_id": trx.transaction_id,
                "victim": trx.victim,
                "query_id": trx.query_id,
                "canonic_query": trx.canonic_query,
                "waits_for": trx.waits_for.model_dump() if trx.waits_for else None,
                # None: el motor no lo informa
                "holds": [lock.model_dump() for lock in trx.holds] if trx.holds is not None else None,
                "blocked_by": trx.blocked_by,
                "rows_locked": trx.rows_locked,
                "undo_log_entries": trx.undo_log_entries,
            }
            for trx in report.transactions
        ],
        "cycle_size": len(report.transactions),
        "victim_process_id": trx_victim.process_id if trx_victim else None,
        "tables": tables(report),
        "signature": signature,
        "signature_parts": [list(part) for part in signature_parts(report)],
        "occurrences": recurrence["count"],
        "first_seen_at": recurrence["first_seen_at"],
        "risk": risk,
        # latencia de deteccion (metrica M4: <= 15 s)
        "detection_delay_s": round((captured_at - detected_at).total_seconds(), 1) if detected_at else None,
        "captured_at": captured_at.isoformat(),
    }
    if any(trx.waits_for and trx.waits_for.targets_source == "ctid" for trx in report.transactions):
        limitations = [*limitations, _TARGETS_FROM_CTID]
    if limitations:
        data["limitations"] = limitations
    return data
