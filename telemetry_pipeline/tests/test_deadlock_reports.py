"""Parsers de informes de abrazo mortal (stages/deadlock_reports.py).

Los fixtures de golden/deadlocks/ se capturaron contra ql_mysql (8.0.46) y
ql_postgres (17) con la inyeccion de la Tabla 3: dos sesiones actualizan las
filas 1 y 2 en orden opuesto (CT-01), y dos sesiones leen la fila 1 con FOR
SHARE y luego la actualizan (CT-02). postgres_still_waiting.jsonl es lo mismo
desde psycopg 3 (parametros aparte: $1 en el log) con log_lock_waits y 1,5 s
entre las dos esperas, mas un CT-01 entre ql_qa.ql_cmp_cuentas y
ql_qb.ql_cmp_cuentas; la hora esta corrida a la de los otros fixtures. Los de CT-03 y CT-04 son solo de MySQL:
dos sesiones bloquean con FOR UPDATE una clave inexistente del mismo hueco y
despues la insertan (entre dos filas y al final del indice), y un UPDATE sin
indice que recorre la tabla choca con otro que tiene una de sus filas.
"""
import json
from pathlib import Path

import pytest
from querylens_contract import DeadlockReport

from stages.deadlock_reports import (
    MAX_RESOURCES_PER_LOCK,
    innodb_canonical,
    innodb_lock_mode,
    parse_innodb_status,
    parse_postgres_deadlock,
    pg_row_key,
    pg_still_waiting_xid,
    statement_lock_mode,
    statement_table,
)

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "golden" / "deadlocks"


def _innodb(name):
    return (GOLDEN / name).read_text(encoding="utf-8")


def _pg_records():
    return [json.loads(line) for line in (GOLDEN / "postgres_40p01.jsonl").read_text().splitlines()]


def _pg_waiting_records():
    """Los 40P01 de postgres_still_waiting.jsonl y sus avisos por (pid, xid)."""
    records = [json.loads(line) for line in (GOLDEN / "postgres_still_waiting.jsonl").read_text().splitlines()]
    deadlocks = [record for record in records if record.get("state_code") == "40P01"]
    still = {(record["pid"], pg_still_waiting_xid(record)): record for record in records if pg_still_waiting_xid(record)}
    return deadlocks, still


def _no_raw_data(report):
    """Ni literales de las sentencias ni el volcado hex de los registros."""
    dumped = json.dumps(report)
    for raw in ("saldo=saldo", "id=1", "id=2", "80000001", "hex", "app_user"):
        assert raw not in dumped, raw


# ------------------------------------------------------------------ mysql

def test_innodb_ct01_both_transactions_hold_x_and_wait_on_the_other_record():
    report = parse_innodb_status(_innodb("innodb_status_ct01.txt"))

    DeadlockReport.model_validate(report)
    assert report["detected_at"] == "2026-10-22 00:59:26"
    assert report["source"] == "innodb_status"
    assert report["complete"] is True
    assert report["database_name"] == "ql_demo"
    first, second = report["transactions"]
    assert (first["process_id"], first["transaction_id"]) == (264, "63989")
    assert (second["process_id"], second["transaction_id"]) == (265, "63990")
    assert [first["victim"], second["victim"]] == [False, True]
    assert first["canonic_query"] == "UPDATE ql_ct01_cuentas SET saldo = saldo + $1 WHERE id = $2"
    assert (first["rows_locked"], first["undo_log_entries"]) == (2, 1)

    assert first["holds"] == [{
        "schema_name": "ql_demo",
        "table_name": "ql_ct01_cuentas",
        "index_name": "PRIMARY",
        "lock_type": "record",
        "lock_mode": "X,REC_NOT_GAP",
        "resources": ["12:4:2"],
        "mode": "X",
        "scope": "row",
        "targets": ["12:4:2"],
    }]
    assert first["waits_for"]["resources"] == ["12:4:3"]
    assert first["waits_for"]["lock_mode"] == "X,REC_NOT_GAP"
    assert (first["waits_for"]["mode"], first["waits_for"]["scope"]) == ("X", "row")
    assert second["holds"][0]["resources"] == ["12:4:3"]
    assert second["waits_for"]["resources"] == ["12:4:2"]
    # cada una espera el registro que la otra mantiene
    assert first["blocked_by"] == [265]
    assert second["blocked_by"] == [264]
    _no_raw_data(report)


