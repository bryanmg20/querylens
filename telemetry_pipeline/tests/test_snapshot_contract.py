import pytest

from models.snapshot import (
    ActiveQueryRow,
    CanonicExplain,
    CanonicalPlan,
    SnapshotPayload,
)

pytestmark = pytest.mark.contract


def test_mysql_golden_validates(mysql_snapshot):
    payload = SnapshotPayload.from_snapshot(mysql_snapshot)
    assert payload.db_id == mysql_snapshot["db_id"]


def test_postgres_golden_validates(postgres_snapshot):
    payload = SnapshotPayload.from_snapshot(postgres_snapshot)
    assert payload.db_id == postgres_snapshot["db_id"]


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_required_sections_present(snapshot_fixture, request):
    raw = request.getfixturevalue(snapshot_fixture)
    payload = SnapshotPayload.from_snapshot(raw)
    assert payload.statements is not None
    assert payload.top_impact_queries is not None
    assert payload.non_explainable_candidates is not None
    assert payload.locks is not None
    assert payload.active_queries is not None
    assert payload.indexes is not None
    assert payload.tables is not None
    assert payload.columns is not None
    assert payload.canonic_explains is not None
    assert len(payload.statements) > 0
    assert len(payload.columns) > 0


def test_top_impact_forgets_statement_section(mysql_snapshot):
    stmt_ids = {s["query_id"] for s in mysql_snapshot["statements"]}
    for top in mysql_snapshot["top_impact_queries"]:
        assert top["query_id"] in stmt_ids


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_active_query_id_matches_statement(snapshot_fixture, request):
    """Dependencia que usa select_explain_ready: el query_id de pg_stat_activity
    debe existir en statements para que un candidato obtenga plan real."""
    raw = request.getfixturevalue(snapshot_fixture)
    stmt_ids = {s["query_id"] for s in raw["statements"] if s["query_id"] is not None}
    active_ids = [a["query_id"] for a in raw["active_queries"] if a["query_id"] is not None]
    if active_ids:
        for qid in active_ids:
            assert qid in stmt_ids


def test_active_queries_have_canonic_field(mysql_snapshot):
    active = mysql_snapshot["active_queries"]
    assert any(a.get("canonic_query") for a in active)


def test_canonic_explains_have_canonic_query(mysql_snapshot):
    explain_ids = {e["query_id"] for e in mysql_snapshot["canonic_explains"]}
    top_ids = {t["query_id"] for t in mysql_snapshot["top_impact_queries"]}
    assert explain_ids <= top_ids


def test_plan_shape_normalized(postgres_snapshot):
    payload = SnapshotPayload.from_snapshot(postgres_snapshot)
    assert isinstance(payload.canonic_explains, list)
    for item in payload.canonic_explains:
        assert isinstance(item, CanonicExplain)
        plan = item.canonical_plan
        assert isinstance(plan, CanonicalPlan)
        assert isinstance(plan.logical_shape.scans, int)


def test_query_ids_carry_engine_type(mysql_snapshot, postgres_snapshot):
    mysql_stmt = mysql_snapshot["statements"][0]["query_id"]
    pg_stmt = postgres_snapshot["statements"][0]["query_id"]
    assert isinstance(mysql_stmt, str)
    assert isinstance(pg_stmt, int)


def test_locks_boolean_coercion(mysql_snapshot):
    payload = SnapshotPayload.from_snapshot(mysql_snapshot)
    for lock in payload.locks:
        assert lock.is_granted is True or lock.is_granted is False


def test_statements_carry_user_and_schema(postgres_snapshot, mysql_snapshot):
    for stmt in mysql_snapshot["statements"]:
        assert stmt["schema_name"] in (None, "ql_demo")
        assert "userid" not in stmt
    for stmt in postgres_snapshot["statements"]:
        assert stmt["schema_name"] in (None, "public")
        assert "userid" not in stmt
    for top in postgres_snapshot["top_impact_queries"]:
        assert "userid" not in top


def test_active_queries_blocking_pids(mysql_snapshot, postgres_snapshot):
    for raw in (mysql_snapshot, postgres_snapshot):
        payload = SnapshotPayload.from_snapshot(raw)
        for row in payload.active_queries:
            assert isinstance(row.blocking_pids, list)
            assert all(isinstance(pid, int) for pid in row.blocking_pids)