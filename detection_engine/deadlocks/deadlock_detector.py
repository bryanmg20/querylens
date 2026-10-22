"""Clasifica cada abrazo mortal de snapshot.deadlocks: CT-01 a CT-04 o sin
clasificar. Cada informe sale en a lo sumo un patron.

Cada abrazo se informa una vez: el mismo informe llega en varios snapshots
(DeadlocksStage trae una ventana) y la memoria de contencion los descarta.
Los que no son de ningun patron (por ejemplo una ampliacion de bloqueos con
un indice que la sentencia podria usar) se recuerdan sin hallazgo.
"""
from datetime import datetime

from querylens_contract import SnapshotPayload

from logger import get_logger
from models import Hallazgo

from . import (
    ct01_inconsistent_order,
    ct02_mode_conversion,
    ct03_range_contention,
    ct04_lock_amplification,
    cycles,
    unclassified_deadlock,
)
from .cycles import deadlock_signature
from .state import DeadlockMemory

logger = get_logger(__name__)

# (regla, modulo con ANTIPATRON y finding), en orden: las reglas se excluyen
# entre si, el orden solo fija cual se prueba primero. Cada regla recibe el
# informe y los indices del snapshot; solo CT-04 los lee.
_PATTERNS = [
    (lambda report, _: ct01_inconsistent_order.is_inconsistent_order(report), ct01_inconsistent_order),
    (lambda report, _: ct02_mode_conversion.is_mode_conversion(report), ct02_mode_conversion),
    (lambda report, _: ct03_range_contention.is_range_contention(report), ct03_range_contention),
    (ct04_lock_amplification.is_lock_amplification, ct04_lock_amplification),
    (lambda report, _: unclassified_deadlock.is_unclassified(report), unclassified_deadlock),
]


def classify(report, indexes=()) -> str | None:
    """ANTIPATRON del patron del informe, o None si no es de ninguno.
    indexes son los de snapshot.indexes."""
    for rule, pattern in _PATTERNS:
        if rule(report, indexes):
            return pattern.ANTIPATRON
    return None


def report_key(report) -> str:
    """Clave del informe en la memoria. detected_at solo no basta: el log de
    Postgres tiene milisegundos y dos abrazos pueden caer en el mismo. La
    victima es la misma en cada snapshot que trae el informe."""
    victim = cycles.victim(report)
    if victim is None or victim.process_id is None:
        return report.detected_at
    return f"{report.detected_at}|{victim.process_id}"


def _already_seen(report, seen: dict[str, str]) -> bool:
    if report.complete:
        return report_key(report) in seen
    # Encabezado de MySQL sin detalle: el mismo abrazo pudo llegar completo
    # en un snapshot anterior (INNODB STATUS todavia lo tenia), con victima
    return report.detected_at in seen or any(key.startswith(f"{report.detected_at}|") for key in seen)


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
        if _already_seen(report, seen):
            continue
        key = report_key(report)
        if not report.complete:
            seen[key] = ""
            without_detail += 1
            continue
        antipatron = classify(report, snapshot.indexes)
        if antipatron is None:
            seen[key] = ""
            logger.info(
                "  contencion | db_id=%s | abrazo de %s sin patron (ni CT-01 a CT-04 ni sin clasificar)",
                snapshot.db_id, report.detected_at,
            )
            continue
        signature = deadlock_signature(report, antipatron)
        seen[key] = signature
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
