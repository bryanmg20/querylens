from models.stats import Stats
# Solo lectura: el rol monitor tiene SELECT y nada mas (PrimerInforme, agentless
# no intrusivo). EXPLAIN de DML, de CTE que escriben o de SELECT con bloqueo
# exige privilegios de escritura, asi que esos candidatos van a non_explainable.
# La regla vive en sql_text (la comparte ExplainStage).
from stages.sql_text import EXPLAINABLE_COMMANDS, is_explainable_command  # noqa: F401


def select_high_impact_time_statements(stats: Stats):
    stats['high_impact_statements'] = (stats.get('statements') or [])[:10]
        

def select_unstable_statements(stats: Stats):
    stats['unstable_statements'] = [stmd for stmd in (stats.get('statements') or []) if stmd.get('coeff_of_variation') is not None and stmd['coeff_of_variation'] > 2 and stmd.get('mean_time_ms', 0) > 10]


def select_disk_spill_indicator(stats: Stats):
    stats['disk_spill_statements'] = [stmd for stmd in (stats.get('statements') or []) if stmd.get('disk_spill_indicator', 0) > 0]
           

def select_candidates_to_explain(stats: Stats, dialect: str = "postgres"):
    candidates = {}
    skipped_candidates = {}

    statement_groups = [
        ("time_high_impact", "high_impact_statements"),
        ("unstable", "unstable_statements"),
        ("disk_spill", "disk_spill_statements"),
    ]

    for reason, stats_key in statement_groups:
        statements = stats.get(stats_key) or []

        for statement in statements:
            query_id = statement.get("query_id")
            query_text = statement.get("query_text")

            if query_id is None or not query_text:
                continue

            target = candidates

            if not is_explainable_command(query_text, dialect):
                target = skipped_candidates

            if query_id not in target:
                target[query_id] = {
                    **statement,
                    "selected_by": [],
                }

            selected_by = target[query_id]["selected_by"]

            if reason not in selected_by:
                selected_by.append(reason)

    stats["top_impact_queries"] = list(candidates.values())
    stats["non_explainable_candidates"] = list(
        skipped_candidates.values()
    )

    stats.pop("high_impact_statements", None)
    stats.pop("unstable_statements", None)
    stats.pop("disk_spill_statements", None)
     

def init_ready_for_explain(stats: Stats):
    readys = {}

    for candidate in stats.get("top_impact_queries", []):
        query_id = candidate.get('query_id')
        if query_id is not None:
            readys[query_id] = {**candidate, "ready_for_explain": False}

    stats["top_impact_queries"] = list(readys.values())