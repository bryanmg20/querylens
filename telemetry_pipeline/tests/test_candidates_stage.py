"""CandidatesStage: el orden y la presencia de cada paso.

El stage encadena seis operaciones sobre stats. Un paso que deja de ejecutarse
no lanza excepcion: los candidatos simplemente no traen ready_for_explain, o
no traen schema_name, y el snapshot sale incompleto sin que nada lo indique.

Por eso estos tests no solo afirman el resultado final, sino que el stage ejecuta
cada paso. Los selectores tienen cobertura propia en test_selectors.py; aqui se
verifica el cableado.
"""
import pytest

from collectors.base import DB_Engine_Collector
from collectors.mysql.collector import Mysql_Collector
from collectors.postgres.collector import Postgres_Collector
from stages import schema_resolver
from stages.candidates import CandidatesStage

pytestmark = pytest.mark.unit


class _BareCollector(DB_Engine_Collector):
    """La base no declara source_dialect, pero candidates.py lo consulta siempre.
    Este collector lo mas lo minimo para exercitar el stage con los hooks en su
    default, que es lo que se quiere verificar en varios de estos tests."""

    source_dialect = "postgres"


def _statement(query_id, query_text="SELECT 1", **extra):
    row = {
        "query_id": query_id,
        "query_text": query_text,
        "schema_name": None,
        "execution_count": 10,
        "rows_returned": 10,
        "total_time_ms": 1000.0,
        "mean_time_ms": 100.0,
        "coeff_of_variation": None,
        "disk_spill_indicator": 0,
    }
    row.update(extra)
    return row


def _stats(*statements):
    return {
        "statements": list(statements),
        "schema_resolver": [],
    }


class TestEveryStepRuns:
    def test_ready_for_explain_is_injected(self):
        """Si select_explain_ready no corre, los candidatos salen sin el campo y
        ExplainStage los descarta todos en silencio."""
        stats = _stats(_statement(1), _statement(2))
        CandidatesStage(_BareCollector()).execute(stats)
        for candidate in stats["top_impact_queries"]:
            assert "ready_for_explain" in candidate
            assert candidate["ready_for_explain"] is False

    def test_selector_sections_are_cleaned_up(self):
        """Los selectores intermedios no deben quedar en el snapshot: son
        artefactos internos y llegan al consumidor de PGMQ."""
        stats = _stats(_statement(1))
        CandidatesStage(_BareCollector()).execute(stats)
        for key in (
            "high_impact_statements",
            "unstable_statements",
            "disk_spill_statements",
        ):
            assert key not in stats

    def test_top_impact_is_populated(self):
        stats = _stats(_statement(1), _statement(2), _statement(3))
        CandidatesStage(_BareCollector()).execute(stats)
        assert len(stats["top_impact_queries"]) == 3

    def test_preprocess_runs_before_selection(self):
        """MySQL computa coeff_of_variation en preprocess_statements, y los
        selectores de inestabilidad lo leen despues. Si el preprocess no corriera,
        coeff_of_variation quedaria en None y la seleccion por variabilidad no
        tendria nada que mirar.

        calculate_stddev_coeff necesita count > 1 y max > mean, asi que el
        statement se arma con esos datos.
        """
        stats = _stats(
            _statement(1, coeff_of_variation=None, execution_count=20, max_time_ms=900.0,
                       mean_time_ms=100.0),
        )
        CandidatesStage(Mysql_Collector(engine=None)).execute(stats)
        candidate = stats["top_impact_queries"][0]
        assert candidate["coeff_of_variation"] is not None
        assert candidate["coeff_of_variation"] > 0

    def test_postgres_preprocess_is_identity(self):
        """Postgres ya trae coeff_of_variation de pg_stat_statements; su
        preprocess no debe tocarlo."""
        stats = _stats(_statement(1, coeff_of_variation=1.25))
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"][0]["coeff_of_variation"] == 1.25

    def test_selected_by_is_recorded(self):
        stats = _stats(_statement(1))
        CandidatesStage(_BareCollector()).execute(stats)
        assert stats["top_impact_queries"][0]["selected_by"] == ["time_high_impact"]


