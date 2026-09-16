SNAPSHOT_SCHEMA = (
    "db_id",
    "snapshot_ts",
    "query_id",
    "query_key",
    "process_id",
    "canonic_explains",
    "locks",
    "active_queries",
    "statements",
    "tables",
    "indexes",
)


def snapshot_contract():
    """Contrato de identidad del snapshot (REFACTOR.md [A]).

    NO es un schema de emisión: no son claves del JSON de la cola. Son los
    campos que el analizador resuelve POR REGISTRO dentro de cada seccion del
    payload, p. ej. `query_id` esta dentro de cada statement/top_impact_queries/
    canonic_explains (hay uno por fila, no es una clave al lado de `indexes`).
    build_snapshot no filtra ni valida: devuelve los keys existentes tal cual.
    """
    return SNAPSHOT_SCHEMA


def build_snapshot(stats, collector):
    collector.stats = stats
    return stats