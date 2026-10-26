"""Informes de abrazo mortal con la forma que deja DeadlocksStage en
snapshot.deadlocks, a partir de los abrazos medidos en vivo contra ql_mysql y
ql_postgres (los fixtures de telemetry_pipeline/tests/golden/deadlocks/).

Los detectores leen el vocabulario comun (mode, scope, targets, holds); los
campos del motor van igual para que la evidencia sea la real.
"""
from datetime import datetime, timezone

from querylens_contract import SnapshotPayload

T0 = datetime(2026, 10, 22, 0, 59, 36, tzinfo=timezone.utc)
DETECTED = "2026-10-22 00:59:26.415843"
PG_DETECTED = "2026-10-22 00:46:01.669000"

UPDATE_PLUS = "UPDATE ql_ct01_cuentas SET saldo = saldo + $1 WHERE id = $2"
UPDATE_MINUS = "UPDATE ql_ct01_cuentas SET saldo = saldo - $1 WHERE id = $2"
INSERT_RESERVA = "INSERT INTO ql_ct03_reservas VALUES ($1, $2)"
UPDATE_PEDIDOS = "UPDATE ql_ct04_pedidos SET estado = $1 WHERE cliente = $2"

# Modo de InnoDB -> vocabulario comun, como lo traduce el parser
# (telemetry_pipeline/stages/deadlock_reports.innodb_canonical)
_INNODB_COMMON = {
    "X,REC_NOT_GAP": ("X", "row"),
    "S,REC_NOT_GAP": ("S", "row"),
    "X": ("X", "row+gap"),
    "S": ("S", "row+gap"),
    "X,GAP": ("X", "gap"),
    "S,GAP": ("S", "gap"),
    "X,GAP,INSERT_INTENTION": ("X", "insert_intention"),
}


def record_lock(heap, mode="X,REC_NOT_GAP", index="PRIMARY", table="ql_ct01_cuentas"):
    common_mode, scope = _INNODB_COMMON[mode]
    return {
        "schema_name": "ql_demo",
        "table_name": table,
        "index_name": index,
        "mode": common_mode,
        "scope": scope,
        "targets": [f"12:4:{heap}"],
        "lock_type": "record",
        "lock_mode": mode,
        "resources": [f"12:4:{heap}"],
    }


def mysql_trx(pid, holds, waits, blocked_by, victim=False, rows_locked=2, undo=1, query=UPDATE_PLUS):
    return {
        "process_id": pid,
        "transaction_id": str(63900 + pid),
        "canonic_query": query,
        "holds": holds,
        "waits_for": waits,
        "blocked_by": blocked_by,
        "rows_locked": rows_locked,
        "undo_log_entries": undo,
        "victim": victim,
    }


def mysql_ct01(detected_at=DETECTED, **overrides):
    """Las filas 1 (heap 2) y 2 (heap 3) en orden opuesto, como el fixture."""
    first = mysql_trx(264, [record_lock(2)], record_lock(3), [265])
    second = mysql_trx(265, [record_lock(3)], record_lock(2), [264], victim=True)
    first.update(overrides.get("first", {}))
    second.update(overrides.get("second", {}))
    return {
        "detected_at": detected_at,
        "source": "innodb_status",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [first, second],
    }


def mysql_ct02(detected_at=DETECTED, **overrides):
    """FOR SHARE sobre la fila 1 y luego UPDATE: mismo registro, S que pasa a X."""
    first = mysql_trx(236, [record_lock(2, "S,REC_NOT_GAP")], record_lock(2), [237], undo=0, query=UPDATE_MINUS)
    second = mysql_trx(
        237, [record_lock(2, "S,REC_NOT_GAP")], record_lock(2), [236], victim=True, undo=0, query=UPDATE_MINUS,
    )
    first.update(overrides.get("first", {}))
    second.update(overrides.get("second", {}))
    return {
        "detected_at": detected_at,
        "source": "innodb_status",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [first, second],
    }


