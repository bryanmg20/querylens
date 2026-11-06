"""CT-04: ampliacion de bloqueos por ausencia de indice."""
from deadlock_builders import (
    T0,
    index_row,
    mysql_ct01,
    mysql_ct03,
    mysql_ct04,
    pg_ct01,
    pg_ct02,
    report,
    snapshot,
)

from deadlocks.ct04_lock_amplification import ANTIPATRON, is_lock_amplification
from deadlocks.deadlock_detector import detect_deadlocks
from deadlocks.state import DeadlockMemory

# indices de ql_ct04_pedidos como los deja el colector: solo la clave primaria
PRIMARY_ONLY = [index_row("id")]


def _ct04(raw, indexes=PRIMARY_ONLY):
    return is_lock_amplification(report(raw), snapshot(indexes=indexes).indexes)


# --------------------------------------------------------------- la regla


def test_mysql_update_without_index_on_the_where_column_is_ct04():
    assert _ct04(mysql_ct04())


def test_table_without_indexes_is_ct04():
    # InnoDB usa un indice oculto: la tabla no aparece en snapshot.indexes
    assert _ct04(mysql_ct04(), indexes=[index_row("id", table="otra_tabla")])


def test_index_starting_with_the_where_column_is_not_ct04():
    assert not _ct04(mysql_ct04(), indexes=[*PRIMARY_ONLY, index_row("cliente, estado", name="ix_cliente")])


def test_index_with_the_column_second_does_not_count():
    assert _ct04(mysql_ct04(), indexes=[*PRIMARY_ONLY, index_row("estado, cliente", name="ix_estado")])


def test_index_of_the_same_table_in_another_schema_does_not_count():
    other_schema = index_row("cliente", name="ix_cliente", schema="otra")
    assert _ct04(mysql_ct04(), indexes=[*PRIMARY_ONLY, other_schema])


def test_nine_to_one_is_not_amplification():
    raw = mysql_ct04(scanner={"rows_locked": 27, "undo_log_entries": 3})
    assert not _ct04(raw)


def test_delete_without_index_is_ct04():
    raw = mysql_ct04(scanner={"canonic_query": "DELETE FROM ql_ct04_pedidos WHERE cliente = $1"})
    assert _ct04(raw)


def test_amplified_select_is_not_ct04():
    # la regla es de UPDATE/DELETE; un SELECT ... FOR UPDATE ampliado queda sin patron
    raw = mysql_ct04(scanner={"canonic_query": "SELECT * FROM ql_ct04_pedidos WHERE cliente = $1 FOR UPDATE"})
    assert not _ct04(raw)


def test_update_with_a_join_is_not_ct04():
    query = "UPDATE ql_ct04_pedidos AS p JOIN clientes AS c ON c.id = p.cliente SET p.estado = $1 WHERE c.zona = $2"
    assert not _ct04(mysql_ct04(scanner={"canonic_query": query}))


def test_where_without_comparable_columns_is_not_ct04():
    # la columna dentro de una funcion no se arregla con un indice simple (AP-03)
    query = "UPDATE ql_ct04_pedidos SET estado = $1 WHERE LOWER(estado) = $2"
    assert not _ct04(mysql_ct04(scanner={"canonic_query": query}))


def test_without_a_cycle_inside_the_report_is_not_ct04():
    assert not _ct04(mysql_ct04(other={"blocked_by": [999]}))


def test_other_patterns_are_not_ct04():
    for raw in (mysql_ct01(), mysql_ct03(), pg_ct01(), pg_ct02()):
        assert not _ct04(raw)


# ------------------------------------------------------------- hallazgo

def test_finding_recommends_the_ap04_index():
    findings, _ = detect_deadlocks(snapshot(mysql_ct04(), indexes=PRIMARY_ONLY), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.antipatron == ANTIPATRON
    assert finding.severidad == "medium"
    assert finding.table_name == "ql_demo.ql_ct04_pedidos"
    evidence = finding.evidencia
    assert evidence["related_antipattern"] == "AP-04"
    assert evidence["amplification"] == {
        "process_id": 448,
        "canonic_query": "UPDATE ql_ct04_pedidos SET estado = $1 WHERE cliente = $2",
        "table": "ql_demo.ql_ct04_pedidos",
        "rows_locked": 150,
        "undo_log_entries": 3,
        "ratio": 50.0,
        "equality_columns": ["cliente"],
        "range_columns": [],
        "suggested_index": "CREATE INDEX ON ql_ct04_pedidos (cliente)",
    }
    assert "limitations" not in evidence
    assert "dejo bloqueadas 150 filas para modificar 3" in finding.explicacion
    assert "CREATE INDEX ON ql_ct04_pedidos (cliente)" in finding.recomendacion
    assert "AP-04" in finding.recomendacion


def test_finding_with_a_range_column_and_an_alias():
    query = "DELETE FROM ql_ct04_pedidos AS p WHERE p.cliente = $1 AND p.creado < $2"
    raw = mysql_ct04(scanner={"canonic_query": query, "undo_log_entries": 0})
    findings, _ = detect_deadlocks(snapshot(raw, indexes=PRIMARY_ONLY), T0, DeadlockMemory())

    (finding,) = findings
    amplification = finding.evidencia["amplification"]
    assert amplification["suggested_index"] == "CREATE INDEX ON ql_ct04_pedidos (cliente, creado)"
    assert "un DELETE sobre ql_demo.ql_ct04_pedidos" in finding.explicacion
    assert "sin haber modificado ninguna" in finding.explicacion
