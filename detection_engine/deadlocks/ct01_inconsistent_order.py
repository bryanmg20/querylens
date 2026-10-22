"""CT-01: abrazo mortal por orden inconsistente de acceso (PG y MySQL).

Regla (Tabla 3, issue #44): ciclo de >= 2 transacciones en el grafo de espera
en el que cada una mantiene en modo exclusivo el recurso que la otra solicita,
adquiridos en orden opuesto. Firma normalizada: conjunto ordenado de (huella,
tabla, indice, modo).

El ciclo sale del informe que el motor escribe al revertir la victima
(snapshot.deadlocks): el abrazo dura lo que tarda el motor en detectarlo y no
aparece en las muestras de active_queries. Operativamente:

- MySQL (INNODB STATUS dice que mantiene y que espera cada transaccion): cada
  una espera un bloqueo exclusivo de registro o tabla (X, sin GAP ni
  INSERT_INTENTION) sobre un recurso distinto, y quien la bloquea mantiene ese
  mismo recurso en X. Mismo recurso en S que pasa a X es conversion (CT-02);
  bloqueos de hueco son contencion de rango (CT-03).
- Postgres (el log solo dice que espera cada proceso): cada una espera un
  recurso distinto. Una espera de fila en Postgres es sobre el transactionid
  de quien la tiene, y el log no dice en que modo se tomo la fila: una
  conversion FOR SHARE -> UPDATE sobre la misma fila (CT-02) se ve igual y
  aqui sale como CT-01. Queda anotado en la evidencia.

Cada abrazo se informa una vez: el mismo informe llega en varios snapshots
(DeadlocksStage trae una ventana) y la memoria de contencion los descarta.
"""
import hashlib
from datetime import datetime

from querylens_contract import DeadlockLock, DeadlockReport, DeadlockTransaction, SnapshotPayload

from anti_patterns.statement_history import parse_instant
from logger import get_logger
from models import Hallazgo

from .state import DeadlockMemory

logger = get_logger(__name__)

ANTIPATRON = "inconsistent_lock_order_deadlock"

# CT-04 (Tabla 3): un UPDATE/DELETE sin indice utilizable mantiene >= 10 veces
# mas bloqueos de fila que filas modificadas. Ese ciclo tambien es de bloqueos
# X en orden cruzado, pero la causa y la recomendacion son otras: no se
# informa como CT-01.
_LOCK_AMPLIFICATION_RATIO = 10

# Severidad: un abrazo aislado lo resuelve el motor y la aplicacion reintenta;
# el mismo patron repetido es un defecto de diseño que ya cuesta transacciones
# revertidas. 3 es el valor inicial a calibrar.
_HIGH_RECURRENCE = 3

_PG_LIMITATION = (
    "Postgres no informa en que modo cada transaccion tomo la fila que retiene: "
    "si ambas leyeron la misma fila con FOR SHARE antes de modificarla, el "
    "patron real es conversion de modo (CT-02)."
)

_RISK = (
    "Cambiar el orden de acceso toca la logica de la aplicacion: todas las "
    "rutas que modifican estas tablas tienen que seguir el mismo orden, o el "
    "ciclo reaparece entre la ruta nueva y una vieja. Tomar los bloqueos por "
    "adelantado (SELECT ... FOR UPDATE ordenado) alarga el tiempo que se "
    "retienen y puede aumentar la espera de otras sesiones."
)


def detect_inconsistent_order(
    snapshot: SnapshotPayload,
    captured_at: datetime,
    memory: DeadlockMemory,
) -> tuple[list[Hallazgo], DeadlockMemory]:
    """Hallazgos de los abrazos nuevos de este snapshot y la memoria
    actualizada, que sera la de la siguiente muestra."""
    memory = memory.pruned(captured_at)
    seen = dict(memory.seen)
    signatures = {signature: dict(info) for signature, info in memory.signatures.items()}
    findings: list[Hallazgo] = []
    without_detail = 0

    for report in sorted(snapshot.deadlocks, key=lambda r: r.detected_at):
        if report.detected_at in seen:
            continue
        if not report.complete:
            seen[report.detected_at] = ""
            without_detail += 1
            continue
        signature = deadlock_signature(report)
        seen[report.detected_at] = signature
        if not is_inconsistent_order(report):
            logger.info(
                "  contencion | db_id=%s | abrazo de %s no es CT-01 (firma %s)",
                snapshot.db_id, report.detected_at, signature,
            )
            continue
        info = signatures.get(signature)
        if info is None:
            info = {"count": 0, "first_seen_at": report.detected_at}
        info["count"] += 1
        info["last_seen_at"] = report.detected_at
        signatures[signature] = info
        findings.append(_finding(report, signature, info, captured_at))

    if without_detail:
        logger.warning(
            "  contencion | db_id=%s | %d abrazo(s) mortal(es) sin detalle: el motor "
            "no conserva su informe (MySQL solo guarda el ultimo)",
            snapshot.db_id, without_detail,
        )
    return findings, DeadlockMemory(seen=seen, signatures=signatures)


