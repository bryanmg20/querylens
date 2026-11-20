"""AP-07 vertido a disco (SegundoInforme, Tabla 2), igual para ambos motores:
la consulta escribio algo en disco durante la ultima ventana, es decir, alguno
de sus indicadores de vertido aumento respecto al snapshot anterior.

  - Postgres: disk_spill_indicator = temp_blks_written (bloques de 8 KB) y
    sort_merge_passes en None; el query_id es un entero.
  - MySQL: disk_spill_indicator = SUM_CREATED_TMP_DISK_TABLES y
    sort_merge_passes = SUM_SORT_MERGE_PASSES; el query_id es texto.

Solo se evaluan las consultas de top_impact_queries.
"""
from datetime import datetime, timedelta, timezone

import pytest
from querylens_contract import SnapshotPayload

from anti_patterns.disk_spill import detect_disk_spill
from models import StatementHistory

NOW = datetime(2026, 11, 16, 10, 0, 10, tzinfo=timezone.utc)
PREVIOUS = NOW - timedelta(seconds=10)
EPOCH = "2026-11-16 09:00:00.000000"
EPOCH_AT = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
PG_ID = 88
MYSQL_ID = "ql_demo/spill"
QUERY = "SELECT c, COUNT(*) FROM ql_scn GROUP BY c ORDER BY c"


def _statement(query_id, calls, total_ms, spill=None, merges=None, epoch=EPOCH):
    return {
        "query_id": query_id, "query_text": QUERY, "canonic_query": QUERY,
        "schema_name": "public", "database_name": "ql_demo",
        "execution_count": calls, "rows_returned": calls, "total_time_ms": total_ms,
        "mean_time_ms": total_ms / calls, "counters_epoch": epoch,
        "disk_spill_indicator": spill, "sort_merge_passes": merges,
    }


def _payload(statements, top_impact=None):
    return SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": statements,
        "top_impact_queries": statements if top_impact is None else top_impact,
        "locks": [], "active_queries": [], "indexes": [], "tables": [], "columns": [],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
    })


def _previous(query_id, calls=10, total_ms=1000.0, spill=None, merges=None, captured_at=PREVIOUS):
    return StatementHistory(
        query_id=str(query_id), captured_at=captured_at, execution_count=calls, total_time_ms=total_ms,
        rows_returned=calls, counters_epoch=EPOCH_AT, disk_spill_indicator=spill, sort_merge_passes=merges,
    )


def _detect(statement, previous):
    history = {} if previous is None else {str(previous.query_id): previous}
    return detect_disk_spill(_payload([statement]), history, NOW)


# --------------------------------------------------------------- positivos


def test_postgres_temp_blocks_written_in_the_window_is_reported():
    finding, = _detect(_statement(PG_ID, 12, 3000.0, spill=500), _previous(PG_ID, spill=100))
    assert finding.antipatron == "disk_spill"
    assert finding.evidencia["window_disk_spill"] == 400
    assert finding.evidencia["disk_spill_unit"] == "bloques_8kb"
    # Postgres no separa ordenamientos de tablas temporales
    assert finding.evidencia["tipo_vertido"] == "sin_detalle"
    assert finding.evidencia["window_calls"] == 2
    assert finding.table_name == "ql_scn"


def test_mysql_temp_table_on_disk_is_reported():
    finding, = _detect(
        _statement(MYSQL_ID, 12, 3000.0, spill=7, merges=0), _previous(MYSQL_ID, spill=5, merges=0),
    )
    assert finding.evidencia["disk_spill_unit"] == "tablas_temporales_disco"
    assert finding.evidencia["tipo_vertido"] == "tabla_temporal"
    assert finding.evidencia["window_disk_spill"] == 2


