"""Conversiones de tipo del predicado, iguales en los dos motores.

Una conversion sobre la columna impide usar su indice, y los detectores la
reconocen por una sola regla: un cast sobre la columna en `predicate`. Los
motores no la muestran igual:

- MySQL no escribe la conversion implicita. Una columna de texto o fecha
  comparada con un numero (`code = 5`) se compara como numero: MySQL convierte
  la columna en cada fila, aunque el plan diga solo `(code = $1)`. Aca se
  escribe el cast que hace el motor: `(CAST(code AS DOUBLE PRECISION) = $1)`.
  El tipo del valor sale de la redaccion (`$n` numero, `'$n'` texto).
- Postgres escribe `(col)::text` al comparar cualquier columna varchar: varchar
  no tiene operadores propios y usa los de text, sin costo y con el indice
  disponible. No es una conversion y se quita. Con tipo desconocido tambien se
  quita (varchar es lo comun). Sobre char(n) se deja: ahi cambia el valor
  (quita los blancos) y el indice no sirve.

Corre al final de NormalizeStage, con los predicados ya redactados.
"""
import re

import sqlglot
from sqlglot import exp

from models.stats import Stats

# Tipos cuya comparacion contra un numero obliga a MySQL a convertir la columna
NON_NUMERIC_TYPES = {
    "character varying", "varchar", "character", "char", "text",
    "tinytext", "mediumtext", "longtext", "enum", "set", "uuid",
    "date", "time", "datetime", "timestamp",
    "timestamp without time zone", "timestamp with time zone",
    "year",
}
VARCHAR_TYPES = {"character varying", "varchar"}

_COMPARISON_TYPES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)

_PG_IDENT = r'(?:"(?:""|[^"])+"|[A-Za-z_]\w*)'
# (col)::text o (alias.col)::text, sin tocar ::text[] ni ::textfoo
_PG_TEXT_CAST_RE = re.compile(rf"\((?P<column>{_PG_IDENT}(?:\.{_PG_IDENT})?)\)::text(?![\w\[])")


def make_type_conversions_explicit(stats: Stats, dialect: str) -> Stats:
    types = _column_types(stats.get("columns") or [])
    candidates = {
        str(candidate["query_id"]): candidate
        for candidate in stats.get("top_impact_queries") or []
        if candidate.get("query_id") is not None
    }
    for explain in stats.get("canonic_explains") or []:
        candidate = candidates.get(str(explain.get("query_id"))) or {}
        schema_name = candidate.get("schema_name")
        aliases = _table_aliases(candidate.get("canonic_query"))
        plan = explain.get("canonical_plan") or {}
        for operation in plan.get("physical_operations") or []:
            predicate = operation.get("predicate")
            if operation.get("type") != "scan" or not predicate:
                continue
            relation = operation.get("relation")
            table = operation.get("table_name") or aliases.get(relation, relation)

            def type_of(qualifier, column_name, relation=relation, table=table):
                # sin calificar o con el alias del scan es de su tabla
                if not qualifier or qualifier == relation:
                    owner = table
                else:
                    owner = aliases.get(qualifier, qualifier)
                return _lookup_type(types, schema_name, owner, column_name)

            if dialect == "mysql":
                operation["predicate"] = _cast_mysql_implicit_conversions(predicate, type_of)
            else:
                operation["predicate"] = _drop_varchar_text_casts(predicate, type_of)
    return stats


def _column_types(columns) -> dict[tuple[str | None, str, str], str]:
    return {
        (column.get("schema_name"), column["table_name"], column["column_name"]):
            (column.get("data_type") or "").strip().lower()
        for column in columns
        if column.get("table_name") and column.get("column_name") and column.get("data_type")
    }


def _lookup_type(types, schema_name, table_name, column_name) -> str | None:
    # Clave exacta con schema; si no, por nombre solo cuando falta el schema de
    # algun lado y hay un unico candidato (mismo criterio que los detectores).
    if not table_name or not column_name:
        return None
    exact = types.get((schema_name, table_name, column_name))
    if exact is not None:
        return exact
    candidates = [
        data_type for (sn, tn, cn), data_type in types.items()
        if tn == table_name and cn == column_name and (sn is None or schema_name is None)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _table_aliases(canonic_query: str | None) -> dict[str, str]:
    # alias (o nombre) -> tabla real de cada tabla de la query. canonic_query
    # viene en dialecto postgres en los dos motores.
    if not canonic_query:
        return {}
    try:
        tree = sqlglot.parse_one(canonic_query, read="postgres")
    except Exception:
        return {}
    aliases = {}
    for table in tree.find_all(exp.Table):
        if table.name:
            aliases[table.alias_or_name] = table.name
    return aliases


def _drop_varchar_text_casts(predicate: str, type_of) -> str:
    def replace(match):
        parts = [part.strip('"') for part in match.group("column").split(".")]
        qualifier, column_name = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
        data_type = type_of(qualifier, column_name)
        if data_type is None or data_type in VARCHAR_TYPES:
            return match.group("column")
        return match.group(0)

    return _PG_TEXT_CAST_RE.sub(replace, predicate)


def _cast_mysql_implicit_conversions(predicate: str, type_of) -> str:
    try:
        tree = sqlglot.parse_one(predicate, read="postgres")
    except Exception:
        # el predicado que no se pudo parsear tampoco lo leen los detectores
        return predicate

    changed = False

    def cast_if_converted(column_node, values) -> None:
        nonlocal changed
        column = _unwrap_paren(column_node)
        if not isinstance(column, exp.Column):
            return
        data_type = type_of(column.table or None, column.name)
        if data_type in NON_NUMERIC_TYPES and any(_is_number(value) for value in values):
            column.replace(exp.Cast(this=column.copy(), to=exp.DataType.build("DOUBLE PRECISION")))
            changed = True

    for comparison in list(tree.find_all(_COMPARISON_TYPES)):
        left, right = comparison.this, comparison.expression
        if isinstance(_unwrap_paren(left), exp.Column):
            cast_if_converted(left, [right])
        elif isinstance(_unwrap_paren(right), exp.Column):
            cast_if_converted(right, [left])
    for node in list(tree.find_all(exp.In)):
        cast_if_converted(node.this, node.expressions)
    for node in list(tree.find_all(exp.Between)):
        cast_if_converted(node.this, [node.args.get("low"), node.args.get("high")])

    if not changed:
        return predicate
    return tree.sql(dialect="postgres", identify=False, comments=False).replace('"', "")


def _unwrap_paren(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _is_number(node) -> bool:
    # $n es un numero redactado; '$n' es texto. Un literal sin redactar (camino
    # de respaldo) tambien se reconoce.
    node = _unwrap_paren(node) if node is not None else None
    if isinstance(node, exp.Neg):
        node = _unwrap_paren(node.this)
    if isinstance(node, exp.Parameter):
        return True
    return isinstance(node, exp.Literal) and not node.is_string
