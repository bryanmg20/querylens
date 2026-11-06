"""pipeline_health de punta a punta contra la base de QueryLens real.

Aplica querylens_database/pipeline_health.sql (idempotente) y recorre
upsert -> resolve -> purge con un db_id propio que se limpia al final.
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from config.connections import get_connection_querylens_db
from health import writer
from health.report import HealthReport

pytestmark = pytest.mark.integration

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "querylens_database" / "pipeline_health.sql"


@pytest.fixture(scope="module")
def ql():
    try:
        engine = get_connection_querylens_db()
        with engine.begin() as conn:
            conn.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
        return engine
    except Exception as exc:
        pytest.skip(f"base de QueryLens no disponible: {type(exc).__name__}: {str(exc)[:120]}")


@pytest.fixture
def db_id(ql):
    value = f"it_{uuid.uuid4().hex[:8]}"
    yield value
    with ql.begin() as conn:
        conn.execute(text("DELETE FROM pipeline_health.issues WHERE db_id = :d"), {"d": value})
        conn.execute(text("DELETE FROM pipeline_health.last_run WHERE db_id = :d"), {"d": value})


def _issues(ql, db_id):
    with ql.connect() as conn:
        return conn.execute(text(
            "SELECT code, section, occurrences, resolved_at, params "
            "FROM pipeline_health.issues WHERE db_id = :d ORDER BY id"
        ), {"d": db_id}).mappings().all()


def _report(*codes, scopes=("connection", "collect")):
    report = HealthReport()
    report.checked(*scopes)
    for code in codes:
        report.add(code, section="statements" if code.startswith("PG_") else "")
    return report


def test_issue_lifecycle_upsert_resolve_purge(ql, db_id):
    now = datetime.now(timezone.utc)

    writer.flush(ql, db_id, _report("PG_STATEMENTS_NOT_INSTALLED"), now)
    writer.flush(ql, db_id, _report("PG_STATEMENTS_NOT_INSTALLED"), now)
    [issue] = _issues(ql, db_id)
    assert issue["code"] == "PG_STATEMENTS_NOT_INSTALLED"
    assert issue["section"] == "statements"
    assert issue["occurrences"] == 2
    assert issue["resolved_at"] is None

    with ql.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM pipeline_health.last_run WHERE db_id = :d"
        ), {"d": db_id}).scalar_one()
    assert status == "failed"

    # El ciclo siguiente ya no lo ve: queda resuelto.
    writer.flush(ql, db_id, _report(), now)
    [issue] = _issues(ql, db_id)
    assert issue["resolved_at"] is not None

    # Si vuelve a aparecer es una fila nueva; la resuelta queda como historia.
    writer.flush(ql, db_id, _report("PG_STATEMENTS_NOT_INSTALLED"), now)
    assert [i["resolved_at"] is None for i in _issues(ql, db_id)] == [False, True]

    # Resuelta hace 16 dias -> la purga la borra; la abierta se queda.
    with ql.begin() as conn:
        conn.execute(text(
            "UPDATE pipeline_health.issues SET resolved_at = now() - interval '16 days' "
            "WHERE db_id = :d AND resolved_at IS NOT NULL"
        ), {"d": db_id})
    writer.purge(ql, days=15)
    [issue] = _issues(ql, db_id)
    assert issue["resolved_at"] is None


def test_unchecked_scope_is_not_resolved(ql, db_id):
    now = datetime.now(timezone.utc)
    writer.flush(ql, db_id, _report("TRACK_COUNTS_OFF", scopes=("preflight",)), now)
    # Ciclo con preflight cacheado: no evalua 'preflight', no lo resuelve.
    writer.flush(ql, db_id, _report(scopes=("connection", "collect")), now)
    [issue] = _issues(ql, db_id)
    assert issue["resolved_at"] is None
