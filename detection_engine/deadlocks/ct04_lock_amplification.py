"""CT-04: ampliacion de bloqueos por ausencia de indice (MySQL).

Regla (Tabla 3, issue #48): ciclo donde un UPDATE/DELETE sin indice
utilizable mantiene >= 10 veces mas bloqueos de fila que filas modificadas.
Sin indice, InnoDB recorre el indice primario completo y en REPEATABLE READ
bloquea (next-key) cada fila que lee, no solo las que modifica: cualquier otra
transaccion que toque la tabla puede cerrar un ciclo con ella.

Con el vocabulario comun del contrato: una transaccion del ciclo cumple la
proporcion (rows_locked / undo_log_entries) y su sentencia es un UPDATE o
DELETE de una sola tabla cuyo WHERE compara columnas contra valores, ninguna
de las cuales es la primera columna de un indice de esa tabla
(snapshot.indexes). El plan de la sentencia no esta en el informe: el
"access_type = ALL" se infiere de los metadatos, con la misma lectura de
columnas e indices que AP-04 (missing_index).

Si la tabla tiene un indice que empieza por una de esas columnas, la
ampliacion tiene otra causa (bloqueos tomados antes en la transaccion, un
indice poco selectivo) y no es CT-04. rows_locked y undo_log_entries son de
toda la transaccion, no solo de la sentencia que esperaba.

Postgres no informa cuantas filas bloquea cada proceso (rows_locked None), y
un UPDATE sin indice solo bloquea las filas que modifica: no aplica.
"""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from querylens_contract import DeadlockReport, DeadlockTransaction, IndexRow

# Misma lectura de columnas e indices que AP-04: el hallazgo recomienda el
# mismo indice que daria AP-04 si la sentencia fuera un SELECT
from anti_patterns.missing_index import (
    _filtered_columns,
    _index_lead_column,
    _lookup_by_table,
    _suggested_index,
)
from logger import get_logger
from models import Hallazgo

from . import cycles

logger = get_logger(__name__)

ANTIPATRON = "lock_amplification_deadlock"

_RISK = (
    "Crear el indice en una tabla grande lleva tiempo y espacio, y cada "
    "INSERT o UPDATE de esas columnas tiene que mantenerlo. En MySQL 8 CREATE "
    "INDEX no bloquea las escrituras (ALGORITHM=INPLACE, LOCK=NONE), pero "
    "conviene hacerlo fuera de las horas de mas carga."
)


@dataclass(frozen=True)
class _Statement:
    kind: str  # "UPDATE" o "DELETE"
    schema_name: str | None
    table_name: str
    equality: list[str]
    range_: list[str]


def is_lock_amplification(report: DeadlockReport, indexes: list[IndexRow]) -> bool:
    if not cycles.is_cycle(report):
        return False
    amplifier = _amplifier(report)
    if amplifier is None:
        return False
    _, statement = amplifier
    lead_columns, ambiguous = _lookup_by_table(
        _lead_columns_by_table(indexes), statement.schema_name, statement.table_name,
    )
    if ambiguous:
        return False  # no se sabe de cual tabla son los indices
    # sin filas en snapshot.indexes la tabla no tiene indices (InnoDB usa uno oculto)
    return not any(column in (lead_columns or set()) for column in statement.equality + statement.range_)


def finding(report: DeadlockReport, signature: str, recurrence: dict, captured_at) -> Hallazgo:
    trx, statement = _amplifier(report)
    table = cycles.qualified(statement.schema_name, statement.table_name)
    suggested = _suggested_index(statement.table_name, statement.equality, statement.range_)
    evidence = cycles.evidence(report, signature, recurrence, captured_at, _RISK, [])
    evidence["amplification"] = {
        "process_id": trx.process_id,
        "canonic_query": trx.canonic_query,
        "table": table,
        "rows_locked": trx.rows_locked,
        "undo_log_entries": trx.undo_log_entries,
        "ratio": round(cycles.lock_ratio(trx), 1),
        "equality_columns": statement.equality,
        "range_columns": statement.range_,
        "suggested_index": suggested,
    }
    evidence["related_antipattern"] = "AP-04"
    return Hallazgo(
        antipatron=ANTIPATRON,
        severidad=cycles.severity(recurrence["count"]),
        evidencia=evidence,
        explicacion=_explanation(report, trx, statement, table, recurrence["count"]),
        recomendacion=_recommendation(report, suggested),
        query_id=trx.query_id,
        table_name=table,
    )


