"""Variantes del plan generico de Postgres con valores de pg_stats.

`EXPLAIN (GENERIC_PLAN)` no conoce el valor de `$n`: para `col = $1` estima
con n_distinct (un promedio) y para un rango usa una selectividad fija. En una
columna sesgada esa estimacion no corresponde a ninguna ejecucion real. Aqui se
piden ademas dos planes custom con valores reales de las estadisticas de la
columna: uno con el valor que trae muchas filas y otro con el que trae pocas.
Asi se sabe si el plan cambia con el valor y en que rango se mueven las filas.

Al snapshot viaja un solo plan: select_plan elige, entre el generico y las
variantes, el que estima un numero de filas mas cercano a avg_rows_per_call
de pg_stat_statements (filas reales por ejecucion). Ese es el plan que mejor
explica lo que la base ejecuta de verdad.

Todo se calcula en el ciclo actual, sin estado entre snapshots. Si algo no se
puede resolver (un `$n` que no se mapea a una columna, una tabla sin
estadisticas, un error), no hay variantes y el snapshot queda con el plan
generico, igual que antes.

Privacidad: los valores de pg_stats son datos reales de la tabla. Son flujo
interno, como query_sample_text en MySQL: viven solo en variables locales de
explain_variants, viajan como bind params (nunca dentro del texto SQL) y no se
escriben en stats. Los planes custom traen el valor en Filter/Index Cond; el
normalizer anonimiza esos predicados con anonymize_predicate.
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import bindparam, text

from stages.sql_text import mask_sql

PREPARED_NAME = "ql_plan_variant"
LABELS = ("many_rows", "few_rows")

# Campos del nodo donde Postgres escribe la condicion que usa un $n.
_COND_KEYS = ("Index Cond", "Recheck Cond", "Filter")

# Columna, opcionalmente calificada por alias, opcionalmente entre parentesis
# y con cast: k, a.k, ((status)::text). El parentesis solo vale si no lo
# precede una palabra: en lower(email) la columna esta dentro de una funcion y
# no es mapeable.
_TYPE = r"(?:::[a-z_][\w ]*?(?:\[\])?)?"
_COL = (
    r"(?:(?<![\w.])\((?:(?P<{p}q>\w+)\.)?(?P<{p}c>\w+)\)"
    r"|(?<![\w.$])(?:(?P<{p}q2>\w+)\.)?(?P<{p}c2>\w+))" + _TYPE
)
_OP = r"(?P<op><=|>=|=|<|>)"
_PARAM = r"\$(?P<n>\d+)" + _TYPE
# Despues del $n solo puede cerrar la condicion: en (k > $1 * 2) el valor no
# es el $n solo y la variante no seria el plan de ese valor.
_END = r"(?=\s*(?:\)|\bAND\b|\bOR\b|$))"

_FORWARD = re.compile(_COL.format(p="") + r"\s*" + _OP + r"\s*" + _PARAM + _END)
_REVERSE = re.compile(
    r"(?<![\w$])" + _PARAM + r"\s*" + _OP + r"\s*" + _COL.format(p="") + _END
)
_ANY_PARAM = re.compile(r"\$(\d+)")
_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "="}

# Literal tal como Postgres lo escribe en un plan custom: string con '' como
# escape o numero, con cast opcional ('fallido'::text, 500,
# '2024-01-01 00:00:00'::timestamp without time zone). El cast es codicioso y
# solo toma palabras en minuscula: AND/OR del plan van en mayuscula. El numero
# no puede ser parte de un identificador, de un $n ni de "SubPlan 1".
_CAST = r"(?:::[a-z_]\w*(?: [a-z_]\w*)*(?:\[\])?)?"
_LITERAL = (
    r"(?:'(?:[^']|'')*'"
    r"|(?<![\w$.'])(?<!Plan )-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.]))" + _CAST
)
_ANY_LITERAL = re.compile(_LITERAL)

COLUMN_STATS_SQL = """
SELECT r.oid IS NOT NULL AS resolved,
       s.attname IS NOT NULL AS has_stats,
       s.most_common_vals::text::text[] AS mcv,
       s.histogram_bounds::text::text[] AS hist
FROM (SELECT to_regclass(quote_ident(:relation)) AS oid) r
LEFT JOIN pg_class c ON c.oid = r.oid
LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stats s
       ON s.schemaname = n.nspname AND s.tablename = c.relname AND s.attname = :column
