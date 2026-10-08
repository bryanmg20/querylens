"""select_candidates_to_explain: el ruteo entre explicables y no explicables.

Esta funcion decide que query llega a ExplainStage. Un error de ruteo no lanza
excepcion: simplemente manda al motor una query que no se puede explicar, o
deja fuera una que si, y el pipeline sigue. Los tests fijan los dos sentidos
porque los dos fallos son silenciosos.
"""
import pytest

from stages.selectors import select_candidates_to_explain

pytestmark = pytest.mark.unit


def _statement(query_id, query_text, **extra):
    row = {
        "query_id": query_id,
        "query_text": query_text,
        "schema_name": None,
        "execution_count": 10,
        "rows_returned": 10,
        "mean_time_ms": 100.0,
        "coeff_of_variation": None,
        "disk_spill_indicator": 0,
    }
    row.update(extra)
    return row


def _run(*statements):
    stats = {
        "statements": list(statements),
        "high_impact_statements": list(statements),
        "unstable_statements": [],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    return stats


@pytest.mark.parametrize(
    "query_text",
    [
        "SELECT * FROM t",
        "select * from t",
        "  SELECT * FROM t  ",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
    ],
)
def test_explainable_command_is_routed_to_top_impact(query_text):
    """El match es sobre el texto normalizado: un SELECT en minusculas o con
    sangria sigue siendo explicable, y de hecho es como llega el texto de
    pg_stat_statements.
    """
    stats = _run(_statement(1, query_text))
    assert len(stats["top_impact_queries"]) == 1
    assert stats["non_explainable_candidates"] == []


@pytest.mark.parametrize(
    "query_text",
    [
        "SET x = 1",
        "SHOW TABLES",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "CREATE TABLE t (a int)",
        "DROP TABLE t",
        "VACUUM t",
        "ANALYZE t",
        "LOCK TABLES t READ",
    ],
)
def test_non_explainable_command_is_routed_away(query_text):
    """Nada de esto se puede EXPLAINar. Mandarlo al motor gasta una consulta
    fallida por cada uno, y el error por query aparece como log, no como
    exception: el ciclo sigue y el snapshot sale igual.
    """
    stats = _run(_statement(1, query_text))
    assert stats["top_impact_queries"] == []
    assert len(stats["non_explainable_candidates"]) == 1


def test_command_match_anchors_at_the_start():
    """La comparacion es de prefijo, no de contenido: un SELECT anidado dentro de
    un DELETE no convierte al DELETE en explicable ni al revés. El DELETE se
    explica porque empieza por DELETE, que esta en la lista.
    """
    stats = _run(_statement(1, "DELETE FROM t WHERE a IN (SELECT id FROM u)"))
    assert len(stats["top_impact_queries"]) == 1
    assert stats["non_explainable_candidates"] == []


def test_lowercase_command_does_not_match_when_not_normalized():
    """El texto se normaliza a mayusculas antes de comparar, asi que SET en
    minusculas tambien se descarta. Si esa normalizacion se perdiera, un SET en
    minusculas llegaria al EXPLAIN."""
    stats = _run(_statement(1, "set x = 1"), _statement(2, "show tables"))
    assert stats["top_impact_queries"] == []
    assert len(stats["non_explainable_candidates"]) == 2


def test_candidate_without_query_text_is_dropped():
    """Sin query_text no hay nada que explicar, y mandarlo al motor produce un
    EXPLAIN de None."""
    stats = _run(_statement(1, None), _statement(2, "SELECT 1"))
    assert [c["query_id"] for c in stats["top_impact_queries"]] == [2]


def test_candidate_without_query_id_is_dropped():
    """Sin query_id no hay como correlacionar el explain con el statement."""
    stats = _run(_statement(None, "SELECT 1"))
    assert stats["top_impact_queries"] == []
    assert stats["non_explainable_candidates"] == []


def test_blank_query_text_lands_in_non_explainable():
    """Un texto de solo espacios es truthy en Python, asi que pasa el descarte de
    `not query_text` y termina en non_explainable_candidates. No se explica, que
    es lo que importa; que figure como 'candidato no explicable' en vez de
    desaparecer es una decision menor que se documenta para que no sorprenda.
    """
    stats = _run(_statement(1, "   "))
    assert stats["top_impact_queries"] == []
    assert len(stats["non_explainable_candidates"]) == 1


def test_same_query_across_reasons_keeps_all_reasons():
    """Una query pesada y a la vez inestable entra una sola vez, pero el
    consumidor necesita saber que cumplio mas de un criterio."""
    stats = {
        "statements": [],
        "high_impact_statements": [_statement(1, "SELECT 1")],
        "unstable_statements": [_statement(1, "SELECT 1")],
        "disk_spill_statements": [_statement(1, "SELECT 1")],
    }
    select_candidates_to_explain(stats)
    assert len(stats["top_impact_queries"]) == 1
    assert sorted(stats["top_impact_queries"][0]["selected_by"]) == [
        "disk_spill", "time_high_impact", "unstable",
    ]


def test_selected_by_has_no_duplicates():
    """selected_by alimenta un filtro en el consumidor; con duplicados contaria de
    mas sin que nada lo indique."""
    stats = {
        "statements": [],
        "high_impact_statements": [_statement(1, "SELECT 1"), _statement(1, "SELECT 1")],
        "unstable_statements": [_statement(1, "SELECT 1")],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    candidate = stats["top_impact_queries"][0]
    assert candidate["selected_by"].count("unstable") == 1


def test_duplicate_candidates_are_deduplicated():
    """El mismo query_id puede aparecer en varias listas. Sin dedupe, ExplainStage
    lo explica N veces y el snapshot trae N copias del mismo plan."""
    stats = {
        "statements": [],
        "high_impact_statements": [_statement(1, "SELECT 1")],
        "unstable_statements": [_statement(1, "SELECT 1")],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    assert len(stats["top_impact_queries"]) == 1


def test_query_id_never_appears_in_both_buckets_for_real_input():
    """Un query_id no puede estar en los dos buckets en ejecucion real: en ambos
    motores el query_id determina el texto (el par schema/digest en MySQL, el
    queryid de pg_stat_statements en Postgres), asi que la contradiccion
    exigiria dos textos distintos para el mismo identificador.

    El codigo deduplica dentro de cada bucket, no entre los dos, asi que con
    entrada contradictoria un id aparece en ambos. Este test documenta la
    invariante que hace que eso no ocurra, en vez de afirmar una deduplicacion
    cruzada que el codigo no tiene.
    """
    statements = [_statement(i, "SELECT 1") for i in range(1, 6)]
    stats = _run(*statements)
    top_ids = {c["query_id"] for c in stats["top_impact_queries"]}
    skipped_ids = {c["query_id"] for c in stats["non_explainable_candidates"]}
    assert not (top_ids & skipped_ids)


def test_digest_is_the_query_id_on_mysql(mysql_snapshot):
    """La garantia real, sobre datos del motor: el texto es funcion del
    query_id (schema/digest en MySQL), asi que un id no puede tener dos
    textos."""
    by_id = {}
    for statement in mysql_snapshot["statements"]:
        by_id.setdefault(statement["query_id"], set()).add(statement["query_text"])
    assert all(len(texts) == 1 for texts in by_id.values())


def test_order_follows_impact_ranking():
    """El orden de top_impact_queries viene del orden de las listas de entrada,
    que a su vez viene del ranking por tiempo. Invertirlo cambia que query se
    explica primero, que es lo que decide el corte."""
    statements = [_statement(i, f"SELECT {i}") for i in range(1, 4)]
    stats = {
        "statements": statements,
        "high_impact_statements": statements,
        "unstable_statements": [],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    assert [c["query_id"] for c in stats["top_impact_queries"]] == [1, 2, 3]


def test_intermediate_sections_are_removed():
    """Las tres listas son artefactos de la seleccion. Si sobreviven llegan al
    consumidor de PGMQ y duplican lo que ya esta en top_impact_queries."""
    stats = _run(_statement(1, "SELECT 1"))
    for key in (
        "high_impact_statements",
        "unstable_statements",
        "disk_spill_statements",
    ):
        assert key not in stats


def test_missing_reason_lists_are_tolerated():
    """Cada selector anterior puede no haber corrido o haber dejado la lista
    vacia. La funcion no debe romper."""
    stats = {
        "statements": [_statement(1, "SELECT 1")],
        "high_impact_statements": None,
        "unstable_statements": None,
        "disk_spill_statements": None,
    }
    select_candidates_to_explain(stats)
    assert stats["top_impact_queries"] == []


def test_candidate_keeps_the_full_statement_payload():
    """El candidato conserva los campos de medicion: son los que el consumidor
    necesita para puntuar el impacto. Un dict armado a mano los perderia."""
    stats = _run(_statement(
        1, "SELECT 1", total_time_ms=999.0, avg_rows_per_call=12.5
    ))
    candidate = stats["top_impact_queries"][0]
    assert candidate["total_time_ms"] == 999.0
    assert candidate["avg_rows_per_call"] == 12.5