def test_innodb_ct02_holds_shared_and_waits_exclusive_on_the_same_record():
    report = parse_innodb_status(_innodb("innodb_status_ct02.txt"))

    first, second = report["transactions"]
    assert first["holds"][0]["lock_mode"] == "S,REC_NOT_GAP"
    assert first["waits_for"]["lock_mode"] == "X,REC_NOT_GAP"
    assert (first["holds"][0]["mode"], first["waits_for"]["mode"]) == ("S", "X")
    assert first["holds"][0]["targets"] == first["waits_for"]["targets"] == second["waits_for"]["targets"]
    assert first["undo_log_entries"] == 0
    _no_raw_data(report)


def test_innodb_ct03_holds_the_gap_and_waits_to_insert_into_it():
    report = parse_innodb_status(_innodb("innodb_status_ct03.txt"))
    DeadlockReport.model_validate(report)

    assert report["complete"] is True
    first, second = report["transactions"]
    for trx in (first, second):
        assert (trx["holds"][0]["mode"], trx["holds"][0]["scope"]) == ("X", "gap")
        assert (trx["waits_for"]["mode"], trx["waits_for"]["scope"]) == ("X", "insert_intention")
        # el hueco se identifica por el registro que lo cierra (la fila 20)
        assert trx["holds"][0]["targets"] == trx["waits_for"]["targets"] == ["14:4:3"]
        assert trx["canonic_query"] == "INSERT INTO ql_ct03_reservas VALUES ($1, $2)"
    assert (first["blocked_by"], second["blocked_by"]) == ([423], [422])
    _no_raw_data(report)
    assert "(15, 3)" not in json.dumps(report)


def test_innodb_ct03_gap_at_the_end_of_the_index_is_next_key_on_supremum():
    # despues de la ultima fila InnoDB bloquea el supremum (heap 1) sin "gap
    # before rec": queda como next-key, no como gap
    report = parse_innodb_status(_innodb("innodb_status_ct03_supremum.txt"))
    DeadlockReport.model_validate(report)

    for trx in report["transactions"]:
        assert trx["holds"][0]["scope"] == "row+gap"
        assert trx["waits_for"]["scope"] == "insert_intention"
        assert trx["holds"][0]["targets"] == trx["waits_for"]["targets"] == ["15:4:1"]


def test_innodb_ct04_update_without_index_locks_the_scanned_rows():
    report = parse_innodb_status(_innodb("innodb_status_ct04.txt"))
    DeadlockReport.model_validate(report)

    scanner, other = report["transactions"]
    assert scanner["canonic_query"] == "UPDATE ql_ct04_pedidos SET estado = $1 WHERE cliente = $2"
    assert (scanner["rows_locked"], scanner["undo_log_entries"]) == (150, 3)
    assert len(scanner["holds"][0]["resources"]) == MAX_RESOURCES_PER_LOCK
    # el cruce sale directo: lo que espera la otra (la fila 1) esta dentro del tope
    assert other["waits_for"]["targets"] == ["16:4:2"]
    assert (scanner["blocked_by"], other["blocked_by"]) == ([447], [448])
    assert other["victim"] is True
    _no_raw_data(report)


def test_innodb_without_deadlocks_since_startup():
    status = "=====\nINNODB MONITOR OUTPUT\n=====\n------------\nTRANSACTIONS\n------------\n"
    assert parse_innodb_status(status) is None
    assert parse_innodb_status("") is None


def test_innodb_too_deep_search_is_incomplete():
    status = (
        "------------------------\nLATEST DETECTED DEADLOCK\n------------------------\n"
        "2026-10-22 01:00:00 140619105269312\n"
        "TOO DEEP OR LONG SEARCH IN THE LOCK TABLE WAITS-FOR GRAPH, WE WILL ROLL BACK FOLLOWING TRANSACTION\n"
        "*** (1) TRANSACTION:\nTRANSACTION 1, ACTIVE 2 sec\n"
        "------------\nTRANSACTIONS\n------------\n"
    )
    report = parse_innodb_status(status)
    assert report["complete"] is False
    assert report["transactions"] == []


