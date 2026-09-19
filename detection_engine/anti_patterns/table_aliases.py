import sqlglot
from sqlglot import exp

from logger import get_logger

logger = get_logger(__name__)


def resolve_table_aliases(canonic_query: str | None, dialect: str = "postgres") -> dict[str, str]:
    # Mapea alias -> nombre real de tabla (canonic_query siempre viene en dialecto postgres)
    if not canonic_query:
        return {}

    try:
        ast = sqlglot.parse_one(canonic_query, read=dialect)
    except Exception as e:
        logger.warning(
            "resolve_table_aliases | no se pudo parsear canonic_query | error=%s | texto=%.200s",
            e, canonic_query,
        )
        return {}

    aliases: dict[str, str] = {}
    for table in ast.find_all(exp.Table):
        real_name = table.name
        if not real_name:
            continue
        aliases[real_name] = real_name
        if table.alias:
            aliases[table.alias] = real_name
    return aliases
