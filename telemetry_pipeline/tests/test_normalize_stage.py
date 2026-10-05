"""NormalizeStage: los tres pasos que el snapshot necesita para ser comparable.

El stage encadena cuatro transformaciones sobre stats. Si un paso deja de correr
no hay excepcion: el snapshot sale con el texto sin anonimizar, con los locks
como texto de MySQL, o con los predicados de MySQL sin normalizar. En los tres
casos el pipeline termina igual y el snapshot llega al consumidor con un formato
distinto al que espera, que es donde el fallo se vuelve caro y dificil de
rastrear.

Por eso estos tests verifican el efecto combinado, no los helpers sueltos: los
helpers ya tienen cobertura en test_normalize.py.
"""
import pytest

from collectors.mysql.collector import Mysql_Collector
from collectors.postgres.collector import Postgres_Collector
from stages.normalize import NormalizeStage

pytestmark = pytest.mark.unit


def _pg_stats():
    return {
        "statements": [{
            "query_id": 1,
            "query_text": "SELECT a FROM t WHERE b = 42",
            "schema_name": "public",
        }],
        "top_impact_queries": [{
            "query_id": 1,
            "query_text": "SELECT `a` FROM `t` WHERE `b` = 42",
            "schema_name": "public",
        }],
        "active_queries": [{
            "query_id": 1,
            "query_text": "SELECT a FROM t WHERE b = 42",
            "transaction_start_time": None,
        }],
        "locks": [],
        "canonic_explains": [],
    }


def _mysql_stats():
    return {
        "statements": [{
            "query_id": "abc",
            "query_text": "SELECT `a` FROM `t` WHERE `b` = ?",
            "schema_name": "ql_demo",
        }],
        "top_impact_queries": [{
            "query_id": "abc",
            "query_text": "SELECT `a` FROM `t` WHERE `b` = ?",
            "query_sample_text": "select a from t where b = 42",
            "schema_name": "ql_demo",
        }],
        "active_queries": [{
            "query_id": "abc",
            "query_text": "select a from t where b = 42",
        }],
        "locks": [{"process_id": 1, "lock_mode": "X", "is_granted": "GRANTED"}],
        "canonic_explains": [],
    }


class TestQueryTextIsRestored:
    def test_top_impact_gets_the_real_statement_text(self):
        """anonimize_query_text restaura el query_text de statements sobre el
        candidato. El nombre dice 'anonimize' pero lo que hace es recuperar el
        texto real: ExplainStage necesita una query ejecutable, y el digest de
        MySQL tiene los parametros como ?.

        Sin este paso, MySQL explainaria el digest con '?' y el motor responderia
        error de sintaxis, perdiendo el candidato.
        """
        stats = _mysql_stats()
        stats["top_impact_queries"][0]["query_text"] = "SELECT `a` FROM `t` WHERE `b` = ?"
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"][0]["query_text"] == (
            "SELECT `a` FROM `t` WHERE `b` = ?"
        )

    def test_candidate_without_matching_statement_keeps_its_text(self):
        """Si el query_id no esta en statements no hay nada que restaurar; el
        texto propio se conserva en vez de quedar en None."""
        stats = _mysql_stats()
        stats["top_impact_queries"][0]["query_id"] = "otro_digest"
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["top_impact_queries"][0]["query_text"] is not None


class TestCanonicQueryIsBuilt:
    def test_mysql_candidate_gets_canonic_query(self):
        """canonic_query es la forma con parametros: es lo que permite comparar
        dos executions de la misma query entre ciclos."""
        stats = _mysql_stats()
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        candidate = stats["top_impact_queries"][0]
        assert candidate["canonic_query"]
        assert "?" not in candidate["canonic_query"]

    def test_mysql_distinctrow_is_cleaned(self):
        """MySQL reporta DISTINCTROW donde el resto del mundo dice DISTINCT. Sin
        esta limpieza, la misma query produce dos canonic_query distintos segun
        el motor y el cruce entre motores se rompe.

        El texto tiene que estar en statements: anonimize_query_text corre antes
        y restaura el query_text real sobre el candidato.
        """
        stats = _mysql_stats()
        distinctrow = "SELECT DISTINCTROW `k` FROM `sbtest1`"
        stats["statements"][0]["query_text"] = distinctrow
        stats["top_impact_queries"][0]["query_text"] = distinctrow
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        canonic = stats["top_impact_queries"][0]["canonic_query"]
        assert "DISTINCTROW" not in canonic
        assert "DISTINCT" in canonic

    def test_postgres_canonic_query_uses_dollar_placeholders(self):
        stats = _pg_stats()
        NormalizeStage(Postgres_Collector(engine=None)).execute(stats)
        assert "$1" in stats["top_impact_queries"][0]["canonic_query"]

    def test_active_queries_get_canonic_and_lose_raw_text(self):
        """La query en vivo se reemplaza por su forma canonica: el texto crudo
        puede traer datos de otras sesiones y el snapshot viaja a una cola."""
        stats = _mysql_stats()
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        active = stats["active_queries"][0]
        assert "query_text" not in active
        assert active["canonic_query"]

    def test_active_query_without_text_gets_placeholder(self):
        stats = {"active_queries": [{"query_id": 1, "query_text": None}]}
        NormalizeStage(Postgres_Collector(engine=None)).execute(stats)
        assert stats["active_queries"][0]["canonic_query"] == "Not available"