def test_innodb_caps_resources_but_keeps_rows_locked():
    records = "".join(
        f"Record lock, heap no {heap} PHYSICAL RECORD: n_fields 4; compact format; info bits 0\n"
        f" 0: len 4; hex 8000{heap:04x}; asc     ;;\n"
        for heap in range(2, 202)
    )
    status = (
        "------------------------\nLATEST DETECTED DEADLOCK\n------------------------\n"
        "2026-10-22 01:00:00 1\n"
        "*** (1) TRANSACTION:\nTRANSACTION 10, ACTIVE 2 sec fetching rows\n"
        "LOCK WAIT 5 lock struct(s), heap size 1128, 200 row lock(s), undo log entries 1\n"
        "MySQL thread id 7, OS thread handle 1, query id 1 localhost u updating\n"
        "UPDATE t SET a = 1 WHERE c = 5\n"
        "*** (1) HOLDS THE LOCK(S):\n"
        "RECORD LOCKS space id 9 page no 4 n bits 300 index PRIMARY of table `db`.`t` trx id 10 lock_mode X\n"
        + records +
        "*** (1) WAITING FOR THIS LOCK TO BE GRANTED:\n"
        "RECORD LOCKS space id 9 page no 5 n bits 300 index PRIMARY of table `db`.`t` trx id 10 lock_mode X waiting\n"
        "Record lock, heap no 7 PHYSICAL RECORD: n_fields 4; compact format; info bits 0\n"
        "*** (2) TRANSACTION:\nTRANSACTION 11, ACTIVE 1 sec starting index read\n"
        "LOCK WAIT 3 lock struct(s), heap size 1128, 2 row lock(s), undo log entries 1\n"
        "MySQL thread id 8, OS thread handle 2, query id 2 localhost u updating\n"
        "UPDATE t SET a = 2 WHERE id = 3\n"
        "*** (2) HOLDS THE LOCK(S):\n"
        "RECORD LOCKS space id 9 page no 5 n bits 300 index PRIMARY of table `db`.`t` trx id 11 lock_mode X locks rec but not gap\n"
        "Record lock, heap no 7 PHYSICAL RECORD: n_fields 4; compact format; info bits 0\n"
        "*** (2) WAITING FOR THIS LOCK TO BE GRANTED:\n"
        "RECORD LOCKS space id 9 page no 4 n bits 300 index PRIMARY of table `db`.`t` trx id 11 lock_mode X locks rec but not gap waiting\n"
        "Record lock, heap no 3 PHYSICAL RECORD: n_fields 4; compact format; info bits 0\n"
        "*** WE ROLL BACK TRANSACTION (2)\n"
        "------------\nTRANSACTIONS\n------------\n"
    )
    report = parse_innodb_status(status)
    big, small = report["transactions"]
    assert len(big["holds"][0]["resources"]) == MAX_RESOURCES_PER_LOCK
    assert big["rows_locked"] == 200
    assert big["holds"][0]["lock_mode"] == "X"
    # 9:4:3 entra en los primeros 64: el cruce se encuentra igual
    assert small["blocked_by"] == [7]
    assert big["blocked_by"] == [8]


def _three_way_status(waited_heap):
    """La 1 recorrio la tabla (heap 2 a 201 de la pagina 4) y espera lo de la
    2; la 2 espera lo de la 3; la 3 espera waited_heap de la pagina 4."""
    records = "".join(
        f"Record lock, heap no {heap} PHYSICAL RECORD: n_fields 4; compact format; info bits 0\n"
        for heap in range(2, 202)
    )

    def trx(number, holds, waits):
        return (
            f"*** ({number}) TRANSACTION:\nTRANSACTION {10 + number}, ACTIVE 2 sec\n"
            f"LOCK WAIT 3 lock struct(s), heap size 1128, 2 row lock(s), undo log entries 1\n"
            f"MySQL thread id {number}, OS thread handle 1, query id 1 localhost u updating\n"
            f"UPDATE t SET a = 1 WHERE id = 1\n"
            f"*** ({number}) HOLDS THE LOCK(S):\n{holds}"
            f"*** ({number}) WAITING FOR THIS LOCK TO BE GRANTED:\n{waits}"
        )

    def record(page, heap, number, suffix=" locks rec but not gap"):
        return (
            f"RECORD LOCKS space id 9 page no {page} n bits 300 index PRIMARY of table `db`.`t` "
            f"trx id {10 + number} lock_mode X{suffix}\n"
            f"Record lock, heap no {heap} PHYSICAL RECORD: n_fields 4; compact format; info bits 0\n"
        )

    scan = (
        "RECORD LOCKS space id 9 page no 4 n bits 300 index PRIMARY of table `db`.`t` trx id 11 lock_mode X\n"
        + records
    )
    return (
        "------------------------\nLATEST DETECTED DEADLOCK\n------------------------\n"
        "2026-10-22 01:00:00 1\n"
        + trx(1, scan, record(5, 2, 1, " locks rec but not gap waiting"))
        + trx(2, record(5, 2, 2), record(6, 2, 2, " locks rec but not gap waiting"))
        + trx(3, record(6, 2, 3), record(4, waited_heap, 3, " locks rec but not gap waiting"))
        + "*** WE ROLL BACK TRANSACTION (3)\n"
        "------------\nTRANSACTIONS\n------------\n"
    )


