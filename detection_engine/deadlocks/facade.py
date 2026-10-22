"""Punto de entrada del modulo de contencion para process_job.

Carga el estado, corre las reglas de contencion y guarda el estado nuevo. Los
hallazgos se devuelven para que main los guarde junto con los de antipatrones.
"""
from datetime import datetime

from querylens_contract import SnapshotPayload
from sqlalchemy.engine import Engine

from anti_patterns.statement_history import parse_instant
from logger import get_logger
from models import Hallazgo

from .ct01_inconsistent_order import detect_inconsistent_order
from .ct05_long_transaction_cascade import detect_long_transaction_cascade
from .state import ensure_contention_state_table, load_contention_state, save_contention_state

logger = get_logger(__name__)

_ensured: set[int] = set()


def resolve_captured_at(snapshot: SnapshotPayload, fallback: datetime) -> datetime:
    """Hora del servidor al tomar el snapshot; enqueued_at (reloj de la base de
    QueryLens) solo para snapshots anteriores a capture_timestamp."""
    if snapshot.capture_timestamp:
        captured_at = parse_instant(snapshot.capture_timestamp[0].captured_at)
        if captured_at is not None:
            return captured_at
    return parse_instant(fallback)


def run_contention(engine: Engine, snapshot: SnapshotPayload, fallback_captured_at: datetime) -> list[Hallazgo]:
    # Una falla aca no tiene que tumbar el job: los hallazgos de antipatrones
    # se guardan igual y la proxima muestra vuelve a evaluar la contencion
    try:
        return _run(engine, snapshot, fallback_captured_at)
    except Exception:
        logger.exception("  contencion | db_id=%s | error, se omite en este snapshot", snapshot.db_id)
        return []


def _run(engine: Engine, snapshot: SnapshotPayload, fallback_captured_at: datetime) -> list[Hallazgo]:
    if not snapshot.db_id:
        return []
    if id(engine) not in _ensured:
        ensure_contention_state_table(engine)
        _ensured.add(id(engine))

    captured_at = resolve_captured_at(snapshot, fallback_captured_at)
    state = load_contention_state(engine, snapshot.db_id)
    previous = state.previous_for(captured_at)
    if previous is None:
        logger.warning(
            "  contencion | db_id=%s | snapshot de %s mas viejo que el ultimo procesado (%s), se omite",
            snapshot.db_id, captured_at, state.captured_at,
        )
        return []

    findings, roots = detect_long_transaction_cascade(snapshot, captured_at, previous)
    deadlock_findings, deadlocks = detect_inconsistent_order(snapshot, captured_at, state.deadlocks_for(captured_at))
    save_contention_state(engine, snapshot.db_id, state.advance(captured_at, roots, deadlocks))
    if roots:
        logger.info(
            "  contencion | db_id=%s | raices en cascada=%d | hallazgos=%d",
            snapshot.db_id, len(roots), len(findings),
        )
    if deadlock_findings:
        logger.info(
            "  contencion | db_id=%s | abrazos mortales CT-01=%d",
            snapshot.db_id, len(deadlock_findings),
        )
    return findings + deadlock_findings
