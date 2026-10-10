"""HealthReport, catalogo y chequeos post-collect: puro, sin base."""
import pytest

from health.catalog import BLOCKING, CATALOG, DEGRADED, INFO
from health.checks import PG_HIDDEN_TEXT, post_collect
from health.report import HealthReport

pytestmark = pytest.mark.unit

SCOPES = {"connection", "credentials", "collect", "preflight", "explain", "pipeline"}
CATEGORIES = {"connection", "extension", "permission", "config", "version", "internal"}


# ---------- catalogo ----------


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_catalog_entries_match_the_schema_checks(code):
    definition = CATALOG[code]
    assert definition.scope in SCOPES
    assert definition.category in CATEGORIES
    assert definition.severity in {BLOCKING, DEGRADED, INFO}
    assert definition.message.strip() and definition.remediation.strip()


def test_codes_have_no_separator_used_by_resolve():
    # writer.RESOLVE_MISSING compara code || '/' || section.
    assert not [code for code in CATALOG if "/" in code]


# ---------- report ----------


def test_unknown_code_is_rejected():
    with pytest.raises(KeyError):
        HealthReport().add("NO_EXISTE")


def test_repeated_issue_is_one_entry_with_count():
    report = HealthReport()
    report.add("EXPLAIN_PERMISSION_DENIED", sqlstate="42501")
    report.add("EXPLAIN_PERMISSION_DENIED", sqlstate="42501")
    report.add("EXPLAIN_PERMISSION_DENIED", sqlstate="42501")
    assert report.issues == {("EXPLAIN_PERMISSION_DENIED", ""): {"sqlstate": "42501", "count": 3}}


def test_same_code_in_different_sections_is_separate():
    report = HealthReport()
    report.add("SECTION_FAILED", section="locks")
    report.add("SECTION_FAILED", section="tables")
    assert set(report.issues) == {("SECTION_FAILED", "locks"), ("SECTION_FAILED", "tables")}


@pytest.mark.parametrize(
    "codes, status",
    [
        ([], "ok"),
        (["STATS_RECENTLY_RESET"], "ok"),
        (["STATS_RECENTLY_RESET", "TRACK_COUNTS_OFF"], "degraded"),
        (["TRACK_COUNTS_OFF", "PG_STATEMENTS_NOT_INSTALLED"], "failed"),
    ],
)
def test_status_follows_worst_severity(codes, status):
    report = HealthReport()
    for code in codes:
        report.add(code)
    assert report.status() == status


def test_recorded_mark_travels_with_the_exception():
    from health.report import is_recorded, mark_recorded

    exc = RuntimeError("x")
    assert not is_recorded(exc)
    mark_recorded(exc)
    assert is_recorded(exc)


# ---------- post_collect ----------


def test_pg_hidden_text_in_statements_and_activity_is_counted():
    report = HealthReport()
    stats = {
        "statements": [{"query_text": PG_HIDDEN_TEXT}, {"query_text": "SELECT 1"}],
        "active_queries": [{"query_text": PG_HIDDEN_TEXT}],
    }
    post_collect(stats, report, "postgres")
    assert report.issues == {("MISSING_PG_READ_ALL_STATS", ""): {"rows": 2, "count": 1}}


def test_pg_old_version_hides_the_misleading_outdated_extension():
    # PG 16: statements falla con 42703 (stats_since) y el collect dice
    # "extension desactualizada"; la causa es la version y solo esa se muestra.
    report = HealthReport()
    report.add("PG_VERSION_UNSUPPORTED", server_version_num=160004)
    report.add("PG_STATEMENTS_OUTDATED", section="statements", sqlstate="42703")
    post_collect({"statements": None}, report, "postgres")
    assert set(report.issues) == {("PG_VERSION_UNSUPPORTED", "")}


def test_pg17_outdated_extension_is_kept():
    report = HealthReport()
    report.add("PG_STATEMENTS_OUTDATED", section="statements", sqlstate="42703")
    post_collect({"statements": None}, report, "postgres")
    assert ("PG_STATEMENTS_OUTDATED", "statements") in report.issues


def test_pg_visible_text_reports_nothing():
    report = HealthReport()
    post_collect({"statements": [{"query_text": "SELECT 1"}], "active_queries": None}, report, "postgres")
    assert report.issues == {}


def test_mysql_sample_at_limit_is_truncated():
    report = HealthReport()
    report.facts["sql_text_limit"] = 10
    stats = {"statements": [{"query_sample_text": "x" * 10}, {"query_sample_text": "short"}]}
    post_collect(stats, report, "mysql")
    assert report.issues[("QUERY_TEXT_TRUNCATED", "")]["rows"] == 1


def test_mysql_without_known_limit_reports_nothing():
    report = HealthReport()
    post_collect({"statements": [{"query_sample_text": "x" * 5000}]}, report, "mysql")
    assert report.issues == {}