def mysql_ct03(detected_at=DETECTED, heap=3, hold="X,GAP", **overrides):
    """Las dos bloquean con FOR UPDATE una clave inexistente del hueco (10, 20)
    y despues la insertan: hueco y espera sobre la fila 20 (heap 3), como el
    fixture. heap=1, hold="X" es el hueco del final del indice (supremum)."""
    def lock(mode):
        return record_lock(heap, mode, table="ql_ct03_reservas")
    first = mysql_trx(422, [lock(hold)], lock("X,GAP,INSERT_INTENTION"), [423], undo=0, query=INSERT_RESERVA)
    second = mysql_trx(
        423, [lock(hold)], lock("X,GAP,INSERT_INTENTION"), [422], victim=True, undo=0, query=INSERT_RESERVA,
    )
    first.update(overrides.get("first", {}))
    second.update(overrides.get("second", {}))
    return {
        "detected_at": detected_at,
        "source": "innodb_status",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [first, second],
    }


def mysql_ct04(detected_at=DETECTED, **overrides):
    """UPDATE ... WHERE cliente = ? sin indice: la sesion 448 recorrio la tabla
    y bloqueo las filas 1 a 149 (150 bloqueos, 3 modificadas) y espera la 150
    (heap 151), que tiene la 447; la 447 espera la fila 1 (heap 2), como el
    fixture."""
    def lock(heap, mode="X"):
        return record_lock(heap, mode, table="ql_ct04_pedidos")
    scanner = mysql_trx(448, [lock(2)], lock(151), [447], rows_locked=150, undo=3, query=UPDATE_PEDIDOS)
    other = mysql_trx(
        447, [lock(151, "X,REC_NOT_GAP")], lock(2), [448], victim=True, rows_locked=2, undo=1, query=UPDATE_PEDIDOS,
    )
    scanner.update(overrides.get("scanner", {}))
    other.update(overrides.get("other", {}))
    return {
        "detected_at": detected_at,
        "source": "innodb_status",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [scanner, other],
    }


def index_row(columns, name="PRIMARY", table="ql_ct04_pedidos", schema="ql_demo"):
    """Una fila de snapshot.indexes con el index_ref que arma el colector de MySQL."""
    return {
        "schema_name": schema,
        "table_name": table,
        "index_name": name,
        "index_ref": f"CREATE INDEX {name} ON {schema}.{table} USING btree ({columns})",
    }


def pg_report(rows=(None, None), detected_at=PG_DETECTED, targets_source="row_key"):
    """Como lo deja el stage: la fila se pide en X (es un UPDATE) y no se sabe
    que mantiene cada proceso (holds None). rows son las etiquetas de fila
    que salen de los literales ("row 1") o del ctid de cada espera
    (targets_source="ctid"), o None si no se sabe."""
    def waits(xid, row):
        return {
            "mode": "X",
            "scope": "row",
            "targets": [row] if row else None,
            "targets_source": targets_source if row else None,
            "lock_mode": "ShareLock",
            "lock_type": "transactionid",
            "resources": [f"transaction {xid}"],
            "table_name": "ql_ct01_cuentas",
        }
    first, second = rows
    return {
        "detected_at": detected_at,
        "source": "postgres_log",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [
            {"process_id": 790, "transaction_id": "67256", "query_id": 1693770756014189287,
             "canonic_query": UPDATE_PLUS, "waits_for": waits(67257, first), "blocked_by": [797], "victim": True},
            {"process_id": 797, "canonic_query": UPDATE_PLUS,
             "waits_for": waits(67256, second), "blocked_by": [790]},
        ],
    }


def pg_ct01(**kwargs):
    """id=2 e id=1: filas distintas."""
    return pg_report(rows=("row 1", "row 2"), **kwargs)


def pg_ct02(**kwargs):
    """id=1 las dos: la misma fila."""
    return pg_report(rows=("row 1", "row 1"), **kwargs)


def scrambled(raw, source="otro_motor"):
    """El mismo informe con los campos del motor reemplazados: las reglas no
    los leen."""
    raw = {**raw, "source": source}
    for trx in raw["transactions"]:
        for lock in [trx["waits_for"], *(trx.get("holds") or [])]:
            lock.update(lock_type="?", lock_mode="?", resources=["?"])
    return raw


def snapshot(*reports, indexes=()):
    return SnapshotPayload.model_validate({
        "db_id": "db-1",
        "statements": [],
        "locks": [],
        "active_queries": [],
        "indexes": list(indexes),
        "tables": [],
        "columns": [],
        "server_start_timestamp": [],
        "deadlocks": list(reports),
    })


def report(raw):
    return snapshot(raw).deadlocks[0]
