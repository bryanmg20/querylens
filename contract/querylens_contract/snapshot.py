from datetime import datetime, timezone
from typing import Annotated, Literal, TypeVar, Union

from pydantic import BeforeValidator, BaseModel, ConfigDict, Field, field_validator

# Tope de query_text en statements (I-14). Es el mismo largo sobre el que
# canonicalizers deja canonic_query en "Not available": por encima nada analiza
# ese texto, solo viaja. pg_stat_statements guarda el texto completo, sin tope:
# un INSERT masivo de sysbench pesa ~58 KB y 20 de ellos eran ~1 MB del payload.
MAX_STATEMENT_TEXT_CHARS = 8192


def _list_or_empty(value):
    return [] if value is None else value


def _to_naive_utc(dt):
    """Aware datetime -> UTC naíve; naive se deja tal cual.

    Postgres entrega timestamptz en la zona de su sesión; MySQL entrega DATETIME
    naive en hora del servidor. Fijar la sesión MySQL a +00:00 (init_command en
    config/connections) hace ambos UTC; este helper solo normaliza el lado que ya
    trae zona, para que counters_epoch, transaction_start_time, last_index_scan y
    server_start_time viajen con el mismo formato sin offset que el lado MySQL.
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
    # query_sample_text (QUERY_SAMPLE_TEXT de MySQL, con literales reales) es
    # flujo INTERNO: vive en stats solo para mark_explainable y el EXPLAIN
    # FORMAT=JSON <sample> de MySQL. No es campo del contrato: se excluye del
    # payload a proposito (requerimiento: no persistir informacion real de las
    # queries). ConfigDict(extra="ignore") lo descarta al validar.
    database_name: str | None = None
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
    # MySQL: SUM_SORT_MERGE_PASSES. Postgres: None (temp_blks_written, en
    # disk_spill_indicator, ya incluye los ordenamientos).
    sort_merge_passes: int | None = None
    counters_epoch: Annotated[str | None, BeforeValidator(_to_iso)] = None
    canonic_query: str | None = None
    # True cuando SnapshotPayload recorto query_text a MAX_STATEMENT_TEXT_CHARS.
    # Solo pasa en statements: los candidatos viajan con el texto completo.
    query_text_truncated: bool = False


class StatementCandidate(StatementRow):
    selected_by: list[str] = Field(default_factory=list)
    ready_for_explain: bool = False


class LockRow(BaseModel):
    process_id: int
    database_name: str | None = None
    table_name: str | None = None
    lock_mode: str
    is_granted: Annotated[bool, BeforeValidator(_to_bool)]


class ActiveQueryRow(BaseModel):
    process_id: int
    database_name: str | None = None
    query_text: str | None = None
    query_id: QueryId
    canonic_query: str | None = None
    transaction_start_time: Annotated[str | None, BeforeValidator(_to_iso)] = None
    # Vocabulario de pg_stat_activity.state ('active', 'idle in transaction', ...);
    # MySQL se traduce a ese mismo vocabulario en su query.
    session_state: str | None = None
    # Cuando empezo a esperar el lock que no le conceden; None si no espera.
    wait_start_time: Annotated[str | None, BeforeValidator(_to_iso)] = None
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


class ForeignKeyRow(BaseModel):
    """Una columna de una FK: las compuestas llegan como varias filas con el
    mismo constraint_name, ordenadas por position. En MySQL schema_name es la
    base; en Postgres, el schema dentro de la base conectada."""
    schema_name: str
    table_name: str
    column_name: str
    referenced_schema_name: str
    referenced_table_name: str
    referenced_column_name: str
    constraint_name: str
    position: int


class ServerStartRow(BaseModel):
    server_start_time: Annotated[str | None, BeforeValidator(_to_iso)] = None


class CaptureTimestampRow(BaseModel):
    """Hora del servidor al tomar el snapshot. Es la referencia para calcular
    antiguedades (transaction_start_time, wait_start_time) con el mismo reloj."""
    captured_at: Annotated[str | None, BeforeValidator(_to_iso)] = None


class DeadlockLock(BaseModel):
    """Un bloqueo de un informe de abrazo mortal: el que una transaccion
    mantiene o el que espera."""
    schema_name: str | None = None
    table_name: str | None = None
    # MySQL: indice del registro bloqueado. Postgres no lo informa.
    index_name: str | None = None
    # MySQL: record | table. Postgres: el tipo de objeto sobre el que espera
    # (transactionid, tuple, relation, ...).
    lock_type: str | None = None
    # MySQL en el vocabulario de performance_schema.data_locks.LOCK_MODE
    # (X, S, X,REC_NOT_GAP, X,GAP, X,INSERT_INTENTION, IX...). Postgres como
    # lo nombra pg_locks.mode (ShareLock, ExclusiveLock, ...).
    lock_mode: str
    # Identificadores fisicos del recurso, nunca datos de la fila: MySQL
    # "space:page:heap" de cada registro (o "table" en un bloqueo de tabla);
    # Postgres el objeto tal como lo escribe el informe ("transaction 67257",
    # "tuple (0,1) of relation 16384"). Dos bloqueos sobre el mismo recurso
    # comparten un elemento.
    resources: list[str] = Field(default_factory=list)


class DeadlockTransaction(BaseModel):
    # Postgres: pid del backend. MySQL: id del processlist (CONNECTION_ID()).
    process_id: int | None = None
    transaction_id: str | None = None
    # Solo la victima de Postgres trae query_id (es su linea del log).
    query_id: QueryId = None
    # La sentencia que esperaba, sin literales. El texto real nunca viaja.
    canonic_query: str | None = None
    # Solo MySQL: Postgres no informa los bloqueos que mantiene cada proceso.
    holds: list[DeadlockLock] = Field(default_factory=list)
    waits_for: DeadlockLock | None = None
    # process_id de las transacciones del ciclo que retienen lo que espera.
    blocked_by: list[int] = Field(default_factory=list)
    # Solo MySQL: "N row lock(s)" y "undo log entries M" del informe.
    rows_locked: int | None = None
    undo_log_entries: int | None = None
    victim: bool = False


class DeadlockReport(BaseModel):
    """Un abrazo mortal que el motor detecto y resolvio revirtiendo una victima.

    Postgres lo deja en el log del servidor y MySQL en SHOW ENGINE INNODB
    STATUS (solo el ultimo) y performance_schema.error_log (solo el encabezado
    de cada uno). Cada snapshot trae los de una ventana reciente, asi que el
    mismo informe llega en varios snapshots seguidos: el consumidor descarta
    los repetidos por detected_at y su contenido."""
    # Hora del servidor, en UTC, en que el motor detecto el ciclo.
    detected_at: Annotated[str, BeforeValidator(_to_iso)]
    # postgres_log | innodb_status | mysql_error_log
    source: str
    database_name: str | None = None
    # False: se sabe que ocurrio pero no que transacciones lo formaron (MySQL
    # con mas de un abrazo entre dos snapshots, o Postgres con
    # log_error_verbosity=terse). No se puede clasificar.
    complete: bool = True
    transactions: list[DeadlockTransaction] = Field(default_factory=list)


class LogicalShape(BaseModel):
    scans: int = 0
    joins: int = 0
    aggregates: int = 0
    sorts: int = 0
    subqueries: int = 0
    distinct: int = 0


class Estimates(BaseModel):
    total_cost: float | None = None
    output_rows: Union[int, float] | None = None


class PhysicalOperation(BaseModel):
    type: Literal["scan", "join", "aggregate", "sort", "subquery", "distinct"]
    access_method: str | None = None
    relation: str | None = None
    # Tabla real de un scan de Postgres (relation es el alias). En MySQL
    # queda en None: el plan solo trae el alias.
    table_name: str | None = None
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
    server_start_timestamp: ListOrNone[ServerStartRow]
    # Seccion del collect, pero con default: los goldens previos no la traen y
    # sin el default dejarian de validar. Sin el campo en el modelo,
    # extra='ignore' descartaria la clave en silencio.
    foreign_keys: ListOrNone[ForeignKeyRow] = []
    # Mismo motivo que foreign_keys: default [] para que los goldens previos validen.
    capture_timestamp: ListOrNone[CaptureTimestampRow] = []
    # La escribe DeadlocksStage, no el collect: default [] por los goldens previos.
    deadlocks: ListOrNone[DeadlockReport] = []
    canonic_explains: ListOrNone[CanonicExplain] = []

    @field_validator("statements", mode="after")
    @classmethod
    def _cap_statement_text(cls, rows):
        """Recorta query_text de statements al serializar. canonic_query y el
        EXPLAIN ya se armaron con el texto completo, y los candidatos
        (top_impact_queries), que son lo que se analiza, no se tocan."""
        for row in rows:
            if len(row.query_text) > MAX_STATEMENT_TEXT_CHARS:
                row.query_text = row.query_text[:MAX_STATEMENT_TEXT_CHARS]
                row.query_text_truncated = True
        return rows

    @classmethod
    def from_snapshot(cls, stats):
        return cls.model_validate(stats)

    def to_json(self):
        return self.model_dump_json(exclude_none=False)