"""Contrato del snapshot que el pipeline encola en analyze_job.

Fuente unica: el pipeline valida y serializa con estos modelos y el
detection_engine valida con los mismos al leer la cola.
"""
from .snapshot import (
    ActiveQueryRow,
    CanonicExplain,
    CaptureTimestampRow,
    CanonicalPlan,
    ColumnRow,
    DeadlockLock,
    DeadlockReport,
    DeadlockTransaction,
    Estimates,
    ForeignKeyRow,
    IndexRow,
    LockRow,
    LogicalShape,
    PhysicalOperation,
    ServerStartRow,
    SnapshotPayload,
    StatementCandidate,
    StatementRow,
    TableRow,
)

__all__ = [
    "ActiveQueryRow",
    "CanonicExplain",
    "CaptureTimestampRow",
    "CanonicalPlan",
    "ColumnRow",
    "DeadlockLock",
    "DeadlockReport",
    "DeadlockTransaction",
    "Estimates",
    "ForeignKeyRow",
    "IndexRow",
    "LockRow",
    "LogicalShape",
    "PhysicalOperation",
    "SnapshotPayload",
    "StatementCandidate",
    "StatementRow",
    "ServerStartRow",
    "TableRow",
]