def test_innodb_cap_keeps_the_waited_record_past_the_limit():
    # heap 150 es el registro 149 de la 1: queda despues del tope, y sin el
    # la 3 no tendria quien la bloquea (con 3 no hay "es la otra")
    report = parse_innodb_status(_three_way_status(waited_heap=150))
    DeadlockReport.model_validate(report)

    scanner, _, third = report["transactions"]
    assert third["blocked_by"] == [1]
    resources = scanner["holds"][0]["resources"]
    # el esperado ocupa un lugar del tope; los demas, en el orden del informe
    assert len(resources) == MAX_RESOURCES_PER_LOCK
    assert resources[:-1] == [f"9:4:{heap}" for heap in range(2, 1 + MAX_RESOURCES_PER_LOCK)]
    assert resources[-1] == "9:4:150"
    assert scanner["holds"][0]["targets"] == resources


@pytest.mark.parametrize("text, expected", [
    ("X locks rec but not gap", "X,REC_NOT_GAP"),
    ("X locks rec but not gap waiting", "X,REC_NOT_GAP"),
    ("S", "S"),
    ("X", "X"),
    ("X waiting", "X"),
    ("X locks gap before rec", "X,GAP"),
    ("X locks gap before rec insert intention waiting", "X,GAP,INSERT_INTENTION"),
    ("X insert intention", "X,INSERT_INTENTION"),
    ("IX", "IX"),
])
def test_innodb_lock_modes_use_data_locks_names(text, expected):
    assert innodb_lock_mode(text) == expected


@pytest.mark.parametrize("lock_type, lock_mode, mode, scope", [
    ("record", "X,REC_NOT_GAP", "X", "row"),
    ("record", "S,REC_NOT_GAP", "S", "row"),
    # next-key: la fila y el hueco anterior
    ("record", "X", "X", "row+gap"),
    ("record", "S", "S", "row+gap"),
    ("record", "X,GAP", "X", "gap"),
    ("record", "X,GAP,INSERT_INTENTION", "X", "insert_intention"),
    ("record", "X,INSERT_INTENTION", "X", "insert_intention"),
    ("table", "IX", "IX", "table"),
    ("table", "X", "X", "table"),
    ("table", "AUTO-INC", None, "table"),
])
def test_innodb_locks_translate_to_the_common_vocabulary(lock_type, lock_mode, mode, scope):
    assert innodb_canonical(lock_type, lock_mode) == {"mode": mode, "scope": scope}


def test_innodb_table_lock():
    status = (
        "------------------------\nLATEST DETECTED DEADLOCK\n------------------------\n"
        "2026-10-22 01:00:00 1\n"
        "*** (1) TRANSACTION:\nTRANSACTION 10, ACTIVE 2 sec\nMySQL thread id 7, OS thread handle 1, query id 1 h u\n"
        "LOCK TABLES a WRITE, b WRITE\n"
        "*** (1) HOLDS THE LOCK(S):\nTABLE LOCK table `db`.`a` trx id 10 lock mode X\n"
        "*** (1) WAITING FOR THIS LOCK TO BE GRANTED:\nTABLE LOCK table `db`.`b` trx id 10 lock mode X waiting\n"
        "*** (2) TRANSACTION:\nTRANSACTION 11, ACTIVE 2 sec\nMySQL thread id 8, OS thread handle 1, query id 1 h u\n"
        "LOCK TABLES b WRITE, a WRITE\n"
        "*** (2) HOLDS THE LOCK(S):\nTABLE LOCK table `db`.`b` trx id 11 lock mode X\n"
        "*** (2) WAITING FOR THIS LOCK TO BE GRANTED:\nTABLE LOCK table `db`.`a` trx id 11 lock mode X waiting\n"
        "*** WE ROLL BACK TRANSACTION (1)\n"
        "------------\nTRANSACTIONS\n------------\n"
    )
    first, second = parse_innodb_status(status)["transactions"]
    assert first["waits_for"] == {
        "schema_name": "db", "table_name": "b", "index_name": None,
        "lock_type": "table", "lock_mode": "X", "resources": ["table db.b"],
        "mode": "X", "scope": "table", "targets": ["table db.b"],
    }
    assert first["blocked_by"] == [8]
    assert second["blocked_by"] == [7]


