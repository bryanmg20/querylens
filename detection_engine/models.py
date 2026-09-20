from dataclasses import dataclass, field
from typing import Any, Mapping


QueryId = int | str


def _records(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        raise TypeError(f"Snapshot field '{key}' must be a list or null")
    if not all(isinstance(item, Mapping) for item in value):
        raise TypeError(f"Snapshot field '{key}' must contain objects")
    return value


@dataclass
class Statement:
    query_id: QueryId | None = None
    query_text: str | None = None
    execution_count: int | None = None
    rows_returned: int | None = None
    avg_rows_per_call: float | None = None
    total_time_ms: float | None = None
    mean_time_ms: float | None = None
    stddev_time_ms: float | None = None
    min_time_ms: float | None = None
    max_time_ms: float | None = None
    coeff_of_variation: float | None = None
    disk_spill_indicator: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Statement":
        return cls(**{field_name: data.get(field_name) for field_name in cls.__dataclass_fields__})


@dataclass
class TableStat:
    schema_name: str | None = None
    table_name: str | None = None
    seq_scans: int | None = None
    idx_scans: int | None = None
    live_rows: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TableStat":
        return cls(**{field_name: data.get(field_name) for field_name in cls.__dataclass_fields__})


@dataclass
class IndexStat:
    schema_name: str | None = None
    table_name: str | None = None
    index_name: str | None = None
    index_scans: int | None = None
    last_index_scan: str | None = None
    index_ref: str | None = None
    index_size_bytes: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IndexStat":
        return cls(**{field_name: data.get(field_name) for field_name in cls.__dataclass_fields__})


@dataclass
class ColumnStat:
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    data_type: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ColumnStat":
        return cls(**{field_name: data.get(field_name) for field_name in cls.__dataclass_fields__})


@dataclass
class ActiveQuery:
    process_id: int | str | None = None
    query_text: str | None = None
    query_id: QueryId | None = None
    transaction_start_time: str | None = None
    blocking_pids: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActiveQuery":
        blocking_pids = data.get("blocking_pids") or []
        if isinstance(blocking_pids, str):
            blocking_pids = [int(pid) for pid in blocking_pids.split(",") if pid.strip().isdigit()]
        if not isinstance(blocking_pids, list):
            raise TypeError("ActiveQuery field 'blocking_pids' must be a list, string, or null")
        return cls(
            process_id=data.get("process_id"),
            query_text=data.get("query_text"),
            query_id=data.get("query_id"),
            transaction_start_time=data.get("transaction_start_time"),
            blocking_pids=blocking_pids,
        )


@dataclass
class Lock:
    lock_mode: str | None = None
    is_granted: bool | str | None = None
    process_id: int | str | None = None
    table_name: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Lock":
        return cls(**{field_name: data.get(field_name) for field_name in cls.__dataclass_fields__})


@dataclass
class CandidateStatement:
    """Forma comun de `top_impact_queries` y `non_explainable_candidates`.

    Ambas listas salen de stages/candidates.py + stages/normalize.py: son
    statements seleccionados por impacto/inestabilidad/disk-spill, con
    `selected_by` agregado y, si el texto real se capturo en active_queries,
    `canonic_query`/`real_query_found` resueltos. Los no explicables (ej.
    COMMIT) llegan sin esos dos ultimos campos.
    """

    query_id: QueryId | None = None
    query_text: str | None = None
    canonic_query: str | None = None
    schema_name: str | None = None
    execution_count: int | None = None
    rows_returned: int | None = None
    avg_rows_per_call: float | None = None
    total_time_ms: float | None = None
    mean_time_ms: float | None = None
    stddev_time_ms: float | None = None
    min_time_ms: float | None = None
    max_time_ms: float | None = None
    coeff_of_variation: float | None = None
    disk_spill_indicator: int | None = None
    real_query_found: bool | None = None
    selected_by: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateStatement":
        scalar_fields = [name for name in cls.__dataclass_fields__ if name != "selected_by"]
        return cls(
            **{field_name: data.get(field_name) for field_name in scalar_fields},
            selected_by=list(data.get("selected_by") or []),
        )


@dataclass
class Snapshot:
    db_id: str | None = None
    source: str | None = None
    statements: list[Statement] = field(default_factory=list)
    tables: list[TableStat] = field(default_factory=list)
    indexes: list[IndexStat] = field(default_factory=list)
    columns: list[ColumnStat] = field(default_factory=list)
    active_queries: list[ActiveQuery] = field(default_factory=list)
    locks: list[Lock] = field(default_factory=list)
    canonic_explains: list[Mapping[str, Any]] = field(default_factory=list)
    top_impact_queries: list[CandidateStatement] = field(default_factory=list)
    non_explainable_candidates: list[CandidateStatement] = field(default_factory=list)
    stats_reset_timestamp: list[Mapping[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Snapshot":
        return cls(
            db_id=payload.get("db_id"),
            source=payload.get("source"),
            statements=[Statement.from_dict(item) for item in _records(payload, "statements")],
            tables=[TableStat.from_dict(item) for item in _records(payload, "tables")],
            indexes=[IndexStat.from_dict(item) for item in _records(payload, "indexes")],
            columns=[ColumnStat.from_dict(item) for item in _records(payload, "columns")],
            active_queries=[ActiveQuery.from_dict(item) for item in _records(payload, "active_queries")],
            locks=[Lock.from_dict(item) for item in _records(payload, "locks")],
            canonic_explains=_records(payload, "canonic_explains"),
            top_impact_queries=[
                CandidateStatement.from_dict(item) for item in _records(payload, "top_impact_queries")
            ],
            non_explainable_candidates=[
                CandidateStatement.from_dict(item) for item in _records(payload, "non_explainable_candidates")
            ],
            stats_reset_timestamp=_records(payload, "stats_reset_timestamp"),
        )


@dataclass
class Hallazgo:
    antipatron: str
    severidad: str
    evidencia: dict[str, Any]
    explicacion: str
    recomendacion: str
    query_id: QueryId | None = None
    table_name: str | None = None
