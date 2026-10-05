import logging

import pytest
from sqlalchemy import create_engine

from stages.explain import ExplainStage

pytestmark = pytest.mark.unit

PG_ENGINE = create_engine("postgresql+psycopg2://ql_user:ql_pass@localhost:5432/ql_demo")
MY_ENGINE = create_engine("mysql+pymysql://ql_demo:ql_pass@localhost:3307/ql_demo")


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, rows=None):
        self.sent = []
        self.rollback_calls = 0
        # `rows=[]` tiene que significar "el motor no devolvio nada", no
        # "usa el default": con `rows or [...]` una lista vacia es falsy y
        # justamente el caso que el test de plan vacio necesita provocar quedaba
        # inalcanzable, con una rama de produccion sin cubrir.
        self.rows = [
            {"QUERY PLAN": '[{"Plan": {"Node Type": "Seq Scan", "Total Cost": 1.0}}]'},
        ] if rows is None else list(rows)

    def execute(self, stmt):
        self.sent.append(str(stmt))
        return _FakeResult(self.rows)

    def rollback(self):
        self.rollback_calls += 1


class _FakeCollector:
    source_dialect = "postgres"
    engine = PG_ENGINE


def _stats(schema_name="public"):
    return {
        "top_impact_queries": [
            {
                "query_id": 1,
                "query_text": "SELECT * FROM sbtest1 WHERE id = $1",
                "ready_for_explain": True,
                "schema_name": schema_name,
            },
        ],
    }


def test_postgres_sets_search_path_before_explain():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO public"
    assert "EXPLAIN (GENERIC_PLAN, FORMAT JSON) SELECT * FROM sbtest1" in conn.sent[1]
    assert stats["canonic_explains"][0]["query_id"] == 1


def test_postgres_emits_generic_plan_not_plain_explain():
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert "GENERIC_PLAN" in conn.sent[1]
    assert "EXPLAIN (FORMAT JSON)" not in conn.sent[1]


def test_postgres_keeps_placeholders_untouched():
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert "id = $1" in conn.sent[1]


def test_postgres_quotes_schema_identifier():
    conn = _FakeConn()
    stats = _stats(schema_name="app stats")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == 'SET LOCAL search_path TO "app stats"'


def test_postgres_resets_search_path_when_no_schema():
    conn = _FakeConn()
    stats = _stats(schema_name=None)
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO DEFAULT"


def test_postgres_records_generic_explain_source():
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert stats["canonic_explains"][0]["explain_source"] == "generic"


def test_postgres_skips_not_ready_candidates():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": 2,
                "query_text": "SELECT * FROM sbtest1",
                "ready_for_explain": False,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent == []
    assert stats["canonic_explains"] == []


def test_postgres_skips_candidates_without_query_id():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": None,
                "query_text": "SELECT 1",
                "ready_for_explain": True,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent == []


def test_postgres_skips_multi_statement_query():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": 3,
                "query_text": "SELECT 1; SELECT 2",
                "ready_for_explain": True,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert [s for s in conn.sent if "EXPLAIN" in s] == []


class _FailingConn(_FakeConn):
    def execute(self, stmt):
        self.sent.append(str(stmt))
        if "EXPLAIN" in str(stmt):
            raise RuntimeError("explain boom")
        return _FakeResult(self.rows)


def test_postgres_explain_error_rolls_back_and_skips():
    conn = _FailingConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.rollback_calls == 1
    assert stats["canonic_explains"] == []


def test_postgres_continues_after_one_failure():
    conn = _FailingConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"].append(
        {
            "query_id": 4,
            "query_text": "SELECT * FROM sbtest2 WHERE id = $1",
            "ready_for_explain": True,
            "schema_name": "public",
        }
    )
    ExplainStage(_FakeCollector()).execute(stats, conn)
    explains = [s for s in conn.sent if "EXPLAIN" in s]
    assert len(explains) == 2


class _FakeMysqlCollector:
    source_dialect = "mysql"
    engine = MY_ENGINE


def _mysql_stats(schema_name="ql_demo"):
    return {
        "top_impact_queries": [
            {
                "query_id": 7,
                "query_text": "INSERT INTO sbtest1 (id, k) VALUES (? , ?)",
                "query_sample_text": "insert into sbtest1 (id, k) values (5, 'x')",
                "ready_for_explain": True,
                "schema_name": schema_name,
            },
        ],
    }


def test_mysql_uses_sample_text_not_digest():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert "insert into sbtest1 (id, k) values (5, 'x')" in conn.sent[1]
    assert "VALUES (? , ?)" not in conn.sent[1]


def test_mysql_uses_schema_before_explain():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name="ql_demo")
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == "USE ql_demo"
    assert "EXPLAIN FORMAT=JSON insert into sbtest1" in conn.sent[1]
    assert stats["canonic_explains"][0]["query_id"] == 7


def test_mysql_records_sample_explain_source():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert stats["canonic_explains"][0]["explain_source"] == "sample"


