from datetime import datetime, timezone
from typing import Annotated, Literal, TypeVar, Union

from pydantic import BeforeValidator, BaseModel, ConfigDict, Field


def _list_or_empty(value):
    return [] if value is None else value


def _to_naive_utc(dt):
    """Aware datetime -> UTC naíve; naive se deja tal cual.

    Postgres entrega timestamptz en la zona de su sesión; MySQL entrega DATETIME
    naive en hora del servidor. Fijar la sesión MySQL a +00:00 (init_command en
    config/connections) hace ambos UTC; este helper solo normaliza el lado que ya
    trae zona, para que counters_epoch, transaction_start_time, last_index_scan y
    stats_reset viajen con el mismo formato sin offset que el lado MySQL.
    """
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _to_iso(value):
    if isinstance(value, datetime):
        return _to_naive_utc(value).isoformat(sep=" ", timespec="microseconds")
    return value


def _to_bool(value):
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized == "GRANTED":
            return True
        if normalized == "WAITING":
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
    schema_name: str | None = None
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
    counters_epoch: Annotated[str | None, BeforeValidator(_to_iso)] = None


class StatementCandidate(StatementRow):
    canonic_query: str | None = None
    selected_by: list[str] = Field(default_factory=list)
    ready_for_explain: bool = False


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
    explain_source: str | None = None
    canonical_plan: CanonicalPlan


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    db_id: str
    statements: ListOrNone[StatementRow]
    # Las 3 claves derivadas tienen default [] a proposito: Orchestrator salta
    # esas stages cuando statements vino en None (recoleccion fallida), asi que
    # pueden no existir en la fuente sin que el snapshot sea invalido. Las 8
    # claves que escribe CollectStage siguen obligatorias.
    top_impact_queries: ListOrNone[StatementCandidate] = []
    non_explainable_candidates: ListOrNone[StatementCandidate] = []
    locks: ListOrNone[LockRow]
    active_queries: ListOrNone[ActiveQueryRow]
    indexes: ListOrNone[IndexRow]
    tables: ListOrNone[TableRow]
    columns: ListOrNone[ColumnRow]
    stats_reset_timestamp: ListOrNone[StatsResetRow]
    canonic_explains: ListOrNone[CanonicExplain] = []

    @classmethod
    def from_snapshot(cls, stats):
        return cls.model_validate(stats)

    def to_json(self):
        return self.model_dump_json(exclude_none=False)