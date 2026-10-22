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

Cada bloqueo sale con el vocabulario comun del contrato (mode, scope,
targets) ademas del crudo del motor: los detectores leen solo el comun, y un
motor nuevo solo necesita su parser. Lo que el motor no informa queda en None.
"""
import re
from datetime import datetime

import sqlglot
from sqlglot import exp

from stages.canonicalizers import canonicalize_query

# Tope de recursos por bloqueo. Un UPDATE sin indice (CT-04) puede mantener
# miles de registros; rows_locked conserva el total. Lo que espera alguna
# transaccion del informe se conserva aunque pase el tope: es el cruce del
# ciclo (_trim_resources).
MAX_RESOURCES_PER_LOCK = 64


# ---------------------------------------------------------------- postgres

_PG_WAIT_RE = re.compile(r"^Process (\d+) waits for (\S+) on (.+?); blocked by process (\d+)\.$")
_PG_STATEMENT_RE = re.compile(r"^Process (\d+): (.*)$")
_PG_TUPLE_RE = re.compile(r"^tuple \((\d+),(\d+)\) of relation (\d+)")
_PG_RELATION_RE = re.compile(r"^relation (\d+)")
_PG_TRANSACTION_RE = re.compile(r"^transaction (\d+)")
_PG_CONTEXT_TABLE_RE = re.compile(r'tuple \(\d+,\d+\) in relation "((?:[^"]|"")+)"')
_PG_OF_DATABASE_RE = re.compile(r" of database \d+$")

# Modo comun de cada modo de pg_locks segun sobre que se pide. Los que no
# tienen equivalente (ShareUpdateExclusiveLock, ShareRowExclusiveLock) quedan
# en None.
_PG_RELATION_MODES = {
    "AccessShareLock": "IS",
    "RowShareLock": "IS",
    "RowExclusiveLock": "IX",
    "ShareLock": "S",
    "ExclusiveLock": "X",
    "AccessExclusiveLock": "X",
}
# El bloqueo de tupla con que Postgres ordena la cola de una fila: FOR KEY
# SHARE y FOR SHARE lo piden en AccessShare y RowShare, FOR NO KEY UPDATE y
# FOR UPDATE en Exclusive y AccessExclusive.
_PG_TUPLE_MODES = {
    "AccessShareLock": "S",
    "RowShareLock": "S",
    "ExclusiveLock": "X",
    "AccessExclusiveLock": "X",
}
_PG_OTHER_MODES = {"ShareLock": "S", "ExclusiveLock": "X"}

_PG_WRITE_RE = re.compile(r"^(UPDATE|DELETE|INSERT|MERGE)\b")
_PG_FOR_UPDATE_RE = re.compile(r"\bFOR (NO KEY )?UPDATE\b")
_PG_FOR_SHARE_RE = re.compile(r"\bFOR (KEY )?SHARE\b")


def parse_postgres_deadlock(record: dict) -> dict:
    """Una linea jsonlog con state_code 40P01. detected_at queda con el
    timestamp crudo del log (en log_timezone): lo convierte el stage, que
    puede preguntarle al servidor por la zona.

    Las esperas sobre una relacion traen relation_oid: el stage lo resuelve a
    nombre con pg_class y lo quita. Las esperas de fila traen row_key cuando
    la sentencia dice que fila toca: el stage lo convierte en targets y lo
    quita."""
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
    # (columna, literal) -> token, compartido por todo el informe
    tokens: dict[tuple[str, str], str] = {}
    for pid, wait in waits.items():
        canonic = canonicalize_query(statements.get(pid), "postgres") if pid in statements else None
        lock = _pg_lock(wait["lock_mode"], wait["target"], canonic)
        if lock.get("table_name") is None and lock.get("relation_oid") is None:
            # Postgres espera la fila sobre el transactionid de quien la tiene:
            # la tabla sale del CONTEXT (solo la victima) o de su sentencia
            lock["table_name"] = (context_table if pid == victim else None) or statement_table(canonic)
        if lock["lock_type"] == "transactionid" and pid in statements:
            row_key = pg_row_key(statements[pid], tokens)
            if row_key is not None:
                # el rol de la linea es el de la victima: para la otra se
                # asume la misma aplicacion. Lo usa el stage para el search_path
                lock["row_key"] = {**row_key, "role": record.get("user")}
        report["transactions"].append({
            "process_id": pid,
            "transaction_id": str(record["txid"]) if pid == victim and record.get("txid") else None,
            "query_id": record.get("query_id") if pid == victim else None,
            "canonic_query": canonic,
            # el log no dice que mantiene cada proceso
            "holds": None,
            "waits_for": lock,
            "blocked_by": [wait["blocked_by"]],
            "victim": pid == victim,
        })
    return report


def _pg_lock(lock_mode: str, target: str, canonic_query: str | None = None) -> dict:
    target = _PG_OF_DATABASE_RE.sub("", target)
    lock = {"lock_mode": lock_mode, "resources": [target]}
    tuple_match = _PG_TUPLE_RE.match(target)
    relation_match = _PG_RELATION_RE.match(target)
    if tuple_match:
        lock.update(lock_type="tuple", relation_oid=int(tuple_match.group(3)))
        lock.update(mode=_PG_TUPLE_MODES.get(lock_mode), scope="row", targets=[target])
    elif relation_match:
        lock.update(lock_type="relation", relation_oid=int(relation_match.group(1)))
        lock.update(mode=_PG_RELATION_MODES.get(lock_mode), scope="table", targets=[target])
    elif _PG_TRANSACTION_RE.match(target):
        # La espera de una fila: el ShareLock es sobre la transaccion que la
        # tiene, no dice que fila ni en que modo se pide. El modo sale de la
        # sentencia que espera; la fila no se sabe.
        lock["lock_type"] = "transactionid"
        lock.update(mode=statement_lock_mode(canonic_query), scope="row", targets=None)
    elif target.startswith("virtual transaction"):
        # espera a que termine otra transaccion (CREATE INDEX CONCURRENTLY...)
        lock["lock_type"] = "virtualxid"
        lock.update(mode=None, scope="other", targets=None)
    else:
        # advisory lock [..], page N of relation, extension of relation, object ...
        lock["lock_type"] = target.split(" ", 1)[0]
        lock.update(mode=_PG_OTHER_MODES.get(lock_mode), scope="other", targets=[target])
    return lock


def statement_lock_mode(canonic_query: str | None) -> str | None:
    """El modo de fila que pide la sentencia: X para las que modifican y FOR
    [NO KEY] UPDATE, S para FOR [KEY] SHARE."""
    if not canonic_query or canonic_query == "Not available":
        return None
    if _PG_WRITE_RE.match(canonic_query) or _PG_FOR_UPDATE_RE.search(canonic_query):
        return "X"
    if _PG_FOR_SHARE_RE.search(canonic_query):
        return "S"
    return None


def pg_row_key(statement: str, tokens: dict[tuple[str, str], str]) -> dict | None:
    """Que fila toca la sentencia segun su WHERE: la tabla y las igualdades
    columna = literal del AND de primer nivel. El log no dice que fila espera
    cada proceso; esto es lo unico del informe que la identifica, y solo si la
    sentencia trae los valores escritos (con $1 no hay de donde sacarlos).

    Ningun literal sale de aca: cada valor se reemplaza por un token (v1,
    v2...) que se comparte entre las sentencias del mismo informe, asi que dos
    sentencias con id = 1 llevan el mismo token sin decir cual es el valor.
    Que las columnas identifiquen una sola fila lo decide el stage."""
    try:
        ast = sqlglot.parse_one(statement, read="postgres")
    except Exception:
        return None
    if isinstance(ast, exp.Update):
        if ast.args.get("from_"):
            return None
        table = ast.this
    elif isinstance(ast, exp.Delete):
        if ast.args.get("using"):
            return None
        table = ast.this
    elif isinstance(ast, exp.Select) and ast.args.get("locks"):
        if ast.args.get("joins") or not ast.args.get("from_"):
            return None
        table = ast.args["from_"].this
    else:
        return None
    where = ast.args.get("where")
    if not isinstance(table, exp.Table) or where is None:
        return None

    names = {table.name, table.alias_or_name}
    values = {}
    for condition in _conjuncts(where.this):
        pair = _column_equals_literal(condition, names)
        if pair is not None:
            column, literal = pair
            values[column] = tokens.setdefault((column, literal), f"v{len(tokens) + 1}")
    if not values:
        return None
    schema = table.args.get("db")
    return {
        "schema": _pg_identifier(schema) if schema else None,
        "table": _pg_identifier(table.this),
        "values": values,
    }


def _pg_identifier(identifier: exp.Identifier) -> str:
    """Como lo guarda pg_catalog: sin comillas Postgres lo pasa a minusculas."""
    return identifier.name if identifier.quoted else identifier.name.lower()


def _conjuncts(node):
    if isinstance(node, exp.Paren):
        yield from _conjuncts(node.this)
    elif isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    else:
        yield node


def _column_equals_literal(node, table_names: set[str]) -> tuple[str, str] | None:
    if not isinstance(node, exp.EQ):
        return None
    for column, value in ((node.this, node.expression), (node.expression, node.this)):
        if not isinstance(column, exp.Column) or (column.table and column.table not in table_names):
            continue
        literal = _literal(value)
        if literal is None:
            continue
        return _pg_identifier(column.this), literal
    return None


def _literal(node) -> str | None:
    """'1' y 1 son el mismo valor para la fila: se compara el texto. Un $1
    (Parameter) no es literal."""
    if isinstance(node, exp.Cast):
        node = node.this
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal) and not node.this.is_string:
        return f"-{node.this.this}"
    if isinstance(node, exp.Literal):
        return node.this
    return None


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
        _trim_resources(transactions)
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
            # sin tope: se recorta despues de cruzar las esperas (_trim_resources)
            locks[-1]["resources"].append(f"{locks[-1]['_page']}:{match.group(1)}")
        # el resto (n_fields, el volcado hex de cada campo) son datos de la fila: se descarta
    for lock in locks:
        lock.pop("_page", None)
        lock.update(innodb_canonical(lock["lock_type"], lock["lock_mode"]))
        # el registro o la tabla ya es la identidad fisica del recurso
        lock["targets"] = list(lock["resources"]) or None
    return locks


def innodb_canonical(lock_type: str, lock_mode: str) -> dict:
    """'X,REC_NOT_GAP' de un registro -> mode X, scope row. Sin sufijo un
    bloqueo de registro es next-key: la fila y el hueco anterior."""
    mode, *flags = lock_mode.split(",")
    canonical_mode = mode if mode in ("S", "X", "IS", "IX") else None
    if lock_type == "table":
        # AUTO-INC no es S ni X: queda sin modo
        return {"mode": canonical_mode, "scope": "table"}
    if "INSERT_INTENTION" in flags:
        scope = "insert_intention"
    elif "GAP" in flags:
        scope = "gap"
    elif "REC_NOT_GAP" in flags:
        scope = "row"
    else:
        scope = "row+gap"
    return {"mode": canonical_mode, "scope": scope}


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


def _trim_resources(transactions: list[dict]) -> None:
    """Cada bloqueo a MAX_RESOURCES_PER_LOCK recursos, pero sin perder lo que
    espera alguna transaccion del informe: blocked_by ya se calculo con todo
    y los detectores buscan ese cruce en holds."""
    waited = {resource for trx in transactions for resource in trx["waits_for"]["resources"]}
    for trx in transactions:
        for lock in [trx["waits_for"], *trx["holds"]]:
            # en el orden del informe
            budget = MAX_RESOURCES_PER_LOCK - sum(resource in waited for resource in lock["resources"])
            kept = []
            for resource in lock["resources"]:
                if resource in waited:
                    kept.append(resource)
                elif budget > 0:
                    kept.append(resource)
                    budget -= 1
            lock["resources"] = kept
            lock["targets"] = list(kept) or None


def _unquote(identifier: str) -> str:
    return identifier.replace("``", "`")


def parse_innodb_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
