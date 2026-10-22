"""Informes de abrazo mortal de cada motor, traducidos al modelo canonico.

Funciones puras: DeadlocksStage hace la lectura y les pasa el texto crudo.
Salen dicts con la forma de querylens_contract.DeadlockReport.

Privacidad: los dos informes traen las sentencias con sus literales, y el de
InnoDB ademas vuelca en hexadecimal el contenido de cada registro bloqueado.
De las sentencias solo sale canonic_query; de los registros, su direccion
fisica (space:page:heap). Nada mas del texto crudo llega al snapshot.

Postgres (mensaje "deadlock detected" del log, state_code 40P01):
    detail  Process 790 waits for ShareLock on transaction 67257; blocked by process 797.
            Process 797 waits for ShareLock on transaction 67256; blocked by process 790.
            Process 790: UPDATE ql_ct01_cuentas SET saldo=saldo+10 WHERE id=2;
            Process 797: UPDATE ql_ct01_cuentas SET saldo=saldo+5 WHERE id=1;
    context while updating tuple (0,2) in relation "ql_ct01_cuentas"
Solo dice que espera cada proceso, no que mantiene; la victima es el pid de
la linea y CONTEXT da la tabla de su espera.

MySQL (seccion LATEST DETECTED DEADLOCK de SHOW ENGINE INNODB STATUS): por
transaccion, que mantiene y que espera, con indice, modo y registro.
"""
import re
from datetime import datetime

import sqlglot
from sqlglot import exp

from stages.canonicalizers import canonicalize_query

# Tope de recursos por bloqueo. Un UPDATE sin indice (CT-04) puede mantener
# miles de registros; para encontrar el ciclo alcanza con unos pocos y
# rows_locked conserva el total.
MAX_RESOURCES_PER_LOCK = 64


# ---------------------------------------------------------------- postgres

_PG_WAIT_RE = re.compile(r"^Process (\d+) waits for (\S+) on (.+?); blocked by process (\d+)\.$")
_PG_STATEMENT_RE = re.compile(r"^Process (\d+): (.*)$")
_PG_TUPLE_RE = re.compile(r"^tuple \((\d+),(\d+)\) of relation (\d+)")
_PG_RELATION_RE = re.compile(r"^relation (\d+)")
_PG_TRANSACTION_RE = re.compile(r"^transaction (\d+)")
_PG_CONTEXT_TABLE_RE = re.compile(r'tuple \(\d+,\d+\) in relation "((?:[^"]|"")+)"')
_PG_OF_DATABASE_RE = re.compile(r" of database \d+$")


def parse_postgres_deadlock(record: dict) -> dict:
    """Una linea jsonlog con state_code 40P01. detected_at queda con el
    timestamp crudo del log (en log_timezone): lo convierte el stage, que
    puede preguntarle al servidor por la zona.

    Las esperas sobre una relacion traen relation_oid: el stage lo resuelve a
    nombre con pg_class y lo quita."""
    waits: dict[int, dict] = {}
    statements: dict[int, str] = {}
    current = None
    for line in (record.get("detail") or "").split("\n"):
        wait = _PG_WAIT_RE.match(line)
        if wait:
            pid = int(wait.group(1))
            waits[pid] = {
                "lock_mode": wait.group(2),
                "target": wait.group(3),
                "blocked_by": int(wait.group(4)),
            }
            current = None
            continue
        statement = _PG_STATEMENT_RE.match(line)
        if statement and int(statement.group(1)) in waits:
            current = int(statement.group(1))
            statements[current] = statement.group(2)
        elif current is not None:
            # la sentencia sigue en varias lineas
            statements[current] += "\n" + line

    report = {
        "detected_at": record.get("timestamp"),
        "source": "postgres_log",
        "database_name": record.get("dbname"),
        "complete": len(waits) >= 2,
        "transactions": [],
    }
    if not report["complete"]:
        # log_error_verbosity=terse: el log trae el mensaje sin DETAIL
        return report

    victim = record.get("pid")
    context_table = _pg_context_table(record.get("context"))
    for pid, wait in waits.items():
        canonic = canonicalize_query(statements.get(pid), "postgres") if pid in statements else None
        lock = _pg_lock(wait["lock_mode"], wait["target"])
        if lock.get("table_name") is None and lock.get("relation_oid") is None:
            # Postgres espera la fila sobre el transactionid de quien la tiene:
            # la tabla sale del CONTEXT (solo la victima) o de su sentencia
            lock["table_name"] = (context_table if pid == victim else None) or statement_table(canonic)
        report["transactions"].append({
            "process_id": pid,
            "transaction_id": str(record["txid"]) if pid == victim and record.get("txid") else None,
            "query_id": record.get("query_id") if pid == victim else None,
            "canonic_query": canonic,
            "holds": [],
            "waits_for": lock,
            "blocked_by": [wait["blocked_by"]],
            "victim": pid == victim,
        })
    return report


