from typing import TypedDict


class Stats(TypedDict, total=False):
    db_id: str
    statements: list[dict] | None
    indexes: list[dict] | None
    tables: list[dict] | None
    locks: list[dict] | None
    active_queries: list[dict] | None
    stats_reset_timestamp: list[dict] | None
    columns: list[dict] | None
    schema_resolver: list[dict] | None
    high_impact_statements: list[dict]
    unstable_statements: list[dict]
    disk_spill_statements: list[dict]
    top_impact_queries: list[dict]
    non_explainable_candidates: list[dict]
    query_explain: list[dict]
    canonic_explains: list[dict]