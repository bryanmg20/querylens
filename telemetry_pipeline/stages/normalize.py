from datetime import datetime
import re
import sqlglot
from sqlglot import exp

from querylens_contract.snapshot import to_bool, to_int_list, to_iso
from models.stats import Stats
from stages import canonicalizers

def _to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value

class NormalizeStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats: Stats) -> Stats:
        canonicalizers.create_canonic_queries(
            stats,
            self.collector.source_dialect,
            clean_mysql=(self.collector.source_dialect == "mysql"),
        )
        normalize_statement_epochs(stats)
        stats = self.collector.normalize_engine_artifacts(stats)
        canonicalizers.normalize_querytext_active(stats, self.collector.source_dialect)
        return stats


EPOCH_FIELDS = ("counters_epoch", "minmax_epoch")

EPOCH_SECTIONS = ("statements", "top_impact_queries", "non_explainable_candidates")


# Las conversiones de abajo usan los mismos helpers que los validadores del
# contrato (querylens_contract.snapshot): una sola fuente para la forma de
# fechas, pids y estado de locks. El stage las aplica para que stats ya tenga
# la forma del contrato antes de validar.


def normalize_statement_epochs(stats: Stats):
    """Deja las fechas de contadores en un unico formato sin tz, en UTC.

    Postgres (`stats_since`, timestamptz) y MySQL (`FIRST_SEEN`, DATETIME) llegan
    con tipos distintos: uno con zona y el otro naive. La sesion MySQL se fija a
    +00:00 (init_command en config/connections) y el timestamptz se pasa a UTC
    (querylens_contract.snapshot.to_iso). Se hornean a la misma representacion que usa
    `normalize_active_query_timestamps`, asi el consumidor puede compararlas
    entre motores sin parsear dos formatos.
    """
    for section in EPOCH_SECTIONS:
        for row in stats.get(section) or []:
            for field in EPOCH_FIELDS:
                if isinstance(row.get(field), datetime):
                    row[field] = to_iso(row[field])


ACTIVE_QUERY_TIMESTAMP_FIELDS = ("transaction_start_time", "wait_start_time")


def normalize_active_query_timestamps(stats: Stats):
    for stmt in stats.get("active_queries") or []:
        for field in ACTIVE_QUERY_TIMESTAMP_FIELDS:
            if isinstance(stmt.get(field), datetime):
                stmt[field] = to_iso(stmt[field])

def normalize_blocking_pids(stats: Stats):
    for query in stats.get("active_queries") or []:
        query["blocking_pids"] = to_int_list(query.get("blocking_pids"))

def normalize_locks(stats: Stats):
    for lock in stats.get("locks") or []:
        lock["is_granted"] = to_bool(lock.get("is_granted"))

def normalize_predicate(stats: Stats):
    for explain in stats.get("canonic_explains") or []:
        for operation in explain.get("canonical_plan", {}).get("physical_operations", []):
            predicate = operation.get("predicate")
            if predicate:
                cleaned_predicate = clean_mysql_explain_predicate_dynamic(predicate)
                operation["predicate"] = cleaned_predicate

