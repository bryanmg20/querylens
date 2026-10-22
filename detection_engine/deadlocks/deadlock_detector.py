"""Clasifica cada abrazo mortal de snapshot.deadlocks: CT-01, CT-02 o sin
clasificar. Cada informe sale en a lo sumo un patron.

Cada abrazo se informa una vez: el mismo informe llega en varios snapshots
(DeadlocksStage trae una ventana) y la memoria de contencion los descarta.
Los que no son de ningun patron (por ejemplo los de hueco, CT-03) se recuerdan
sin hallazgo.
"""
from datetime import datetime

from querylens_contract import SnapshotPayload

from logger import get_logger
from models import Hallazgo

from . import ct01_inconsistent_order, ct02_mode_conversion, unclassified_deadlock
from .cycles import deadlock_signature
from .state import DeadlockMemory

logger = get_logger(__name__)

# (regla, modulo con ANTIPATRON y finding), en orden: las reglas se excluyen
# entre si, el orden solo fija cual se prueba primero
_PATTERNS = [
    (ct01_inconsistent_order.is_inconsistent_order, ct01_inconsistent_order),
    (ct02_mode_conversion.is_mode_conversion, ct02_mode_conversion),
    (unclassified_deadlock.is_unclassified, unclassified_deadlock),
]


def classify(report) -> str | None:
    """ANTIPATRON del patron del informe, o None si no es de ninguno."""
    for rule, pattern in _PATTERNS:
        if rule(report):
            return pattern.ANTIPATRON
    return None


def detect_deadlocks(
    snapshot: SnapshotPayload,
    captured_at: datetime,
    memory: DeadlockMemory,
) -> tuple[list[Hallazgo], DeadlockMemory]:
    """Hallazgos de los abrazos nuevos de este snapshot y la memoria
    actualizada, que sera la de la siguiente muestra."""
    memory = memory.pruned(captured_at)
    seen = dict(memory.seen)
    signatures = {signature: dict(info) for signature, info in memory.signatures.items()}
    patterns = {pattern.ANTIPATRON: pattern for _, pattern in _PATTERNS}
    findings: list[Hallazgo] = []
    without_detail = 0

    for report in sorted(snapshot.deadlocks, key=lambda r: r.detected_at):
        if report.detected_at in seen:
            continue
        if not report.complete:
            seen[report.detected_at] = ""
            without_detail += 1
            continue
        antipatron = classify(report)
        if antipatron is None:
            seen[report.detected_at] = ""
            logger.info(
                "  contencion | db_id=%s | abrazo de %s sin patron (ni CT-01 ni CT-02)",
                snapshot.db_id, report.detected_at,
            )
            continue
        signature = deadlock_signature(report, antipatron)
        seen[report.detected_at] = signature
        info = signatures.get(signature)
        if info is None:
            info = {"count": 0, "first_seen_at": report.detected_at}
        info["count"] += 1
        info["last_seen_at"] = report.detected_at
        signatures[signature] = info
        findings.append(patterns[antipatron].finding(report, signature, info, captured_at))

    if without_detail:
        logger.warning(
            "  contencion | db_id=%s | %d abrazo(s) mortal(es) sin detalle: el motor "
            "no conserva su informe (MySQL solo guarda el ultimo)",
            snapshot.db_id, without_detail,
        )
    return findings, DeadlockMemory(seen=seen, signatures=signatures)
