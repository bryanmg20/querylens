"""Tests contra los contenedores reales del sandbox (ql_postgres, ql_mysql, querylens_db).

Se saltan solos si los motores no estan levantados, para que la suite siga
corriendo en una maquina sin Docker. Requieren `docker compose up` en ql_sandbox/
y en la raiz del repo.

    docker compose -f ql_sandbox/docker-compose.yml up -d
    docker compose up -d
"""
import uuid

import pytest
from sqlalchemy import text

from collectors.postgres.collector import Postgres_Collector
from config.connections import (
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
)
from enqueue import send_to_queue
from models.snapshot import SnapshotPayload
from orchestrator import Orchestrator
from stages.collect import CollectStage

pytestmark = pytest.mark.integration


def _engine_or_skip(factory):
    try:
        engine = factory()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        pytest.skip(f"motor no disponible: {type(exc).__name__}: {str(exc)[:120]}")


@pytest.fixture(scope="module")
def pg():
    return _engine_or_skip(get_connection_postgres)


@pytest.fixture(scope="module")
def my():
    return _engine_or_skip(get_connection_mysql)


@pytest.fixture(scope="module")
def ql():
    return _engine_or_skip(get_connection_querylens_db)


# ---------- PostgreSQL: capa de recoleccion ----------


def test_collect_returns_every_declared_section(pg):
    collector = Postgres_Collector(engine=pg)
    with pg.connect() as conn:
        stats = CollectStage(collector).execute({}, conn)
    assert set(stats) == set(collector.queries)
    for key, rows in stats.items():
        assert rows is not None, f"{key} fallo en el motor real"
    assert stats["columns"], "information_schema.columns no deberia venir vacio"
    assert stats["stats_reset_timestamp"]


def test_statements_exclude_the_monitoring_user_itself(pg):
    collector = Postgres_Collector(engine=pg)
    with pg.connect() as conn:
        stats = CollectStage(collector).execute({}, conn)
    assert isinstance(stats["statements"], list)
    usernames = {s.get("username") for s in stats["statements"] if "username" in s}
    assert "querylens_monitor" not in usernames


def test_schema_resolver_maps_the_demo_user_to_a_real_schema(pg):
    collector = Postgres_Collector(engine=pg)
    with pg.connect() as conn:
        stats = CollectStage(collector).execute({}, conn)
    rows = stats["schema_resolver"]
    assert rows, "el sandbox debe resolver al menos un schema"
    assert all(r["resolved_schema"] for r in rows)
    with pg.connect() as conn:
        for schema in {r["resolved_schema"] for r in rows}:
            exists = conn.execute(text(
                "SELECT count(*) FROM information_schema.schemata WHERE schema_name = :s"
            ), {"s": schema}).scalar()
            assert exists == 1, f"{schema} no existe en el motor"


def test_generic_plan_explain_is_supported_by_the_engine(pg):
    collector = Postgres_Collector(engine=pg)
    with pg.connect() as conn:
        rows = conn.execute(text(collector.queries["statements"])).mappings().first()
    if rows is None:
        pytest.skip("pg_stat_statements vacio: corre la bateria primero")
    with pg.connect() as conn:
        result = conn.execute(text(
            f"EXPLAIN (GENERIC_PLAN, FORMAT JSON) {rows['query_text']}"
        )).mappings().first()
    assert result is not None
    assert "QUERY PLAN" in result


# ---------- PostgreSQL: pipeline completo ----------


def test_full_pipeline_produces_a_valid_snapshot(pg):
    collector = Postgres_Collector(engine=pg)
    stats = Orchestrator(collector).run_pipeline()
    payload = SnapshotPayload.from_snapshot(stats)
    assert payload.db_id == "querylens-db-01"
    assert isinstance(payload.statements, list)
    assert isinstance(payload.canonic_explains, list)


def test_full_pipeline_never_leaks_transient_keys(pg):
    collector = Postgres_Collector(engine=pg)
    stats = Orchestrator(collector).run_pipeline()
    for transient in (
        "high_impact_statements",
        "unstable_statements",
        "disk_spill_statements",
        "query_explain",
        "schema_resolver",
    ):
        assert transient not in stats


def test_pipeline_reads_real_statements_from_the_motor(pg):
    collector = Postgres_Collector(engine=pg)
    stats = Orchestrator(collector).run_pipeline()
    assert stats["statements"] is not None, "el sandbox deberia tener statements"
    assert stats["statements"], "corre la bateria para poblar pg_stat_statements"


# ---------- MySQL ----------


def test_mysql_statements_expose_query_sample_text(my):
    from collectors.mysql.queries import STATEMENTS_QUERY

    with my.connect() as conn:
        rows = list(conn.execute(text(STATEMENTS_QUERY)).mappings())
    assert rows, "performance_schema deberia tener digests tras la bateria"
    assert all(r["query_sample_text"] for r in rows), (
        "sin QUERY_SAMPLE_TEXT el motor no puede explicar nada"
    )


def test_mysql_statements_exclude_internals(my):
    from collectors.mysql.queries import STATEMENTS_QUERY

    with my.connect() as conn:
        rows = list(conn.execute(text(STATEMENTS_QUERY)).mappings())
    prefixes = tuple((r["query_text"] or "").strip().upper()[:8] for r in rows)
    assert not any(p.startswith("SET @@") for p in prefixes)
    assert not any("PERFORMANCE_SCHEMA" in p for p in prefixes)


def test_mysql_explain_requires_the_use_statement_first(my):
    """El engine no trae base por defecto: sin USE el EXPLAIN falla con 1046."""
    with my.connect() as conn:
        with pytest.raises(Exception) as no_use:
            conn.execute(text("EXPLAIN FORMAT=JSON SELECT c FROM sbtest1 WHERE id = 5"))
        assert "1046" in str(no_use.value)

        conn.execute(text("USE ql_demo"))
        result = conn.execute(
            text("EXPLAIN FORMAT=JSON SELECT c FROM sbtest1 WHERE id = 5")
        ).mappings().first()
    assert result is not None
    assert "query_block" in result["EXPLAIN"]


def test_mysql_full_pipeline_produces_a_valid_snapshot(my):
    from collectors.mysql.collector import Mysql_Collector

    collector = Mysql_Collector(engine=my)
    stats = Orchestrator(collector).run_pipeline()
    payload = SnapshotPayload.from_snapshot(stats)
    assert payload.db_id == "querylens-db-01"
    assert isinstance(payload.statements, list)


# ---------- PGMQ ----------


def test_enqueue_round_trip_on_a_throwaway_queue(ql):
    queue = f"test_enqueue_{uuid.uuid4().hex[:8]}"
    with ql.begin() as conn:
        conn.execute(text("SELECT pgmq.create(:q)"), {"q": queue})
    try:
        msg_id = send_to_queue('{"db_id": "integration-test"}', ql, queue_name=queue)
        assert msg_id is not None
        with ql.connect() as conn:
            row = conn.execute(
                text(
                    f'SELECT message->>\'db_id\' AS db_id FROM pgmq."q_{queue}" WHERE msg_id = :m'
                ),
                {"m": msg_id},
            ).mappings().first()
        assert row["db_id"] == "integration-test"
    finally:
        with ql.begin() as conn:
            conn.execute(text("SELECT pgmq.drop_queue(:q, true)"), {"q": queue})


def test_analyze_job_queue_exists(ql):
    with ql.connect() as conn:
        exists = conn.execute(text(
            "SELECT count(*) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'pgmq' AND c.relname = 'q_analyze_job'"
        )).scalar()
    assert exists == 1, "init.sql deberia haber creado la cola analyze_job"
