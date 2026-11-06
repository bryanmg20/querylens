"""AP-03 (predicado no sargable) y AP-04 (indice ausente) dentro de AND y OR.

Regla medida en los dos motores (ql_sandbox/explain_or, 4.152 combinaciones):
un AND usa indice si alguna de sus partes puede; un OR solo si todas sus ramas
pueden. Cada detector reporta solo si su arreglo, y solo el suyo, hace que el
predicado pase a usar un indice.
"""
from datetime import datetime, timezone

import pytest
from querylens_contract import SnapshotPayload

from anti_patterns.missing_index import _index_fix, detect_missing_indexes
from anti_patterns.non_sargable_predicate import _non_sargable_fixes
from models import StatementHistory


def _ap03(predicate, *lead_columns):
    return [(subtype, column) for subtype, column, _ in _non_sargable_fixes(predicate, set(lead_columns))]


def _ap04(predicate, *lead_columns):
    fix = _index_fix(predicate, lambda column: True, lambda name: "integer", set(lead_columns))
    return None if fix is None else (fix.equality, fix.range_, fix.per_branch)


# AP-03 predicado no sargable

@pytest.mark.parametrize(
    "predicate, lead_columns, expected",
    [
        # OR: reescribir alcanza solo si las demas ramas ya usan indice
        ("((status = $1) OR (lower(note) = $2))", ("status", "note"), [("function_on_column", "note")]),
        ("((status = $1) OR (lower(note) = $2))", ("note",), []),  # status sin indice: caso mixto
        (
            "((lower(status) = $1) OR (lower(note) = $2))", ("status", "note"),
            [("function_on_column", "status"), ("function_on_column", "note")],
        ),
        ("((status = $1) OR (lower(note) = $2) OR (total > $3))", ("status", "note"), []),
        # AND: alcanza con una parte; nada si otra parte ya usa indice
        ("((code = $1) AND (lower(note) = $2))", ("note",), [("function_on_column", "note")]),
        ("((status = $1) AND (lower(note) = $2))", ("status", "note"), []),
        # AND con un OR: se prefiere la parte simple
        (
            "(((status = $1) OR (lower(note) = $2)) AND ((total + $3) = $4))", ("status", "note", "total"),
            [("arithmetic_on_column", "total")],
        ),
        # anidados
        (
            "(((code = $1) AND (lower(note) = $2)) OR (status = $3))", ("note", "status"),
            [("function_on_column", "note")],
        ),
        # operadores que un indice no busca
        ("(lower(note) <> $1)", ("note",), []),
        ("(lower(note) ~~ $1)", ("note",), []),
        ("(NOT (lower(note) = $1))", ("note",), []),
    ],
)
def test_ap03_and_or(predicate, lead_columns, expected):
    assert _ap03(predicate, *lead_columns) == expected


def test_ap03_marks_condition_inside_or():
    (_, _, in_or), = _non_sargable_fixes("((status = $1) OR (lower(note) = $2))", {"status", "note"})
    assert in_or is True
    (_, _, in_or), = _non_sargable_fixes("((code = $1) AND (lower(note) = $2))", {"note"})
    assert in_or is False


# AP-04 indice ausente

@pytest.mark.parametrize(
    "predicate, lead_columns, expected",
    [
        # OR: cada rama sin indice necesita el suyo
        ("((status = $1) OR (note = $2))", ("note",), (["status"], [], True)),
        ("((status = $1) OR (note = $2))", (), (["status", "note"], [], True)),
        ("((status = $1) OR (total > $2))", (), (["status"], ["total"], True)),
        ("((status = $1) OR (note = $2))", ("status", "note"), None),  # ya usa indices
        # otra rama envuelta: indexar no alcanza (caso mixto)
        ("((status = $1) OR (lower(note) = $2))", (), None),
        ("((status = $1) OR (note ~~ $2))", (), None),
        # AND: un indice compuesto, como antes
        ("((status = $1) AND (total > $2))", (), (["status"], ["total"], False)),
        ("((status = $1) AND (note = $2))", ("note",), None),
        # AND con un OR: se prefiere la parte simple
        ("(((status = $1) OR (total > $2)) AND (code = $3))", (), (["code"], [], False)),
        # AND de dos OR sin parte simple: los dos se pueden arreglar
        (
            "(((status = $1) OR (note = $2)) AND ((code = $3) OR (total > $4)))", ("note", "code"),
            (["status"], ["total"], True),
        ),
    ],
)
def test_ap04_and_or(predicate, lead_columns, expected):
    assert _ap04(predicate, *lead_columns) == expected


def test_ap04_finding_suggests_one_index_per_or_branch():
    statement = {
        "query_id": 1, "query_text": "q", "schema_name": "public", "execution_count": 50,
        "rows_returned": 50, "total_time_ms": 500.0, "mean_time_ms": 10.0,
        "counters_epoch": "2026-10-13 10:00:00.000000",
        "canonic_query": "SELECT id FROM t WHERE status = $1 OR note = $2",
    }
    snapshot = SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": [statement], "top_impact_queries": [statement],
        "locks": [], "active_queries": [],
        "indexes": [{
            "schema_name": "public", "table_name": "t", "index_name": "t_note", "index_scans": 0,
            "index_ref": "CREATE INDEX t_note ON public.t USING btree (note)",
        }],
        "tables": [{"schema_name": "public", "table_name": "t", "live_rows": 100_000}],
        "columns": [
            {"schema_name": "public", "table_name": "t", "column_name": "status", "data_type": "integer"},
            {"schema_name": "public", "table_name": "t", "column_name": "note", "data_type": "integer"},
        ],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        "canonic_explains": [{
            "query_id": 1, "explain_source": "auto_explain",
            "canonical_plan": {"physical_operations": [{
                "type": "scan", "access_method": "full_table_scan", "relation": "t", "table_name": "t",
                "estimated_rows": 10, "predicate": "((status = $1) OR (note = $2))",
            }]},
        }],
    })
    history = {"1": StatementHistory(
        query_id="1", captured_at=datetime(2026, 10, 13, 10, 5, tzinfo=timezone.utc),
        execution_count=30, total_time_ms=500.0, rows_returned=30,
        counters_epoch=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
    )}
    finding, = detect_missing_indexes(snapshot, history, datetime(2026, 10, 13, 10, 6, tzinfo=timezone.utc))
    assert finding.evidencia["indice_por_rama"] is True
    assert finding.evidencia["suggested_index"] == "CREATE INDEX ON t (status)"
    assert "rama del OR" in finding.recomendacion