# --------------------------------------------------------------- postgres

def test_postgres_ct01_from_the_server_log():
    record = _pg_records()[0]
    report = parse_postgres_deadlock(record)

    DeadlockReport.model_validate({**report, "detected_at": "2026-10-22 00:46:01.669"})
    assert report["detected_at"] == "2026-10-22 00:46:01.669 UTC"
    assert report["source"] == "postgres_log"
    assert report["database_name"] == "ql_demo"
    assert report["complete"] is True
    victim, other = report["transactions"]
    assert (victim["process_id"], victim["victim"]) == (790, True)
    assert victim["transaction_id"] == "67256"
    assert victim["query_id"] == record["query_id"]
    assert victim["waits_for"] == {
        "lock_mode": "ShareLock",
        "resources": ["transaction 67257"],
        "lock_type": "transactionid",
        # el UPDATE pide la fila en X; el ShareLock es sobre la transaccion
        "mode": "X",
        "scope": "row",
        # la fila no se sabe: se espera a la transaccion que la tiene
        "targets": None,
        # del CONTEXT, que solo describe a la victima
        "table_name": "ql_ct01_cuentas",
        "row_key": {"schema": None, "table": "ql_ct01_cuentas", "values": {"id": "v1"}, "role": "ql_user"},
        # la fila por su direccion: de su CONTEXT
        "row_ctid": {"schema": None, "table": "ql_ct01_cuentas", "ctid": "0,2", "role": "ql_user"},
    }
    assert victim["blocked_by"] == [797]
    # el log no dice que mantiene cada proceso
    assert victim["holds"] is None
    assert (other["process_id"], other["victim"], other["query_id"]) == (797, False, None)
    assert other["waits_for"]["resources"] == ["transaction 67256"]
    # de su sentencia: el CONTEXT no habla de ella
    assert other["waits_for"]["table_name"] == "ql_ct01_cuentas"
    assert other["canonic_query"] == "UPDATE ql_ct01_cuentas SET saldo = saldo + $1 WHERE id = $2"
    # la fila de cada uno, con tokens en lugar de los valores (id=2 e id=1)
    assert victim["waits_for"]["row_key"]["values"] == {"id": "v1"}
    assert other["waits_for"]["row_key"]["values"] == {"id": "v2"}
    _no_raw_data(report)


def test_postgres_ct02_rows_share_the_token():
    report = parse_postgres_deadlock(_pg_records()[1])
    keys = [trx["waits_for"]["row_key"] for trx in report["transactions"]]
    assert keys[0] == keys[1]
    assert keys[0]["values"] == {"id": "v1"}
    _no_raw_data(report)


def test_postgres_ct02_looks_the_same_as_ct01():
    """El log no dice que mantiene cada proceso: el FOR SHARE previo no aparece."""
    ct01, ct02 = (parse_postgres_deadlock(record) for record in _pg_records())
    shape = lambda report: [(t["waits_for"]["lock_type"], t["waits_for"]["lock_mode"]) for t in report["transactions"]]
    assert shape(ct01) == shape(ct02)


def _ctids(report):
    return [
        (trx["waits_for"]["row_ctid"]["schema"], trx["waits_for"]["row_ctid"]["table"], trx["waits_for"]["row_ctid"]["ctid"])
        if "row_ctid" in trx["waits_for"] else None
        for trx in report["transactions"]
    ]


def test_postgres_rows_of_parameterized_statements_come_from_the_ctids():
    (ct01, ct02, schemas), still = _pg_waiting_records()

    reports = [parse_postgres_deadlock(record, still) for record in (ct01, ct02, schemas)]

    # la victima por su CONTEXT, la otra por su aviso "still waiting"
    assert _ctids(reports[0]) == [(None, "ql_ct01_cuentas", "0,1"), (None, "ql_ct01_cuentas", "0,2")]
    assert _ctids(reports[1]) == [(None, "ql_ct02_cuentas", "0,1"), (None, "ql_ct02_cuentas", "0,1")]
    # mismo nombre y ctid en dos esquemas: el esquema sale de cada sentencia
    assert _ctids(reports[2]) == [("ql_qa", "ql_cmp_cuentas", "0,1"), ("ql_qb", "ql_cmp_cuentas", "0,1")]
    for report in reports:
        # con $1 no hay valores de donde sacar la fila
        assert all("row_key" not in trx["waits_for"] for trx in report["transactions"])
        DeadlockReport.model_validate({**report, "detected_at": "2026-10-22 00:46:02.502"})


