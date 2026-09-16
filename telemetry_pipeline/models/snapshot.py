from datetime import datetime
from typing import Annotated, Literal, TypeVar, Union

from pydantic import BeforeValidator, BaseModel, ConfigDict, Field


def _list_or_empty(value):
    return [] if value is None else value


def _to_iso(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    return value


def _to_bool(value):
    if isinstance(value, str):
        if value.upper() == "GRANTED":
            return True
        if value.upper() == "WAITING":
            return False
    return value


def _to_int_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [int(pid) for pid in value.split(",") if pid.strip().isdigit()]
    return value


T = TypeVar("T")

ListOrNone = Annotated[list[T], BeforeValidator(_list_or_empty)]

QueryId = Union[str, int, None]


class StatementRow(BaseModel):
    query_id: QueryId
    query_text: str
    execution_count: int
    rows_returned: int
    avg_rows_per_call: float | None = None
    total_time_ms: float | None = None
    mean_time_ms: float | None = None
    stddev_time_ms: float | None = None
    min_time_ms: float | None = None
    max_time_ms: float | None = None
    coeff_of_variation: float | None = None
    disk_spill_indicator: int | None = None


class StatementCandidate(StatementRow):
    canonic_query: str | None = None
    selected_by: list[str] = Field(default_factory=list)
    real_query_found: bool = False


class LockRow(BaseModel):
    process_id: int
    table_name: str | None = None
    lock_mode: str
    is_granted: Annotated[bool, BeforeValidator(_to_bool)]


class ActiveQueryRow(BaseModel):
    process_id: int
    query_text: str | None = None
    query_id: QueryId
    canonic_query: str | None = None
    transaction_start_time: Annotated[str | None, BeforeValidator(_to_iso)] = None
    blocking_pids: Annotated[list[int], BeforeValidator(_to_int_list)] = Field(
        default_factory=list
    )


class IndexRow(BaseModel):
    schema_name: str
    table_name: str
    index_name: str
    index_scans: int | None = None
    last_index_scan: Annotated[str | None, BeforeValidator(_to_iso)] = None
    index_ref: str | None = None
    index_size_bytes: int | None = None


class TableRow(BaseModel):
    schema_name: str
    table_name: str
    seq_scans: int | None = None
    idx_scans: int | None = None
    live_rows: int | None = None


class ColumnRow(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    data_type: str


class StatsResetRow(BaseModel):
    stats_reset: Annotated[str | None, BeforeValidator(_to_iso)] = None


class LogicalShape(BaseModel):
    scans: int = 0
    joins: int = 0
    aggregates: int = 0
    sorts: int = 0
    subqueries: int = 0
    distinct: int = 0


class Estimates(BaseModel):
    total_cost: float | None = None


class PhysicalOperation(BaseModel):
    type: Literal["scan", "join", "aggregate", "sort", "subquery", "distinct"]
    access_method: str | None = None
    relation: str | None = None
    estimated_rows: Union[int, float] | None = None
    predicate: str | None = None
    index_name: str | None = None


class CanonicalPlan(BaseModel):
    logical_shape: LogicalShape = Field(default_factory=LogicalShape)
    physical_operations: list[PhysicalOperation] = Field(default_factory=list)
    estimates: Estimates = Field(default_factory=Estimates)


class CanonicExplain(BaseModel):
    query_id: QueryId
    canonical_plan: CanonicalPlan


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    db_id: str
    statements: ListOrNone[StatementRow]
    top_impact_queries: ListOrNone[StatementCandidate]
    non_explainable_candidates: ListOrNone[StatementCandidate]
    locks: ListOrNone[LockRow]
    active_queries: ListOrNone[ActiveQueryRow]
    indexes: ListOrNone[IndexRow]
    tables: ListOrNone[TableRow]
    columns: ListOrNone[ColumnRow]
    stats_reset_timestamp: ListOrNone[StatsResetRow]
    canonic_explains: ListOrNone[CanonicExplain]

    @classmethod
    def from_snapshot(cls, stats):
        return cls.model_validate(stats)

    def to_json(self):
        return self.model_dump_json(exclude_none=False)