def _pg_lock(lock_mode: str, target: str) -> dict:
    target = _PG_OF_DATABASE_RE.sub("", target)
    lock = {"lock_mode": lock_mode, "resources": [target]}
    tuple_match = _PG_TUPLE_RE.match(target)
    relation_match = _PG_RELATION_RE.match(target)
    if tuple_match:
        lock.update(lock_type="tuple", relation_oid=int(tuple_match.group(3)))
    elif relation_match:
        lock.update(lock_type="relation", relation_oid=int(relation_match.group(1)))
    elif _PG_TRANSACTION_RE.match(target):
        lock["lock_type"] = "transactionid"
    elif target.startswith("virtual transaction"):
        lock["lock_type"] = "virtualxid"
    else:
        # advisory lock [..], page N of relation, extension of relation, object ...
        lock["lock_type"] = target.split(" ", 1)[0]
    return lock


def _pg_context_table(context: str | None) -> str | None:
    if not context:
        return None
    match = _PG_CONTEXT_TABLE_RE.search(context)
    return match.group(1).replace('""', '"') if match else None


def statement_table(canonic_query: str | None) -> str | None:
    """Tabla que modifica o bloquea la sentencia: la del UPDATE/DELETE/INSERT,
    o la primera del FROM de un SELECT ... FOR UPDATE/SHARE."""
    if not canonic_query or canonic_query == "Not available":
        return None
    try:
        ast = sqlglot.parse_one(canonic_query, read="postgres")
    except Exception:
        return None
    target = ast.this if isinstance(ast, (exp.Update, exp.Delete, exp.Insert)) else None
    if isinstance(target, exp.Schema):
        # INSERT INTO t (a, b)
        target = target.this
    if not isinstance(target, exp.Table):
        target = ast.find(exp.Table)
    return target.name if isinstance(target, exp.Table) and target.name else None


# ------------------------------------------------------------------- mysql

_INNODB_SECTION_RE = re.compile(
    r"LATEST DETECTED DEADLOCK\n-+\n(?P<body>.*?)\n-{4,}\n[A-Z][A-Z /]*\n", re.DOTALL
)
_INNODB_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_INNODB_MARKER_RE = re.compile(
    r"^\*\*\* \((\d+)\) (TRANSACTION|HOLDS THE LOCK\(S\)|WAITING FOR THIS LOCK TO BE GRANTED):\s*$"
)
_INNODB_VICTIM_RE = re.compile(r"^\*\*\* WE ROLL BACK TRANSACTION \((\d+)\)")
_INNODB_TRX_RE = re.compile(r"^TRANSACTION (\d+), ")
_INNODB_THREAD_RE = re.compile(r"^MySQL thread id (\d+),")
_INNODB_ROW_LOCKS_RE = re.compile(r"(\d+) row lock\(s\)")
_INNODB_UNDO_RE = re.compile(r"undo log entries (\d+)")
_INNODB_RECORD_LOCKS_RE = re.compile(
    r"^RECORD LOCKS space id (\d+) page no (\d+) n bits \d+ index (\S+) of table "
    r"`((?:[^`]|``)+)`\.`((?:[^`]|``)+)`.*? trx id \d+ lock[_ ]mode (.+)$"
)
_INNODB_TABLE_LOCK_RE = re.compile(
    r"^TABLE LOCK table `((?:[^`]|``)+)`\.`((?:[^`]|``)+)`.*? trx id \d+ lock mode (.+)$"
)
_INNODB_RECORD_RE = re.compile(r"^Record lock, heap no (\d+)")
# Lineas de una transaccion que no son la sentencia
_INNODB_TRX_HEADER_RE = re.compile(
    r"^(TRANSACTION \d+|mysql tables in use|LOCK WAIT|\d+ lock struct|MySQL thread id|"
    r"Trx read view|TOO DEEP|---)"
)


def parse_innodb_status(status: str) -> dict | None:
    """La seccion LATEST DETECTED DEADLOCK de SHOW ENGINE INNODB STATUS, o
    None si el servidor no tuvo abrazos desde que arranco. detected_at queda
    como lo escribe InnoDB, en la zona del sistema: lo pasa a UTC el stage."""
    match = _INNODB_SECTION_RE.search(status or "")
    if match is None:
        return None
    lines = match.group("body").split("\n")
    stamp = _INNODB_TIMESTAMP_RE.match(lines[0]) if lines else None
    if stamp is None:
        return None

    blocks: dict[int, dict[str, list[str]]] = {}
    victim = None
    current = None
    for line in lines[1:]:
        marker = _INNODB_MARKER_RE.match(line)
        if marker:
            current = blocks.setdefault(int(marker.group(1)), {}).setdefault(marker.group(2), [])
            continue
        victim_match = _INNODB_VICTIM_RE.match(line)
        if victim_match:
            victim = int(victim_match.group(1))
            current = None
            continue
        if current is not None:
            current.append(line)

    transactions = [
        _innodb_transaction(blocks[number], number == victim)
        for number in sorted(blocks)
        if "TRANSACTION" in blocks[number]
    ]
    complete = (
        len(transactions) >= 2
        and victim is not None
        and all(trx["waits_for"] is not None for trx in transactions)
    )
    if complete:
        _innodb_blocked_by(transactions)
    else:
        # TOO DEEP OR LONG SEARCH: InnoDB corto la busqueda sin informar el ciclo
        transactions = []
    return {
        "detected_at": stamp.group(1),
        "source": "innodb_status",
        "database_name": transactions[0]["waits_for"]["schema_name"] if transactions else None,
        "complete": complete,
        "transactions": transactions,
    }