def test_postgres_without_the_waiting_notice_only_the_victim_has_a_ctid():
    (ct01, _, _), _ = _pg_waiting_records()
    assert _ctids(parse_postgres_deadlock(ct01)) == [(None, "ql_ct01_cuentas", "0,1"), None]


def test_postgres_waiting_notice_of_another_transaction_is_not_used():
    (ct01, _, _), still = _pg_waiting_records()
    (key,) = [key for key in still if key[0] == 1712]
    moved = {(key[0], "1"): still[key]}
    assert _ctids(parse_postgres_deadlock(ct01, moved))[1] is None


@pytest.mark.parametrize("context, row", [
    ('while updating tuple (0,2) in relation "t"', (None, "t", "0,2")),
    ('while deleting tuple (3,14) in relation "t"', (None, "t", "3,14")),
    ('while locking tuple (0,7) in relation "t"', (None, "t", "0,7")),
    ('while updating tuple (0,2) in relation "Mi ""T"""', (None, 'Mi "T"', "0,2")),
    # otra version de la fila, o la fila con la que choca un indice: no es la que espera
    ('while locking updated version of tuple (0,9) in relation "t"', None),
    ('while checking uniqueness of tuple (0,1) in relation "t"', None),
    ('while inserting index tuple (0,1) in relation "t"', None),
    (None, None),
])
def test_postgres_victim_row_from_its_context(context, row):
    record = {
        "timestamp": "2026-10-22 00:46:05.000 UTC", "pid": 10, "dbname": "ql_demo", "user": "app",
        "state_code": "40P01", "context": context,
        "detail": (
            "Process 10 waits for ShareLock on transaction 2; blocked by process 11.\n"
            "Process 11 waits for ShareLock on transaction 1; blocked by process 10.\n"
            "Process 10: UPDATE t SET a = $1 WHERE id = $2\n"
            "Process 11: UPDATE t SET a = $1 WHERE id = $2"
        ),
    }
    assert _ctids(parse_postgres_deadlock(record))[0] == row


def test_postgres_schema_of_a_partition_is_not_taken_from_its_parent():
    record = {
        "timestamp": "2026-10-22 00:46:05.000 UTC", "pid": 10, "dbname": "ql_demo", "user": "app",
        "state_code": "40P01", "context": 'while updating tuple (0,1) in relation "pedidos_2026"',
        "detail": (
            "Process 10 waits for ShareLock on transaction 2; blocked by process 11.\n"
            "Process 11 waits for ShareLock on transaction 1; blocked by process 10.\n"
            "Process 10: UPDATE ventas.pedidos SET a = $1 WHERE id = $2\n"
            "Process 11: UPDATE ventas.pedidos SET a = $1 WHERE id = $2"
        ),
    }
    assert _ctids(parse_postgres_deadlock(record))[0] == (None, "pedidos_2026", "0,1")


@pytest.mark.parametrize("message, xid", [
    ("process 1712 still waiting for ShareLock on transaction 87242 after 1000.142 ms", "87242"),
    ("process 1713 detected deadlock while waiting for ShareLock on transaction 87241 after 1000.1 ms", None),
    ("process 9 still waiting for ExclusiveLock on tuple (0,1) of relation 16384 of database 5 after 1000.1 ms", None),
    ("checkpoint complete", None),
])
def test_pg_still_waiting_xid(message, xid):
    assert pg_still_waiting_xid({"message": message}) == xid


