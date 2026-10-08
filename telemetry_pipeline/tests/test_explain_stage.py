import json
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


class _Tx:
    """begin() de SQLAlchemy: hace commit si el bloque sale limpio y rollback
    si lanza. Los fakes de conexion imitan ese contrato en miniatura."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, *exc):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


class _FakeConn:
    def __init__(self, rows=None):
        self.sent = []
        self.rollback_calls = 0
        self.commit_calls = 0
        self.close_calls = 0
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

    def begin(self):
        return _Tx(self)

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.close_calls += 1


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


def test_postgres_explain_error_rolls_back_the_short_transaction():
    conn = _FailingConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.rollback_calls == 1
    assert conn.commit_calls == 0
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
    """Sin schema EN la fila (schema_name ni database_name) el candidato no se
    explica: usar el USE de otro candidato anterior armaria el plan contra la
    base equivocada. Mejor perder el plan que explicarlo mal atribuido."""
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name=None)
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert [s for s in conn.sent if "EXPLAIN" in s] == []
    assert stats["canonic_explains"] == []


def test_mysql_falls_back_to_database_name_when_no_schema():
    """La atribucion de MySQL usa schema_name; cuando la fila no lo trae, la
    base del statement (database_name) sirve de contexto para el USE."""
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name=None)
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == "USE ventas"
    assert "EXPLAIN FORMAT=JSON insert into sbtest1" in conn.sent[1]


def test_mysql_canonical_plan_carries_the_real_explain_content():
    """El canonical_plan debe reflejar el JSON que devolvio el motor, no una
    constante (mutation "mysql devuelve plan fijo"): si ExplainStage hardcodeara
    el plan, la relation 'sbtest1' de la muestra nunca llegaria al snapshot."""
    conn = _FakeConn(rows=[{"EXPLAIN": json.dumps({
        "query_block": {
            "cost_info": {"query_cost": "42.0"},
            "table": {"table_name": "sbtest1", "access_type": "ALL"},
        }
    })}])
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["physical_operations"][0]["relation"] == "sbtest1"
    assert plan["logical_shape"]["scans"] == 1
    assert plan["estimates"]["total_cost"] == 42.0


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


def test_postgres_commits_the_short_transaction_per_candidate():
    """Cada candidato abre su propia transaccion corta (M-12): un exit limpio la
    commitea. Sin el commit, el SET LOCAL/EXPLAIN quedarian en la transaccion
    larga del ciclo y las lecturas del servidor quedarian abiertas hasta el fin."""
    conn = _FakeConn()
    stats = _stats()
    stats["top_impact_queries"].append(
        {
            "query_id": 8,
            "query_text": "SELECT * FROM t2 WHERE id = $1",
            "ready_for_explain": True,
            "schema_name": "public",
        }
    )
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.commit_calls == 2


def test_postgres_explain_error_rolls_back_the_short_transaction():
    conn = _FailingConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.rollback_calls == 1
    assert conn.commit_calls == 0
    assert stats["canonic_explains"] == []


class _FakeForeignEngine:
    def __init__(self, url, tracker=None):
        self.url = url
        self.connect_calls = 0
        self.disposed = False
        self.created_conns = []
        if tracker is not None:
            tracker.append(self)

    def connect(self):
        self.connect_calls += 1
        conn = _FakeConn()
        self.created_conns.append(conn)
        return conn

    def dispose(self):
        self.disposed = True


class _TrackerCollector(_FakeCollector):
    def __init__(self, tracker=None):
        self.tracker = tracker

    @property
    def engine(self):
        return PG_ENGINE

    @property
    def source_dialect(self):
        return "postgres"


def _tracker_stage(tracker=None):
    stage = ExplainStage(_TrackerCollector(tracker))
    stage._foreign_engine_factory = lambda url: _FakeForeignEngine(url, tracker)
    return stage


def test_postgres_foreign_database_routes_to_dedicated_connection():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    created = []
    stage = _tracker_stage(created)
    stage.execute(stats, conn)
    assert conn.sent == []
    assert stats["canonic_explains"][0]["query_id"] == 1
    assert len(created) == 1
    assert created[0].url.database == "ventas"


def test_postgres_same_database_uses_main_connection():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ql_demo"
    stage = _tracker_stage()
    stage.execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO public"
    assert stage._foreign_engines == {}


def test_postgres_foreign_engine_is_reused_for_same_database():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    stats["top_impact_queries"].append(
        {
            "query_id": 5,
            "query_text": "SELECT * FROM sbtest2 WHERE id = $1",
            "ready_for_explain": True,
            "schema_name": "public",
            "database_name": "ventas",
        }
    )
    created = []
    stage = _tracker_stage(created)
    stage.execute(stats, conn)
    assert len(created) == 1
    assert created[0].connect_calls == 2


def test_postgres_foreign_engines_are_disposed_after_execute():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    created = []
    stage = _tracker_stage(created)
    stage.execute(stats, conn)
    assert all(engine.disposed for engine in created)
    assert stage._foreign_engines == {}


def test_postgres_foreign_connection_is_closed_after_execute():
    """N-2: el `with active.begin()` cierra la transaccion, no la conexion.
    La conexion extranjera (creada con engine.connect() en _foreign_connection)
    tiene que cerrarse explicitamente; sin esto queda enganchada hasta que el
    GC la finalize. La compartida del ciclo NO se toca: la cierra el orchestrator.
    """
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    created = []
    stage = _tracker_stage(created)
    stage.execute(stats, conn)
    assert created[0].created_conns[0].close_calls == 1
    assert conn.close_calls == 0


def test_postgres_shared_connection_is_never_closed_by_the_stage():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ql_demo"
    stage = _tracker_stage()
    stage.execute(stats, conn)
    assert conn.close_calls == 0


def test_postgres_foreign_connection_is_closed_even_when_explain_fails():
    class _FailingForeignEngine(_FakeForeignEngine):
        def connect(self):
            self.connect_calls += 1
            conn = _FailingConn()
            self.created_conns.append(conn)
            return conn

    conn = _FakeConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    created = []
    stage = ExplainStage(_TrackerCollector(None))
    stage._foreign_engine_factory = lambda url: _FailingForeignEngine(url, created)
    stage.execute(stats, conn)
    assert created[0].created_conns[0].close_calls == 1
    assert conn.close_calls == 0


def test_mysql_never_opens_foreign_connection():
    """El ruteo por base es solo de Postgres: en MySQL una conexion cambia de
    base con USE, asi que el mismo conn del ciclo sirve para todo. Si alguien
    reutilizara _foreign_connection para mysql, abriria conexiones de mas."""
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    stats["top_impact_queries"][0]["database_name"] = "ventas"
    stage = ExplainStage(_FakeMysqlCollector())
    stage.execute(stats, conn)
    assert conn.sent[0] == "USE ql_demo"
    assert stage._foreign_engines == {}