def test_mysql_skips_use_when_no_schema():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name=None)
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert "EXPLAIN FORMAT=JSON insert into sbtest1" in conn.sent[0]


def test_mysql_truncated_sample_is_not_explainable():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    stats["top_impact_queries"][0]["ready_for_explain"] = False
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent == []
    assert stats["canonic_explains"] == []


def test_mysql_truncated_sample_rolls_back_without_aborting_cycle():
    class _TruncatedConn(_FakeConn):
        def execute(self, stmt):
            self.sent.append(str(stmt))
            if "EXPLAIN FORMAT=JSON" in str(stmt):
                raise RuntimeError("You have an error in your SQL syntax")
            return _FakeResult(self.rows)

    conn = _TruncatedConn()
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.rollback_calls == 1
    assert stats["canonic_explains"] == []


def test_mysql_no_plan_row_is_logged_and_skipped(caplog):
    """MySQL puede devolver un result set vacio en vez de un error.

    El caso es distinto del de una excepcion: no hay traceback que lo delate, asi
    que sin el log un plan perdido seria silencioso. El assert del log es lo que
    distingue esta rama de la de error.
    """
    conn = _FakeConn(rows=[])
    stats = _mysql_stats()
    with caplog.at_level(logging.ERROR):
        ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert stats["canonic_explains"] == []
    assert any(
        "returned no plan row" in r.message and f"query_id={stats['top_impact_queries'][0]['query_id']}" in r.message
        for r in caplog.records
    ), f"la rama de plan vacio no se disparo: {[r.message for r in caplog.records]}"


def test_mysql_quotes_schema_identifier():
    """El schema sale de pg_roles.rolconfig via schema_resolver, no de una
    constante del codigo, asi que puede traer comillas o espacios. Sin quoting
    el USE falla y el EXPLAIN del candidato cae al schema equivocado.

    ql_demo no ejercita esto: es un identificador simple que el servidor acepta
    sin comillas, por eso hace falta un caso que las necesite.
    """
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name="ql demo")
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == "USE `ql demo`"


@pytest.mark.parametrize(
    "schema_name,expected",
    [
        # El preparer de PyMySQL solo entrecomilla cuando hace falta: un
        # identificador simple se emite crudo, uno con espacios o caracteres
        # especiales entrecomillado, y el case se preserva porque en MySQL
        # Mixed_Case y mixed_case son bases distintas.
        ("ql_demo", "USE ql_demo"),
        ("otro_schema", "USE otro_schema"),
        ("app-stats", "USE `app-stats`"),
        ("ql demo", "USE `ql demo`"),
        ("Mixed_Case", "USE `Mixed_Case`"),
    ],
)
def test_mysql_use_identifiers_are_safely_quoted(schema_name, expected):
    """Un schema con espacios o guiones sin entrecomillar hace fallar el USE, y
    con el falla el EXPLAIN del candidato."""
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name=schema_name)
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == expected


def test_mysql_schema_from_resolver_drives_the_use():
    """El USE sale de schema_name, que es lo que llena schema_resolver. Si el
    stage lo derivara de otra parte, el candidato se explicaria en otra base."""
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name="otro_schema")
    stats["top_impact_queries"][0]["query_text"] = "SELECT 1 FROM ql_demo.t"
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == "USE otro_schema"


def test_postgres_search_path_is_local_not_session():
    """SET LOCAL confines el cambio a la transaccion. Un SET (sin LOCAL) dejaria
    el search_path alterado para el resto de la sesion del pool, y el siguiente
    candidato se explicaría en el schema del anterior."""
    conn = _FakeConn()
    stats = _stats(schema_name="otro")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO otro"
    assert "SET LOCAL" in conn.sent[0]


def test_explain_text_is_not_interpolated_with_placeholders():
    """Postgres deja los placeholders como estan; el EXPLAIN necesita el texto
    parametrizado para que el plan sea generico."""
    conn = _FakeConn()
    stats = _stats()
    stats["top_impact_queries"][0]["query_text"] = "SELECT * FROM t WHERE a = $1 AND b = $2"
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert "$1" in conn.sent[1] and "$2" in conn.sent[1]


def test_mysql_never_sends_postgres_placeholders_to_explain():
    """MySQL usa la muestra, que trae literales. Mandarle el digest con ? lo
    hace fallar con error de sintaxis y se pierde el candidato."""
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    stats["top_impact_queries"][0]["query_text"] = "SELECT * FROM sbtest1 WHERE id = ?"
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    explain_sql = [s for s in conn.sent if "EXPLAIN" in s][0]
    assert "?" not in explain_sql


def test_query_explain_is_dropped_from_snapshot():
    """query_explain es la forma cruda del motor; canonic_explains es la
    canonica. Las dos viajando duplicaria el payload y expondría JSON crudo del
    motor al consumidor."""
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert "query_explain" not in stats
    assert stats["canonic_explains"]