def test_mysql_sort_merge_passes_alone_are_reported():
    # un ORDER BY que no cabe en sort_buffer_size solo sube las merge passes
    finding, = _detect(
        _statement(MYSQL_ID, 12, 3000.0, spill=5, merges=30), _previous(MYSQL_ID, spill=5, merges=10),
    )
    assert finding.evidencia["tipo_vertido"] == "ordenamiento"
    assert finding.evidencia["window_sort_merge_passes"] == 20


def test_mysql_table_and_sort_together():
    finding, = _detect(
        _statement(MYSQL_ID, 12, 3000.0, spill=6, merges=11), _previous(MYSQL_ID, spill=5, merges=10),
    )
    assert finding.evidencia["tipo_vertido"] == "tabla_temporal_y_ordenamiento"


# ---------------------------------------------------------------- controles


def test_no_increase_is_not_reported():
    # escribio a disco antes, pero no en esta ventana
    assert _detect(_statement(PG_ID, 12, 3000.0, spill=100), _previous(PG_ID, spill=100)) == []
    assert _detect(
        _statement(MYSQL_ID, 12, 3000.0, spill=5, merges=10), _previous(MYSQL_ID, spill=5, merges=10),
    ) == []


def test_without_indicators_the_query_is_not_evaluated():
    assert _detect(_statement(PG_ID, 12, 3000.0), _previous(PG_ID)) == []


def test_previous_row_without_the_indicator_has_nothing_to_compare():
    # fila guardada antes de que existiera la columna
    assert _detect(_statement(PG_ID, 12, 3000.0, spill=500), _previous(PG_ID, spill=None)) == []


def test_counters_reset_is_not_compared_with_the_previous_period():
    statement = _statement(PG_ID, 12, 3000.0, spill=500, epoch="2026-11-16 09:30:00.000000")
    assert _detect(statement, _previous(PG_ID, spill=100)) == []


def test_indicator_going_back_is_a_reset_not_a_spill():
    assert _detect(_statement(PG_ID, 12, 3000.0, spill=50), _previous(PG_ID, spill=100)) == []


def test_only_top_impact_queries_are_evaluated():
    statement = _statement(PG_ID, 12, 3000.0, spill=500)
    history = {str(PG_ID): _previous(PG_ID, spill=100)}
    assert detect_disk_spill(_payload([statement], top_impact=[]), history, NOW) == []


# ------------------------------------------------- consulta nueva en la ventana


def test_query_that_started_in_this_window_uses_all_its_counters():
    # sin fila anterior y con el contador empezado hace 5 s
    started = (NOW - timedelta(seconds=5)).replace(tzinfo=None).isoformat(sep=" ")
    finding, = _detect(_statement(PG_ID, 3, 900.0, spill=40, epoch=started), None)
    assert finding.evidencia["query_new_in_window"] is True
    assert finding.evidencia["window_disk_spill"] == 40


def test_query_without_row_that_started_long_ago_waits_for_the_next_snapshot():
    assert _detect(_statement(PG_ID, 3, 900.0, spill=40), None) == []


def test_query_that_started_after_the_previous_snapshot_is_new():
    # hueco largo entre snapshots: el anterior fue hace 10 min y la consulta empezo despues
    started = (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat(sep=" ")
    other = _previous("otra", captured_at=NOW - timedelta(minutes=10))
    finding, = detect_disk_spill(
        _payload([_statement(PG_ID, 3, 900.0, spill=40, epoch=started)]), {"otra": other}, NOW,
    )
    assert finding.evidencia["query_new_in_window"] is True


# ---------------------------------------------------------------- severidad


@pytest.mark.parametrize(("window_ms", "severity"), [
    (10_000.0, "high"),
    (1_000.0, "medium"),
    (999.0, "low"),
])
def test_severity_follows_the_time_of_the_query_in_the_window(window_ms, severity):
    finding, = _detect(
        _statement(PG_ID, 12, 1000.0 + window_ms, spill=500), _previous(PG_ID, total_ms=1000.0, spill=100),
    )
    assert finding.severidad == severity
    assert finding.impacto_ms == pytest.approx(window_ms)