ORDER BY s.inherited DESC NULLS LAST
LIMIT 1
"""


@dataclass(frozen=True)
class ParamRef:
    relation: str
    column: str
    op: str


@dataclass
class ColumnStats:
    mcv: list = field(default_factory=list)
    hist: list = field(default_factory=list)


@dataclass(frozen=True)
class Pick:
    many: str
    few: str
    varies: bool


@dataclass
class VariantsOutcome:
    variants: list = field(default_factory=list)
    stability: str | None = None
    stats_missing: bool = False


def query_params(query_text: str | None) -> list[int] | None:
    """Numeros de $n del texto, ordenados. None si no son 1..N contiguos (el
    EXECUTE los pasa por posicion) o si el texto no se puede leer."""
    code = mask_sql(query_text or "")
    if code is None:
        return None
    numbers = sorted({int(n) for n in _ANY_PARAM.findall(code)})
    if numbers != list(range(1, len(numbers) + 1)):
        return None
    return numbers


def _relations(node, aliases):
    if not isinstance(node, dict):
        return
    if node.get("Relation Name"):
        aliases[node.get("Alias") or node["Relation Name"]] = node["Relation Name"]
    for child in node.get("Plans") or []:
        _relations(child, aliases)


def anonymize_predicate(predicate):
    """Predicado de un plan custom sin los valores de pg_stats: cada literal
    pasa a $1, $2, ... en orden de lectura, numerado por predicado. Es la misma
    regla que clean_mysql_explain_predicate_dynamic aplica a los predicados de
    MySQL (N-1): el numero ordena los valores del predicado, no apunta al $n
    del texto de la query."""
    if not predicate:
        return predicate
    number = 0

    def replace(match):
        nonlocal number
        number += 1
        return f"${number}"

    return _ANY_LITERAL.sub(replace, predicate)


def _conditions(node, inherited=None):
    """(relacion del nodo, texto de la condicion). Un Bitmap Index Scan no
    trae Relation Name: su condicion es de la tabla del Bitmap Heap Scan padre."""
    if not isinstance(node, dict):
        return
    relation = node.get("Relation Name")
    if relation is None and str(node.get("Node Type", "")).startswith("Bitmap"):
        relation = inherited
    for key in _COND_KEYS:
        if isinstance(node.get(key), str):
            yield relation, node[key]
    for child in node.get("Plans") or []:
        yield from _conditions(child, relation)


def param_columns(root_plan, params: list[int]) -> dict[int, ParamRef] | None:
    """Mapea cada $n a (relacion, columna, operador) leyendo las condiciones
    del plan generico. None si algun $n no queda mapeado o queda mapeado a
    dos columnas distintas: sin un valor por parametro no hay EXECUTE."""
    if not params:
        return None
    aliases = {}
    _relations(root_plan, aliases)
    found: dict[int, ParamRef] = {}
    for node_relation, condition in _conditions(root_plan):
        for pattern, flip in ((_FORWARD, False), (_REVERSE, True)):
            for match in pattern.finditer(condition):
                qualifier = match.group("q") or match.group("q2")
                column = match.group("c") or match.group("c2")
                relation = aliases.get(qualifier) if qualifier else node_relation
                if relation is None:
                    return None
                op = _FLIP[match.group("op")] if flip else match.group("op")
                ref = ParamRef(relation, column, op)
                number = int(match.group("n"))
                if found.get(number, ref) != ref:
                    return None
                found[number] = ref
    if set(found) != set(params):
        return None
    return found


def _values(items):
    return [v for v in (items or []) if v is not None and not isinstance(v, list)]


def pick_values(op: str, stats: ColumnStats) -> Pick | None:
    """Valores de prueba para un $n. varies=False: con cualquier valor el plan
    es el mismo (columna sin valores frecuentes), many y few son el mismo valor
    representativo. None: no hay de donde sacar un valor."""
    mcv = _values(stats.mcv)
    hist = _values(stats.hist)
    if op == "=":
        middle = hist[len(hist) // 2] if hist else None
        if not mcv:
            # Sin valores frecuentes cada valor estima lo mismo que el generico.
            return Pick(middle, middle, False) if middle is not None else None
        few = middle if middle is not None else mcv[-1]
        if few == mcv[0]:
            return Pick(mcv[0], mcv[0], False)
        return Pick(mcv[0], few, True)
    # Rangos: el primer borde del histograma deja pasar casi todo con > y casi
    # nada con <. Los MCV no sirven aqui: vienen como texto y su orden no es el
    # del tipo de la columna.
    if len(hist) < 2:
        return None
    if op in (">", ">="):
        return Pick(hist[0], hist[-1], True)
    return Pick(hist[-1], hist[0], True)


def fetch_column_stats(conn, relation: str, column: str):
    """ColumnStats, "missing" si la tabla existe pero la columna no tiene
    estadisticas (sin ANALYZE), o None si la relacion no resuelve en el
    search_path de la transaccion."""
    stmt = text(COLUMN_STATS_SQL).bindparams(relation=relation, column=column)
    row = next(conn.execute(stmt).mappings(), None)
    if row is None or not row["resolved"]:
        return None
    if not row["has_stats"]:
        return "missing"
    return ColumnStats(mcv=row["mcv"] or [], hist=row["hist"] or [])


def _explain_execute(conn, values):
    names = [f"p{i}" for i in range(1, len(values) + 1)]
    placeholders = ", ".join(f":{name}" for name in names)
    stmt = text(f"EXPLAIN (FORMAT JSON) EXECUTE {PREPARED_NAME}({placeholders})")
    stmt = stmt.bindparams(*(bindparam(n, value=v) for n, v in zip(names, values)))
    return [dict(row) for row in conn.execute(stmt).mappings()]


def param_targets(query_text: str | None, root_plan) -> list[ParamRef] | None:
    """Un ParamRef por $n, en orden ($1, $2, ...). None si la query no tiene
    parametros o alguno no se mapea: no hay variantes y no se toca la base."""
    params = query_params(query_text)
    refs = param_columns(root_plan, params) if params else None
    if not refs:
        return None
    return [refs[number] for number in params]


def explain_variants(conn, query_text: str, targets: list[ParamRef]) -> VariantsOutcome:
    """Corre dentro de una transaccion corta del candidato, con el search_path
    ya fijado. Puede lanzar: quien llama descarta las variantes y conserva el
    plan generico."""
    picks = []
    for ref in targets:
        stats = fetch_column_stats(conn, ref.relation, ref.column)
        if stats is None:
            return VariantsOutcome()
        if stats == "missing":
            return VariantsOutcome(stats_missing=True)
        pick = pick_values(ref.op, stats)
        if pick is None:
            return VariantsOutcome()
        picks.append(pick)

    if not any(pick.varies for pick in picks):
        return VariantsOutcome(stability="stable")

    variants = []
    prepared = False
    try:
        # Savepoint: si un EXECUTE falla, la transaccion sigue usable y el
        # DEALLOCATE del finally corre. PREPARE es de sesion y un ROLLBACK no
        # lo deshace.
        with conn.begin_nested():
            conn.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
            conn.execute(text(f"PREPARE {PREPARED_NAME} AS {query_text}"))
            prepared = True
            for label, attr in zip(LABELS, ("many", "few")):
                plan = _explain_execute(conn, [getattr(pick, attr) for pick in picks])
                variants.append({"label": label, "plan": plan})
    finally:
        if prepared:
            conn.execute(text(f"DEALLOCATE {PREPARED_NAME}"))
    return VariantsOutcome(variants=variants)


def q_error(estimated, actual) -> float | None:
    """max(est/real, real/est), la misma metrica de AP-08. Ambos valores con
    piso 1: Postgres nunca estima menos de 1 fila y una query que no devuelve
    nada no tiene un error infinito. None si falta alguno de los dos."""
    if estimated is None or actual is None:
        return None
    try:
        estimated = max(float(estimated), 1.0)
        actual = max(float(actual), 1.0)
    except (TypeError, ValueError):
        return None
    return max(estimated / actual, actual / estimated)


def select_plan(generic_rows, variant_rows: list, actual_rows) -> int | None:
    """Posicion de la variante a enviar, o None para el generico. Gana el menor
    q-error contra las filas reales por ejecucion. En empate, o sin filas
    reales con que comparar, queda el generico: una variante solo lo reemplaza
    si estima estrictamente mejor."""
    best_error = q_error(generic_rows, actual_rows)
    if best_error is None:
        return None
    best = None
    for index, rows in enumerate(variant_rows):
        error = q_error(rows, actual_rows)
        if error is not None and error < best_error:
            best, best_error = index, error
    return best