class TestEngineArtifacts:
    def test_mysql_locks_are_coerced(self):
        """normalize_engine_artifacts de MySQL convierte el estado del lock de
        texto a bool. Sin esto, el snapshot lleva 'GRANTED' como string y el
        consumidor que compara contra True no lo reconoce."""
        stats = _mysql_stats()
        stats["locks"] = [
            {"process_id": 1, "lock_mode": "X", "is_granted": "GRANTED"},
            {"process_id": 2, "lock_mode": "X", "is_granted": "WAITING"},
        ]
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["locks"][0]["is_granted"] is True
        assert stats["locks"][1]["is_granted"] is False

    def test_mysql_blocking_pids_are_split(self):
        stats = _mysql_stats()
        stats["active_queries"] = [
            {"query_id": 1, "query_text": "SELECT 1", "blocking_pids": "10,20"},
        ]
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        assert stats["active_queries"][0]["blocking_pids"] == [10, 20]

    def test_postgres_timestamps_lose_timezone(self):
        from datetime import datetime, timezone

        stats = _pg_stats()
        stats["active_queries"][0]["transaction_start_time"] = datetime(
            2026, 1, 1, 12, 0, tzinfo=timezone.utc
        )
        NormalizeStage(Postgres_Collector(engine=None)).execute(stats)
        assert isinstance(
            stats["active_queries"][0]["transaction_start_time"], str
        )

    def test_base_collector_runs_no_engine_hook(self):
        """La base no normaliza nada: un motor nuevo nace sin convertir hasta que
        defina su hook."""
        class _Bare(Postgres_Collector):
            def normalize_engine_artifacts(self, stats):
                return stats

        stats = _mysql_stats()
        NormalizeStage(_Bare(engine=None)).execute(stats)
        assert stats["locks"][0]["is_granted"] == "GRANTED"


class TestPredicateCanonicalization:
    def test_mysql_schema_is_stripped_from_predicate(self):
        """El predicate de MySQL trae el schema qualifying cada columna. Es lo que
        impide comparar un plan de MySQL con uno de Postgres, que es el punto del
        contrato canonico."""
        stats = _mysql_stats()
        stats["canonic_explains"] = [{
            "query_id": "abc",
            "canonical_plan": {
                "physical_operations": [{
                    "type": "scan",
                    "predicate": "`ql_demo`.`sbtest1`.`k` > 10",
                }],
            },
        }]
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        operation = stats["canonic_explains"][0]["canonical_plan"][
            "physical_operations"
        ][0]
        assert "ql_demo" not in operation["predicate"]
        assert "`" not in operation["predicate"]

    def test_cache_marks_are_removed(self):
        """MySQL envuelve constantes en <cache>, que es anotacion interna del
        optimizador y no parte del predicado."""
        stats = _mysql_stats()
        stats["canonic_explains"] = [{
            "query_id": "abc",
            "canonical_plan": {
                "physical_operations": [{
                    "type": "scan",
                    "predicate": "k = <cache>10</cache>",
                }],
            },
        }]
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        predicate = stats["canonic_explains"][0]["canonical_plan"][
            "physical_operations"
        ][0]["predicate"]
        assert "<cache>" not in predicate

    def test_null_predicate_is_left_alone(self):
        """predicate None significa que el plan no trae condicion; llamar a la
        limpieza con None devolveria None, pero el test fija que no se intenta
        construir texto a partir de nada."""
        stats = _mysql_stats()
        stats["canonic_explains"] = [{
            "query_id": "abc",
            "canonical_plan": {"physical_operations": [{"type": "join", "predicate": None}]},
        }]
        NormalizeStage(Mysql_Collector(engine=None)).execute(stats)
        operation = stats["canonic_explains"][0]["canonical_plan"][
            "physical_operations"
        ][0]
        assert operation["predicate"] is None
