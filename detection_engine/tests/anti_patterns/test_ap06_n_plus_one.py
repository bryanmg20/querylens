"""AP-06 patron N+1 (SegundoInforme, Tabla 2, con ventanas de 10 s), igual
para ambos motores: una huella hija con igualdad sobre las columnas de una
clave foranea se ejecuta >= 50 veces en la ventana, y sus ejecuciones
coinciden (+-20 %) con las filas que devolvio en la misma ventana una huella
padre que lee la otra tabla de la relacion y devuelve >= 2 filas por
ejecucion.

Relacion de las pruebas: orders.customer_id -> customers.id.
  uno_a_muchos: padre lee customers, hija orders WHERE customer_id = ?
  muchos_a_uno: padre lee orders, hija customers WHERE id = ?
"""
from datetime import datetime, timedelta, timezone

import pytest
from querylens_contract import SnapshotPayload

from anti_patterns.n_plus_one import detect_n_plus_one
from models import StatementHistory

NOW = datetime(2026, 11, 16, 10, 0, 10, tzinfo=timezone.utc)
PREVIOUS = NOW - timedelta(seconds=10)
EPOCH = "2026-11-16 09:00:00.000000"
EPOCH_AT = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

PARENT_ID, CHILD_ID = 11, 12
CUSTOMERS_BY_REGION = "SELECT id, name FROM customers WHERE region = $1"
ORDERS_BY_CUSTOMER = "SELECT id, total FROM orders WHERE customer_id = $1"
ORDERS_BY_STATUS = "SELECT id, customer_id FROM orders WHERE status = $1"
CUSTOMER_BY_ID = "SELECT name FROM customers WHERE id = $1"

FOREIGN_KEY = {
    "schema_name": "public", "table_name": "orders", "column_name": "customer_id",
    "referenced_schema_name": "public", "referenced_table_name": "customers",
    "referenced_column_name": "id", "constraint_name": "orders_customer_fk", "position": 1,
}

# contadores acumulados de las filas anteriores
BASE_CALLS, BASE_ROWS, BASE_MS = 1000, 3000, 2000.0


def _statement(query_id, canonic_query, calls, rows, database="ql_demo"):
    return {
        "query_id": query_id, "query_text": canonic_query, "canonic_query": canonic_query,
        "schema_name": "public", "database_name": database,
        "execution_count": BASE_CALLS + calls, "rows_returned": BASE_ROWS + rows,
        "total_time_ms": BASE_MS + calls, "mean_time_ms": 1.0, "counters_epoch": EPOCH,
    }


def _previous(query_id, captured_at=PREVIOUS):
    return StatementHistory(
        query_id=str(query_id), captured_at=captured_at, execution_count=BASE_CALLS,
        total_time_ms=BASE_MS, rows_returned=BASE_ROWS, counters_epoch=EPOCH_AT,
    )


def _detect(
    parent_calls=2, parent_rows=100, child_calls=100, child_rows=300,
    parent_query=CUSTOMERS_BY_REGION, child_query=ORDERS_BY_CUSTOMER,
    foreign_keys=(FOREIGN_KEY,), child_database="ql_demo", parent_previous_at=PREVIOUS,
):
    # parent_calls / parent_rows / child_calls / child_rows: lo de la ventana
    statements = [
        _statement(PARENT_ID, parent_query, parent_calls, parent_rows),
        _statement(CHILD_ID, child_query, child_calls, child_rows, database=child_database),
    ]
    snapshot = SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": statements, "top_impact_queries": [],
        "locks": [], "active_queries": [], "indexes": [], "tables": [], "columns": [],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        "foreign_keys": list(foreign_keys),
    })
    history = {
        str(PARENT_ID): _previous(PARENT_ID, captured_at=parent_previous_at),
        str(CHILD_ID): _previous(CHILD_ID),
    }
    return detect_n_plus_one(snapshot, history, NOW)


# ---------------------------------------------------------------- positivos


def test_one_to_many_child_runs_once_per_parent_row():
    finding, = _detect()
    assert finding.antipatron == "n_plus_one"
    assert finding.query_id == CHILD_ID
    assert finding.table_name == "orders"
    assert finding.evidencia["direccion"] == "uno_a_muchos"
    assert finding.evidencia["parent_query_id"] == PARENT_ID
    assert finding.evidencia["child_window_calls"] == 100
    assert finding.evidencia["parent_window_rows"] == 100
    assert finding.evidencia["constraint_name"] == "orders_customer_fk"


def test_many_to_one_child_reads_the_referenced_row_of_each_parent_row():
    finding, = _detect(parent_query=ORDERS_BY_STATUS, child_query=CUSTOMER_BY_ID, child_rows=100)
    assert finding.table_name == "customers"
    assert finding.evidencia["direccion"] == "muchos_a_uno"


# ------------------------------------------------------- umbrales de la regla


def test_child_needs_at_least_fifty_calls_in_the_window():
    assert _detect(parent_rows=49, child_calls=49) == []
    assert len(_detect(parent_rows=50, child_calls=50)) == 1


@pytest.mark.parametrize(("child_calls", "reported"), [
    (80, True), (120, True),     # 20 % de desvio, en el limite
    (79, False), (121, False),   # 21 %
])
def test_child_calls_must_match_parent_rows_within_twenty_percent(child_calls, reported):
    assert bool(_detect(parent_rows=100, child_calls=child_calls)) is reported


def test_parent_must_return_at_least_two_rows_per_call():
    # 100 ejecuciones del padre con una fila cada una: el "1 + 1" de una pagina de detalle
    assert _detect(parent_calls=100, parent_rows=100, child_calls=100) == []


# ---------------------------------------------------------------- controles


def test_without_foreign_keys_there_is_no_relation_to_check():
    assert _detect(foreign_keys=()) == []


def test_child_with_in_list_is_the_fix_not_the_pattern():
    child = "SELECT id, total FROM orders WHERE customer_id IN ($1, $2, $3)"
    assert _detect(child_query=child) == []


def test_locking_reads_are_excluded():
    # Postgres registra el chequeo de cada FK como un SELECT ... FOR KEY SHARE por fila escrita
    child = "SELECT id FROM orders WHERE customer_id = $1 FOR UPDATE"
    assert _detect(child_query=child) == []


def test_parent_and_child_must_be_in_the_same_database():
    assert _detect(child_database="otra_base") == []


def test_parent_and_child_must_be_measured_against_the_same_snapshot():
    assert _detect(parent_previous_at=PREVIOUS - timedelta(seconds=10)) == []


def test_parent_that_does_not_read_the_related_table_is_not_a_parent():
    unrelated = "SELECT id, name FROM products WHERE category = $1"
    assert _detect(parent_query=unrelated) == []


# ---------------------------------------------------------------- severidad


def test_severity_is_high_from_five_hundred_child_calls():
    assert _detect(parent_rows=500, child_calls=500)[0].severidad == "high"
    assert _detect(parent_rows=499, child_calls=499)[0].severidad == "medium"