class TestMarkExplainable:
    def test_mysql_marks_when_sample_exists(self):
        """mark_explainable es lo unico que puede poner ready_for_explain=True.
        Para MySQL hace falta query_sample_text, porque el EXPLAIN usa esa."""
        stats = _stats(_statement(1))
        stats["statements"][0]["query_sample_text"] = "SELECT 1 FROM sbtest1"
        CandidatesStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is True

    def test_mysql_does_not_mark_without_sample(self):
        """Sin muestra no hay EXPLAIN posible; marcarlo seria enviar basura al
        motor."""
        stats = _stats(_statement(1))
        CandidatesStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False

    def test_postgres_marks_without_sample(self):
        """Postgres explica el query_text normalizado contra el catalogo, no
        necesita muestra: el criterio es distinto por motor a proposito."""
        stats = _stats(_statement(1))
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is True

    def test_base_collector_marks_nothing(self):
        """El default de la base no marca: un motor nuevo nace sin explicar hasta
        que defina su criterio."""
        stats = _stats(_statement(1))
        CandidatesStage(_BareCollector()).execute(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False


class TestSchemaResolverWiring:
    def test_postgres_resolves_schema(self):
        """El nombre del schema es lo que permite el search_path del EXPLAIN.
        Si el resolver no corre en Postgres, todos los explains caen al schema
        equivocado."""
        stats = _stats(_statement(1))
        stats["schema_resolver"] = [{"user_id": 10, "resolved_schema": "app"}]
        stats["statements"][0]["userid"] = 10
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert stats["statements"][0]["schema_name"] == "app"

    def test_mysql_does_not_resolve_schema(self):
        """MySQL no tiene search_path por usuario: el resolver es de Postgres.

        El collector de MySQL ni siquiera pide la seccion schema_resolver (no esta
        en su queries), asi que en ejecucion normal la fila no llega. Este test
        la inyecta a proposito para fijar que, aun si llegara, el stage no la
        aplica: asignar el schema resuelto de Postgres a un candidato MySQL lo
        mandaria al USE equivocado.
        """
        stats = _stats(_statement(1))
        stats["schema_resolver"] = [{"user_id": 10, "resolved_schema": "app"}]
        stats["statements"][0]["userid"] = 10
        CandidatesStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["statements"][0]["schema_name"] is None
        assert "userid" in stats["statements"][0]

    def test_mysql_never_queries_the_resolver(self):
        """La garantia real de que la fila no existe en MySQL: el collector no la
        pide. Sin esto, el test anterior dependeria de una inyeccion artificial."""
        assert "schema_resolver" not in Mysql_Collector(engine=None).queries
        assert "schema_resolver" in Postgres_Collector(engine=None).queries

    def test_userid_is_consumed(self):
        """userid es una clave de Postgres que no pertenece al contrato; si
        sobreviviera, el consumidor veria un campo que no espera."""
        stats = _stats(_statement(1))
        stats["schema_resolver"] = [{"user_id": 10, "resolved_schema": "app"}]
        stats["statements"][0]["userid"] = 10
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert "userid" not in stats["statements"][0]
        assert "userid" not in stats["top_impact_queries"][0]

    def test_resolver_rows_never_reach_snapshot(self):
        stats = _stats(_statement(1))
        stats["schema_resolver"] = [{"user_id": 10, "resolved_schema": "app"}]
        stats["statements"][0]["userid"] = 10
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert "schema_resolver" not in stats


class TestNonExplainableRouting:
    def test_explainable_command_lands_in_top_impact(self):
        stats = _stats(_statement(1, query_text="SELECT * FROM t"))
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert len(stats["top_impact_queries"]) == 1
        assert stats["non_explainable_candidates"] == []

    @pytest.mark.parametrize("query_text", ["SET x = 1", "SHOW TABLES", "COMMIT"])
    def test_non_explainable_command_is_routed_away(self, query_text):
        """Un SET o un SHOW no se pueden EXPLAINar. SiREWndieran al top impact,
        ExplainStage gastaria una consulta fallida por cada uno."""
        stats = _stats(_statement(1, query_text=query_text))
        CandidatesStage(Postgres_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"] == []
        assert len(stats["non_explainable_candidates"]) == 1