def test_postgres_tuple_and_relation_waits_keep_the_oid_for_the_stage():
    record = {
        "timestamp": "2026-10-22 00:46:01.669 UTC",
        "pid": 10,
        "dbname": "ql_demo",
        "detail": (
            "Process 10 waits for ExclusiveLock on tuple (0,1) of relation 16384 of database 5; blocked by process 11.\n"
            "Process 11 waits for AccessExclusiveLock on relation 16390 of database 5; blocked by process 10.\n"
            "Process 10: UPDATE a SET x = 1 WHERE id = 1;\n"
            "Process 11: LOCK TABLE b IN ACCESS EXCLUSIVE MODE;"
        ),
    }
    victim, other = parse_postgres_deadlock(record)["transactions"]
    assert victim["waits_for"] == {
        "lock_mode": "ExclusiveLock",
        "resources": ["tuple (0,1) of relation 16384"],
        "lock_type": "tuple",
        "relation_oid": 16384,
        "mode": "X",
        "scope": "row",
        "targets": ["tuple (0,1) of relation 16384"],
    }
    assert other["waits_for"]["lock_type"] == "relation"
    assert other["waits_for"]["relation_oid"] == 16390
    assert other["waits_for"]["resources"] == ["relation 16390"]
    assert (other["waits_for"]["mode"], other["waits_for"]["scope"]) == ("X", "table")
    assert other["waits_for"]["targets"] == ["relation 16390"]


def test_postgres_advisory_lock_keys_become_tokens():
    # pg_advisory_xact_lock(424242) y (777777): las claves son ids de la aplicacion
    record = {
        "timestamp": "2026-10-22 00:46:01.669 UTC", "pid": 10, "dbname": "ql_demo",
        "detail": (
            "Process 10 waits for ExclusiveLock on advisory lock [16384,0,424242,1]; blocked by process 11.\n"
            "Process 11 waits for ExclusiveLock on advisory lock [16384,0,777777,1]; blocked by process 10.\n"
            "Process 10: SELECT pg_advisory_xact_lock(777777);\n"
            "Process 11: SELECT pg_advisory_xact_lock(424242);"
        ),
    }
    report = parse_postgres_deadlock(record)
    victim, other = report["transactions"]
    assert victim["waits_for"]["targets"] == victim["waits_for"]["resources"] == ["advisory lock a1"]
    assert other["waits_for"]["targets"] == ["advisory lock a2"]
    assert (victim["waits_for"]["mode"], victim["waits_for"]["scope"]) == ("X", "other")
    dumped = json.dumps(report)
    assert "424242" not in dumped and "777777" not in dumped


def test_postgres_multiline_statement_is_canonicalized_whole():
    record = {
        "timestamp": "t", "pid": 10, "dbname": "d",
        "detail": (
            "Process 10 waits for ShareLock on transaction 2; blocked by process 11.\n"
            "Process 11 waits for ShareLock on transaction 1; blocked by process 10.\n"
            "Process 10: UPDATE cuentas\n   SET saldo = 'secreto'\n WHERE id = 7;\n"
            "Process 11: DELETE FROM cuentas WHERE id = 8;"
        ),
    }
    victim, other = parse_postgres_deadlock(record)["transactions"]
    assert victim["canonic_query"] == "UPDATE cuentas SET saldo = $1 WHERE id = $2"
    assert other["canonic_query"] == "DELETE FROM cuentas WHERE id = $1"
    assert "secreto" not in json.dumps(victim)


def test_postgres_terse_log_without_detail_is_incomplete():
    report = parse_postgres_deadlock({"timestamp": "t", "pid": 1, "message": "deadlock detected"})
    assert report["complete"] is False
    assert report["transactions"] == []


@pytest.mark.parametrize("canonic, table", [
    ("UPDATE cuentas SET saldo = $1 WHERE id = $2", "cuentas"),
    ("DELETE FROM public.cuentas WHERE id = $1", "cuentas"),
    ("INSERT INTO cuentas (id, saldo) VALUES ($1, $2)", "cuentas"),
    ("SELECT saldo FROM cuentas WHERE id = $1 FOR UPDATE", "cuentas"),
    ("Not available", None),
    (None, None),
])
def test_statement_table(canonic, table):
    assert statement_table(canonic) == table


@pytest.mark.parametrize("canonic, mode", [
    ("UPDATE cuentas SET saldo = $1 WHERE id = $2", "X"),
    ("DELETE FROM cuentas WHERE id = $1", "X"),
    ("INSERT INTO cuentas (id, saldo) VALUES ($1, $2)", "X"),
    ("SELECT saldo FROM cuentas WHERE id = $1 FOR UPDATE", "X"),
    ("SELECT saldo FROM cuentas WHERE id = $1 FOR NO KEY UPDATE", "X"),
    ("SELECT saldo FROM cuentas WHERE id = $1 FOR SHARE", "S"),
    ("SELECT saldo FROM cuentas WHERE id = $1 FOR KEY SHARE NOWAIT", "S"),
    ("SELECT saldo FROM cuentas WHERE id = $1", None),
    ("Not available", None),
    (None, None),
])
def test_statement_lock_mode(canonic, mode):
    assert statement_lock_mode(canonic) == mode