def clean_mysql_explain_predicate_dynamic(pred_text):
    if not pred_text:
        return None

    try:
        preprocessed = pred_text.replace('<cache>', '').replace('</cache>', '')
        ast = sqlglot.parse_one(preprocessed, read='mysql')
        for column in list(ast.find_all(exp.Column)):
            if column.db:
                column.set('db', None)
        for table in list(ast.find_all(exp.Table)):
            if table.db:
                table.set('db', None)
        # MySQL copia la subconsulta entera dentro del predicado de la tabla de
        # afuera: sus columnas y funciones parecian de esa tabla. Va la misma
        # etiqueta que pone Postgres, (SubPlan N); la subconsulta tiene su
        # propio scan en el plan. Antes que los literales, para que los de
        # adentro no consuman numeros de $n.
        subplans = _replace_subqueries(ast)
        # N-1: el plan de MySQL se armo con la query real, asi que los
        # predicados traen literales de datos (numeros, strings). La cola PGMQ
        # no los puede conservar: se reemplazan por placeholders $n, igual que
        # canonicalize_query hace con query_text. NULL/TRUE/FALSE no son datos
        # y dejan el predicado legible, asi que no se tocan. El texto (string,
        # hex, bit) sale como '$n' y el numero como $n: solo viaja el tipo, que
        # el consumidor necesita para ver la conversion implicita de MySQL
        # (`telefono = 5551234`), que el plan no muestra.
        for node in list(ast.find_all(*canonicalizers.DATA_LITERAL_NODES, exp.Parameter)):
            if isinstance(node, exp.Literal) and node.is_string or isinstance(
                node, (exp.HexString, exp.BitString, exp.ByteString)
            ):
                node.replace(exp.Literal.string("%s"))
            else:
                node.replace(exp.Placeholder())
        canonic = ast.sql(dialect='postgres', identify=False, comments=False)

        param_index = 1

        def replace_placeholder(match):
            nonlocal param_index
            current_param = f"${param_index}"
            param_index += 1
            return current_param

        canonic = re.sub(r"%s", replace_placeholder, canonic).replace('"', '')
        for number, marker in enumerate(subplans, start=1):
            canonic = canonic.replace(marker, f"(SubPlan {number})")
        return canonic
    except Exception:
        cleaned = re.sub(r'/\*.*?\*/', '', pred_text)
        cleaned = cleaned.replace('`', '')
        match = re.search(r'\b([a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\.', cleaned)
        if match:
            dynamic_schema = match.group(1)
            cleaned = re.sub(rf'\b{dynamic_schema}\.', '', cleaned)
        # N-1: si sqlglot no pudo parsear, el fallback igualmente no puede
        # dejar literales crudos pasar (aqui entra el %7% de un LIKE, por
        # ejemplo). Un solo pase alternando string/numero, en orden de lectura.
        counter = {"n": 0}

        def replace_literal(match):
            counter["n"] += 1
            # Mismo criterio que el camino sqlglot: texto/hex/bit como '$n'.
            literal = match.group(0)
            if literal[0].isdigit() and literal[:2].lower() not in ("0x", "0b"):
                return f"${counter['n']}"
            return f"'${counter['n']}'"

        cleaned = _LITERAL_RE.sub(replace_literal, cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned


def _replace_subqueries(ast):
    """Reemplaza cada subconsulta (la de afuera de cada anidamiento) por un
    marcador y devuelve los marcadores en orden de lectura. `= (SELECT ..)` y
    `IN (SELECT ..)` pierden el Subquery; EXISTS (SELECT ..) entero."""
    markers = []
    while True:
        nested = next(
            (select for select in ast.find_all(exp.Select) if select is not ast),
            None,
        )
        if nested is None:
            return markers
        target = nested
        if isinstance(target.parent, exp.Subquery):
            target = target.parent
        if isinstance(target.parent, exp.Exists):
            target = target.parent
        marker = f"__ql_subplan_{len(markers) + 1}__"
        markers.append(marker)
        target.replace(exp.column(marker))


def redact_postgres_plan_predicate(pred_text):
    """Literales de un predicado de plan de Postgres a $n.

    El plan de auto_explain sale de una ejecucion real: Filter / Index Cond
    traen los valores ((c <> 'abc'::bpchar), (id < 2000)), igual que los
    predicados de MySQL (N-1). Se reemplazan sobre el texto que deparsea
    Postgres, sin pasar por sqlglot. El cast que Postgres pega al literal
    ('abc'::bpchar) se va con el: el plan generico muestra el parametro solo
    ((c ~~ $1)) y los dos formatos tienen que coincidir. Los casts sobre
    columnas ((c)::text) salen igual en ambos planes y se dejan. Los
    identificadores entre comillas, los $n que ya estaban y las referencias
    SubPlan/InitPlan N no son datos y se dejan. La numeracion sigue al $n mas
    alto del texto."""
    if not pred_text:
        return pred_text
    existing = [int(n) for n in re.findall(r"\$(\d+)", pred_text)]
    counter = {"n": max(existing, default=0)}

    def replace(match):
        if match.group("keep"):
            return match.group(0)
        counter["n"] += 1
        return f"${counter['n']}"

    return _PG_PLAN_TOKEN_RE.sub(replace, pred_text)


# Nombre de tipo como lo escribe format_type: con schema y comillas
# opcionales, multi-palabra (character varying, double precision,
# timestamp(3) with time zone), con typmod y como array.
_PG_IDENT = r'(?:"(?:""|[^"])*"|[A-Za-z_]\w*)'
_PG_CAST = (
    rf"(?:::{_PG_IDENT}(?:\.{_PG_IDENT})?"
    r"(?:\s+(?:varying|precision))?"
    r"(?:\(\d+(?:,\s*\d+)?\))?"
    r"(?:\s+with(?:out)?\s+time\s+zone)?"
    r"(?:\[\])*)*"
)

# Un solo pase en orden de lectura: lo que se conserva va primero en la
# alternancia para que un numero dentro de "col 1" o de SubPlan 1 no se tome
# como literal. E'..', B'..' y X'..' caen enteros en un solo $n, junto con
# los casts que los siguen.
_PG_PLAN_TOKEN_RE = re.compile(
    r'(?P<keep>"(?:""|[^"])*"|\b(?:Sub|Init)Plan \d+|\$\d+)'
    rf"|(?:\b[eEbBxX])?'(?:''|[^'])*'{_PG_CAST}"
    rf"|(?<![\w$])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?{_PG_CAST}"
)


# Hex/bit primero: 0xDEAD o X'0A' deben caer enteros en un solo $n, no dejar el
# prefijo (X$1) ni partirse en digitos sueltos.
_LITERAL_RE = re.compile(
    r"\b[xXbB]'[^']*'|'(?:''|[^'])*'|\b0[xXbB][0-9a-fA-F]+\b|\b\d+(?:\.\d+)?\b"
)