def _innodb_transaction(block: dict[str, list[str]], victim: bool) -> dict:
    header = block.get("TRANSACTION", [])
    trx = {
        "process_id": None,
        "transaction_id": None,
        "canonic_query": None,
        "holds": _innodb_locks(block.get("HOLDS THE LOCK(S)", [])),
        "waits_for": next(iter(_innodb_locks(block.get("WAITING FOR THIS LOCK TO BE GRANTED", []))), None),
        "blocked_by": [],
        "rows_locked": None,
        "undo_log_entries": 0,
        "victim": victim,
    }
    statement = []
    for line in header:
        if (match := _INNODB_TRX_RE.match(line)):
            trx["transaction_id"] = match.group(1)
        if (match := _INNODB_THREAD_RE.match(line)):
            trx["process_id"] = int(match.group(1))
            statement = []
            continue
        if (match := _INNODB_ROW_LOCKS_RE.search(line)):
            trx["rows_locked"] = int(match.group(1))
        if (match := _INNODB_UNDO_RE.search(line)):
            trx["undo_log_entries"] = int(match.group(1))
        if trx["process_id"] is not None and line.strip() and not _INNODB_TRX_HEADER_RE.match(line):
            # despues de "MySQL thread id ..." viene la sentencia, en una o varias lineas
            statement.append(line)
    if statement:
        trx["canonic_query"] = canonicalize_query("\n".join(statement), "mysql")
    return trx


def _innodb_locks(lines: list[str]) -> list[dict]:
    locks = []
    for line in lines:
        if (match := _INNODB_RECORD_LOCKS_RE.match(line)):
            space, page, index, schema, table, mode = match.groups()
            locks.append({
                "schema_name": _unquote(schema),
                "table_name": _unquote(table),
                "index_name": index.strip("`"),
                "lock_type": "record",
                "lock_mode": innodb_lock_mode(mode),
                "resources": [],
                "_page": f"{space}:{page}",
            })
        elif (match := _INNODB_TABLE_LOCK_RE.match(line)):
            schema, table, mode = match.groups()
            locks.append({
                "schema_name": _unquote(schema),
                "table_name": _unquote(table),
                "index_name": None,
                "lock_type": "table",
                "lock_mode": innodb_lock_mode(mode),
                "resources": [f"table {_unquote(schema)}.{_unquote(table)}"],
            })
        elif (match := _INNODB_RECORD_RE.match(line)) and locks and locks[-1]["lock_type"] == "record":
            resources = locks[-1]["resources"]
            if len(resources) < MAX_RESOURCES_PER_LOCK:
                resources.append(f"{locks[-1]['_page']}:{match.group(1)}")
        # el resto (n_fields, el volcado hex de cada campo) son datos de la fila: se descarta
    for lock in locks:
        lock.pop("_page", None)
    return locks


def innodb_lock_mode(text: str) -> str:
    """'X locks rec but not gap waiting' -> 'X,REC_NOT_GAP', con los nombres de
    performance_schema.data_locks.LOCK_MODE."""
    words = text.replace(" waiting", "").split()
    if not words:
        return text
    mode = [words[0]]
    rest = " ".join(words[1:])
    if "locks rec but not gap" in rest:
        mode.append("REC_NOT_GAP")
    if "locks gap before rec" in rest:
        mode.append("GAP")
    if "insert intention" in rest:
        mode.append("INSERT_INTENTION")
    return ",".join(mode)


def _innodb_blocked_by(transactions: list[dict]) -> None:
    """Quien retiene lo que espera cada transaccion: el que mantiene un
    bloqueo sobre el mismo recurso. Con dos transacciones y sin cruce (el
    informe de InnoDB a veces omite el bloqueo exacto) es la otra."""
    for trx in transactions:
        wanted = set(trx["waits_for"]["resources"])
        holders = [
            other["process_id"]
            for other in transactions
            if other is not trx and any(wanted & set(lock["resources"]) for lock in other["holds"])
        ]
        if not holders and len(transactions) == 2:
            holders = [other["process_id"] for other in transactions if other is not trx]
        trx["blocked_by"] = [pid for pid in holders if pid is not None]


def _unquote(identifier: str) -> str:
    return identifier.replace("``", "`")


def parse_innodb_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