def _amplifier(report: DeadlockReport) -> tuple[DeadlockTransaction, _Statement] | None:
    """La transaccion con mas filas bloqueadas por fila modificada entre las
    que cumplen la proporcion con un UPDATE/DELETE que filtra por columnas."""
    candidates = []
    for trx in report.transactions:
        if not cycles.is_amplified(trx):
            continue
        statement = _statement(trx)
        if statement is not None:
            candidates.append((trx, statement))
    return max(candidates, key=lambda pair: cycles.lock_ratio(pair[0]), default=None)


def _statement(trx: DeadlockTransaction) -> _Statement | None:
    """UPDATE/DELETE de una sola tabla con las columnas de su WHERE que un
    indice podria usar, o None. canonic_query siempre viene en dialecto postgres."""
    if not trx.canonic_query:
        return None
    try:
        tree = sqlglot.parse_one(trx.canonic_query, read="postgres")
    except Exception as e:
        logger.warning(
            "ct04 | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, trx.canonic_query,
        )
        return None
    if not isinstance(tree, (exp.Update, exp.Delete)) or not isinstance(tree.this, exp.Table):
        return None
    # varias tablas (UPDATE ... JOIN, DELETE ... USING) o subconsultas: el
    # indice que falta no se deduce de una sola tabla
    if tree.find(exp.Join) or tree.args.get("using") or tree.find(exp.Subquery, exp.Select):
        return None
    where = tree.args.get("where")
    if where is None or not tree.this.name:
        return None
    table = tree.this
    names = {table.name, table.alias} - {""}

    def _belongs_to_table(column: exp.Column) -> bool:
        return not column.table or column.table in names

    equality, range_, _ = _filtered_columns(where.this.sql(dialect="postgres"), _belongs_to_table)
    if not equality and not range_:
        return None
    return _Statement(
        kind="UPDATE" if isinstance(tree, exp.Update) else "DELETE",
        schema_name=table.db or _schema_of(trx, table.name),
        table_name=table.name,
        equality=equality,
        range_=range_,
    )


def _schema_of(trx: DeadlockTransaction, table_name: str) -> str | None:
    """canonic_query no trae el schema: sale de los bloqueos sobre esa tabla."""
    for lock in [trx.waits_for, *(trx.holds or [])]:
        if lock is not None and lock.table_name == table_name and lock.schema_name:
            return lock.schema_name
    return None


def _lead_columns_by_table(indexes: list[IndexRow]) -> dict[tuple[str | None, str], set[str]]:
    lead_columns: dict[tuple[str | None, str], set[str]] = {}
    for index in indexes:
        columns = lead_columns.setdefault((index.schema_name, index.table_name), set())
        lead_column = _index_lead_column(index.index_ref)
        if lead_column:
            columns.add(lead_column)
    return lead_columns


def _explanation(
    report: DeadlockReport, trx: DeadlockTransaction, statement: _Statement, table: str, occurrences: int,
) -> str:
    columns = ", ".join(statement.equality + statement.range_)
    modified = (
        f"para modificar {trx.undo_log_entries}" if trx.undo_log_entries else "sin haber modificado ninguna"
    )
    return (
        f"{len(report.transactions)} transacciones formaron un abrazo mortal{cycles.where(cycles.tables(report))}. "
        f"La sesion {trx.process_id} ejecutaba un {statement.kind} sobre {table} que filtra por "
        f"{columns}, y ningun indice de la tabla empieza por esas columnas: InnoDB recorrio la "
        f"tabla y dejo bloqueadas {trx.rows_locked} filas {modified}. Con tantas filas retenidas, "
        "cualquier otra transaccion que toque la tabla puede cerrar un ciclo con ella, aunque "
        f"modifique filas distintas. {cycles.resolution(report)}{cycles.recurrence_text(occurrences)}"
    )


def _recommendation(report: DeadlockReport, suggested: str) -> str:
    return (
        f"Crear un indice que empiece por las columnas del WHERE: {suggested}. Con el indice, "
        "la sentencia bloquea solo las filas que modifica. Es el indice ausente de AP-04 en "
        f"una sentencia que modifica en lugar de leer. {cycles.retry_text(report)}"
    )