def is_inconsistent_order(report: DeadlockReport) -> bool:
    transactions = report.transactions
    if not report.complete or len(transactions) < 2:
        return False
    if any(trx.waits_for is None or not trx.waits_for.resources for trx in transactions):
        return False
    by_pid = {trx.process_id: trx for trx in transactions}
    # ciclo: cada una espera a otra del informe (con grado de salida >= 1 en un
    # grafo finito hay ciclo)
    if any(not any(pid in by_pid for pid in trx.blocked_by) for trx in transactions):
        return False
    # orden opuesto: cada una espera un recurso distinto. Todas sobre el mismo
    # es una conversion de modo (CT-02).
    waited = [frozenset(trx.waits_for.resources) for trx in transactions]
    if any(a & b for i, a in enumerate(waited) for b in waited[i + 1:]):
        return False
    if not any(trx.holds for trx in transactions):
        # Postgres: no hay mas que mirar
        return True
    if any(_amplified(trx) for trx in transactions):
        return False
    for trx in transactions:
        if not _exclusive(trx.waits_for):
            return False
        holders = [by_pid[pid] for pid in trx.blocked_by if pid in by_pid]
        if not any(_holds_exclusive(holder, trx.waits_for) for holder in holders):
            return False
    return True


def _exclusive(lock: DeadlockLock) -> bool:
    """X de registro (next-key o REC_NOT_GAP) o de tabla. X,GAP no protege el
    registro y X,INSERT_INTENTION es una insercion en un hueco: CT-03."""
    mode, *flags = lock.lock_mode.split(",")
    return mode == "X" and not {"GAP", "INSERT_INTENTION"} & set(flags)


def _holds_exclusive(holder: DeadlockTransaction, wanted: DeadlockLock) -> bool:
    return any(
        _exclusive(lock) and set(lock.resources) & set(wanted.resources)
        for lock in holder.holds
    )


def _amplified(trx: DeadlockTransaction) -> bool:
    if trx.rows_locked is None:
        return False
    return trx.rows_locked >= _LOCK_AMPLIFICATION_RATIO * max(trx.undo_log_entries or 0, 1)


def signature_parts(report: DeadlockReport) -> list[tuple[str, str, str, str]]:
    """(huella, tabla, indice, modo) de lo que espera cada transaccion, ordenado:
    no depende de que transaccion fue la victima ni del orden del informe."""
    parts = []
    for trx in report.transactions:
        lock = trx.waits_for
        parts.append((
            trx.canonic_query or "",
            _qualified(lock.schema_name, lock.table_name) if lock else "",
            (lock.index_name or "") if lock else "",
            lock.lock_mode if lock else "",
        ))
    return sorted(parts)


def deadlock_signature(report: DeadlockReport) -> str:
    raw = "\n".join("\x1f".join(part) for part in signature_parts(report))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _qualified(schema: str | None, table: str | None) -> str:
    if not table:
        return ""
    return f"{schema}.{table}" if schema else table


def _finding(report: DeadlockReport, signature: str, recurrence: dict, captured_at: datetime) -> Hallazgo:
    transactions = report.transactions
    tables = sorted({
        _qualified(trx.waits_for.schema_name, trx.waits_for.table_name)
        for trx in transactions if trx.waits_for and trx.waits_for.table_name
    })
    victim = next((trx for trx in transactions if trx.victim), None)
    engine = "postgres" if report.source == "postgres_log" else "mysql"
    detected_at = parse_instant(report.detected_at)
    evidence = {
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
                "holds": [lock.model_dump() for lock in trx.holds],
                "blocked_by": trx.blocked_by,
                "rows_locked": trx.rows_locked,
                "undo_log_entries": trx.undo_log_entries,
            }
            for trx in transactions
        ],
        "cycle_size": len(transactions),
        "victim_process_id": victim.process_id if victim else None,
        "tables": tables,
        "signature": signature,
        "signature_parts": [list(part) for part in signature_parts(report)],
        "occurrences": recurrence["count"],
        "first_seen_at": recurrence["first_seen_at"],
        "risk": _RISK,
        # latencia de deteccion (metrica M4: <= 15 s)
        "detection_delay_s": round((captured_at - detected_at).total_seconds(), 1) if detected_at else None,
        "captured_at": captured_at.isoformat(),
    }
    if engine == "postgres":
        evidence["limitations"] = [_PG_LIMITATION]

    return Hallazgo(
        antipatron=ANTIPATRON,
        severidad="high" if recurrence["count"] >= _HIGH_RECURRENCE else "medium",
        evidencia=evidence,
        explicacion=_explanation(transactions, tables, victim, recurrence["count"], engine),
        recomendacion=_recommendation(tables),
        query_id=victim.query_id if victim and victim.query_id is not None else None,
        table_name=", ".join(tables) if tables else None,
    )


def _explanation(transactions, tables, victim, occurrences: int, engine: str) -> str:
    where = f" en {', '.join(tables)}" if tables else ""
    # Postgres no informa el modo de lo que retiene cada una
    held = "retenia en modo exclusivo" if engine == "mysql" else "retenia"
    text = (
        f"{len(transactions)} transacciones formaron un abrazo mortal{where}: cada una "
        f"{held} lo que la otra pedia, porque tomaron los mismos "
        "recursos en orden opuesto. El motor lo resolvio revirtiendo "
        f"{'la transaccion de la sesion ' + str(victim.process_id) if victim else 'una de ellas'}, "
        "que recibio un error de abrazo mortal y perdio su trabajo."
    )
    if occurrences > 1:
        text += f" Es la vez {occurrences} que aparece este mismo patron."
    return text


def _recommendation(tables: list[str]) -> str:
    where = f" sobre {', '.join(tables)}" if tables else ""
    return (
        f"Imponer un orden de acceso unico{where}: toda transaccion que modifique "
        "varias filas o tablas debe tomarlas en el mismo orden (por ejemplo, por "
        "clave primaria ascendente), o bloquearlas todas al inicio con "
        "SELECT ... FOR UPDATE ordenado por esa clave. Mantener las "
        "transacciones cortas reduce la ventana del ciclo, y la aplicacion debe "
        "reintentar la transaccion revertida (SQLSTATE 40P01 en Postgres, error "
        "1213 en MySQL)."
    )