def test_postgres_row_wait_takes_the_mode_of_its_statement():
    record = {
        "timestamp": "t", "pid": 10, "dbname": "d",
        "detail": (
            "Process 10 waits for ShareLock on transaction 2; blocked by process 11.\n"
            "Process 11 waits for ShareLock on transaction 1; blocked by process 10.\n"
            "Process 10: SELECT saldo FROM cuentas WHERE id = 7 FOR SHARE;\n"
            "Process 11: CALL algo();"
        ),
    }
    victim, other = parse_postgres_deadlock(record)["transactions"]
    assert (victim["waits_for"]["mode"], victim["waits_for"]["scope"]) == ("S", "row")
    # no se sabe que pide: queda sin modo, no se adivina
    assert other["waits_for"]["mode"] is None


@pytest.mark.parametrize("pg_mode, mode", [
    ("AccessShareLock", "IS"),
    ("RowShareLock", "IS"),
    ("RowExclusiveLock", "IX"),
    ("ShareLock", "S"),
    ("ShareUpdateExclusiveLock", None),
    ("ShareRowExclusiveLock", None),
    ("ExclusiveLock", "X"),
    ("AccessExclusiveLock", "X"),
])
def test_postgres_relation_modes(pg_mode, mode):
    record = {
        "timestamp": "t", "pid": 10, "dbname": "d",
        "detail": (
            f"Process 10 waits for {pg_mode} on relation 16390 of database 5; blocked by process 11.\n"
            "Process 11 waits for AccessExclusiveLock on relation 16384 of database 5; blocked by process 10."
        ),
    }
    lock = parse_postgres_deadlock(record)["transactions"][0]["waits_for"]
    assert (lock["mode"], lock["scope"]) == (mode, "table")


@pytest.mark.parametrize("statement, key", [
    ("UPDATE t SET a = 1 WHERE id = 1", {"schema": None, "table": "t", "values": {"id": "v1"}}),
    ("UPDATE public.t x SET a = 1 WHERE x.id = 1 AND (k = 'b' AND saldo >= 3)",
     {"schema": "public", "table": "t", "values": {"id": "v1", "k": "v2"}}),
    ('UPDATE "Ventas"."MiT" SET a = 1 WHERE "Id" = -3', {"schema": "Ventas", "table": "MiT", "values": {"Id": "v1"}}),
    ("UPDATE T SET a = 1 WHERE ID = 1", {"schema": None, "table": "t", "values": {"id": "v1"}}),
    ("UPDATE t SET a = 1 WHERE 1 = id", {"schema": None, "table": "t", "values": {"id": "v1"}}),
    ("SELECT * FROM t WHERE id = '1'::int FOR SHARE", {"schema": None, "table": "t", "values": {"id": "v1"}}),
    ("DELETE FROM t WHERE id = 1", {"schema": None, "table": "t", "values": {"id": "v1"}}),
    # sin valores escritos, o sin una fila identificable por el WHERE
    ("UPDATE t SET a = $1 WHERE id = $2", None),
    ("UPDATE t SET a = 1 WHERE id = 1 OR id = 2", None),
    ("UPDATE t SET a = 1 FROM u WHERE t.id = u.id AND t.id = 1", None),
    ("DELETE FROM t USING u WHERE t.id = 1", None),
    ("SELECT * FROM t WHERE id = 1", None),
    ("SELECT * FROM t JOIN u ON t.id = u.id WHERE t.id = 1 FOR UPDATE", None),
    ("UPDATE t SET a = 1", None),
    ("UPDATE a SET x = 1 WHERE id = 1; UPDATE b SET x = 2 WHERE id = 2;", None),
    ("SELECT transferir(1, 2)", None),
])
def test_pg_row_key(statement, key):
    assert pg_row_key(statement, {}) == key


def test_pg_row_key_tokens_are_shared_within_a_report():
    tokens = {}
    first = pg_row_key("UPDATE t SET a = 1 WHERE id = 7", tokens)
    second = pg_row_key("UPDATE t SET a = 2 WHERE id = '7'", tokens)
    third = pg_row_key("UPDATE t SET a = 3 WHERE id = 8", tokens)
    assert first["values"] == second["values"] == {"id": "v1"}
    assert third["values"] == {"id": "v2"}
