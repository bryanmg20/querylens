"""Modelos propios del detector. El snapshot que llega por analyze_job se
valida con querylens_contract (el mismo paquete con el que lo arma el pipeline)."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from querylens_contract.snapshot import QueryId


@dataclass
class StatementHistory:
    """Una fila de public.statement_samples: el estado de un query_id que los
    detectores con ventana (AP-01, AP-06, AP-07, AP-08) arrastran de un snapshot al siguiente."""

    query_id: str
    # hora del snapshot que dejo esta fila asi
    captured_at: datetime
    # ultimos contadores acumulados, contra los que se resta el proximo snapshot
    execution_count: int
    total_time_ms: float
    # None si la fila es anterior a que se guardara este contador
    disk_spill_indicator: int | None = None
    sort_merge_passes: int | None = None
    rows_returned: int | None = None
    counters_epoch: datetime | None = None
    # latencia media de las ultimas ventanas validas y su hora, de la mas vieja a la mas nueva
    window_means_ms: list[float] = field(default_factory=list)
    window_ends_at: list[datetime] = field(default_factory=list)


@dataclass
class Hallazgo:
    antipatron: str
    severidad: str
    evidencia: dict[str, Any]
    explicacion: str
    recomendacion: str
    query_id: QueryId | None = None
    table_name: str | None = None
