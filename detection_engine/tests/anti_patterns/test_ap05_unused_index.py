"""AP-05 (indice no utilizado o redundante): un indice no primario ni unico
sin lecturas en la ventana de estadisticas, o cuyas columnas son prefijo
izquierdo de otro indice sin WHERE de la misma tabla. Cada hallazgo va con una
query de top_impact_queries que corrio en la ventana y filtra esa tabla.
"""
from datetime import datetime, timedelta, timezone

from querylens_contract import SnapshotPayload

from anti_patterns.unused_index import (
    DEFAULT_MIN_STATS_WINDOW,
    _seen_in_plans,
    _used_index_keys,
    detect_unused_indexes,
)
from models import StatementHistory

CAPTURED_AT = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SERVER_START = "2026-11-01 00:00:00.000000"
QUERY_ID = "ql_demo/q1"


def _index(name, columns, scans, table="sbtest1", unique=False, where="", schema="ql_demo"):
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return {
        "schema_name": schema, "table_name": table, "index_name": name, "index_scans": scans,
        "index_ref": f"CREATE {kind} {name} ON {schema}.{table} USING btree ({columns}){where}",
    }


def _scan(relation, index_name=None, table_name=None, access_method="index_scan", predicate=None):
    return {
        "type": "scan", "access_method": access_method, "relation": relation,
        "table_name": table_name, "index_name": index_name, "predicate": predicate,
    }


def _snapshot(indexes, operations, canonic_query="SELECT c FROM sbtest1 WHERE k = $1",
              stats_reset_time=None, execution_count=50, schema="ql_demo", foreign_keys=()):
    statement = {
        "query_id": QUERY_ID, "query_text": "q", "schema_name": schema,
        "execution_count": execution_count, "rows_returned": 50, "total_time_ms": 100.0,
        "mean_time_ms": 2.0, "canonic_query": canonic_query,
    }
    columns = [
        {"schema_name": "ql_demo", "table_name": table, "column_name": column, "data_type": "integer"}
        for table in ("sbtest1", "sbtest2")
        for column in ("id", "k", "c", "pad")
    ]
    return SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": [statement], "top_impact_queries": [statement],
        "locks": [], "active_queries": [], "indexes": indexes, "tables": [], "columns": columns,
        "foreign_keys": list(foreign_keys),
        "server_start_timestamp": [{"server_start_time": SERVER_START, "stats_reset_time": stats_reset_time}],
        "canonic_explains": [{
            "query_id": QUERY_ID, "explain_source": "history",
            "canonical_plan": {"physical_operations": operations},
        }],
    })


def _history(execution_count=30):
    return {QUERY_ID: StatementHistory(
        query_id=QUERY_ID, captured_at=CAPTURED_AT - timedelta(seconds=10),
        execution_count=execution_count, total_time_ms=60.0,
    )}


def _detect(snapshot, history=None):
    return detect_unused_indexes(snapshot, DEFAULT_MIN_STATS_WINDOW, history or _history(), CAPTURED_AT)


def _by_index(findings):
    return {(f.evidencia["table_name"], f.evidencia["index_name"]): f for f in findings}


def _sysbench_indexes():
    return [
        _index("sbtest1_pkey", "id", 500, unique=True),
        _index("k_1", "k", 500),
        _index("idx_pad", "pad", 0),
        _index("k_1_dup", "k", 0),
    ]


def test_informe_scenario_unused_pad_and_duplicate_of_k_1():
    findings = _by_index(_detect(_snapshot(_sysbench_indexes(), [_scan("sbtest1", "k_1", "sbtest1")])))
    assert set(findings) == {("sbtest1", "idx_pad"), ("sbtest1", "k_1_dup")}

    unused = findings[("sbtest1", "idx_pad")]
    assert unused.evidencia["subtipo"] == "no_usado"
    assert unused.severidad == "low"  # la query filtra por k, no por pad
    assert unused.query_id == QUERY_ID

    redundant = findings[("sbtest1", "k_1_dup")]
    assert redundant.evidencia["subtipo"] == "redundante"
    assert redundant.evidencia["tipo_redundancia"] == "duplicado_exacto"
    assert redundant.evidencia["cubierto_por"] == "k_1"


def test_unused_index_filtered_by_its_lead_column_is_high():
    indexes = [_index("k_1", "k", 0)]
    operations = [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan", predicate="(k = 5)")]
    finding, = _detect(_snapshot(indexes, operations))
    assert finding.evidencia["subtipo"] == "no_usado"
    assert finding.severidad == "high"


def test_full_index_scan_counts_as_full_scan_like_ap02():
    # recorre el primario entero filtrando por k: igual que un full_table_scan
    indexes = [_index("k_1", "k", 0)]
    operations = [_scan("sbtest1", "PRIMARY", "sbtest1", access_method="full_index_scan", predicate="(k = 5)")]
    finding, = _detect(_snapshot(indexes, operations))
    query, = finding.evidencia["queries_que_filtran_la_tabla"]
    assert query["scan_completo_por_esa_columna"] is True


def test_index_cited_in_a_captured_plan_is_not_unused_even_with_zero_scans():
    # Postgres actualiza idx_scan con retraso: el plan ya lo cito
    indexes = [_index("k_1", "k", 0)]
    assert _detect(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")])) == []


def test_plan_use_is_matched_by_table_and_index_not_only_by_name():
    # MySQL: el plan trae el alias y no table_name; sbtest1.k_1 se uso, sbtest2.k_1 no
    indexes = [_index("k_1", "k", 0, table="sbtest1"), _index("k_1", "k", 0, table="sbtest2")]
    operations = [_scan("a", "k_1"), _scan("b", "PRIMARY")]
    query = "SELECT a.c FROM sbtest1 a JOIN sbtest2 b ON b.id = a.id WHERE a.k = $1"
    findings = _by_index(_detect(_snapshot(indexes, operations, canonic_query=query)))
    assert set(findings) == {("sbtest2", "k_1")}
    assert findings[("sbtest2", "k_1")].severidad == "low"  # sbtest2 se filtra por id


def test_plan_use_is_matched_by_schema_too():
    # misma tabla e indice en dos schemas (Postgres y MySQL): el plan es de una
    # query de ql_demo, asi que solo cuenta el de ql_demo
    indexes = [_index("k_1", "k", 0), _index("k_1", "k", 0, schema="ql_otro")]
    used = _used_index_keys(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")]))
    assert _seen_in_plans(used, "ql_demo", "sbtest1", "k_1")
    assert not _seen_in_plans(used, "ql_otro", "sbtest1", "k_1")


def test_plan_of_a_table_in_another_schema_counts_in_any_schema():
    # la query es de ql_demo pero lee ql_otro.sbtest1: ql_demo no tiene ese
    # indice, asi que no se sabe el schema y se cuenta como visto, sin reportar
    indexes = [_index("k_1", "k", 0, schema="ql_otro")]
    used = _used_index_keys(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")]))
    assert _seen_in_plans(used, "ql_otro", "sbtest1", "k_1")


def test_plan_of_a_query_without_schema_counts_in_any_schema():
    indexes = [_index("k_1", "k", 0), _index("k_1", "k", 0, schema="ql_otro")]
    used = _used_index_keys(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")], schema=None))
    assert _seen_in_plans(used, "ql_demo", "sbtest1", "k_1")
    assert _seen_in_plans(used, "ql_otro", "sbtest1", "k_1")


def _fk(columns, table="sbtest1", name="fk_parent"):
    return [
        {"schema_name": "ql_demo", "table_name": table, "column_name": column,
         "referenced_schema_name": "ql_demo", "referenced_table_name": "parent",
         "referenced_column_name": column, "constraint_name": name, "position": position}
        for position, column in enumerate(columns, start=1)
    ]


def test_the_only_index_backing_a_foreign_key_is_not_unused():
    # MySQL no deja eliminarlo; en Postgres el DELETE del padre recorreria la hija
    indexes = [_index("k_1", "k", 0)]
    snapshot = _snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan")],
                         foreign_keys=_fk(["k"]))
    assert _detect(snapshot) == []


def test_a_foreign_key_backed_by_two_unused_indexes_keeps_the_narrowest():
    # ninguno tiene lecturas: (k) no sale como redundante de (k, c), se protege
    # el mas angosto para la FK y (k, c) sale como no usado
    indexes = [_index("k_1", "k", 0), _index("idx_k_c", "k, c", 0)]
    snapshot = _snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan")],
                         foreign_keys=_fk(["k"]))
    findings = _by_index(_detect(snapshot))
    assert set(findings) == {("sbtest1", "idx_k_c")}
    assert findings[("sbtest1", "idx_k_c")].evidencia["subtipo"] == "no_usado"


def test_a_duplicate_backing_a_foreign_key_leaves_the_kept_one():
    indexes = [_index("k_1", "k", 0), _index("k_1_dup", "k", 0)]
    snapshot = _snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan")],
                         foreign_keys=_fk(["k"]))
    findings = _by_index(_detect(snapshot))
    assert set(findings) == {("sbtest1", "k_1_dup")}
    assert findings[("sbtest1", "k_1_dup")].evidencia["subtipo"] == "no_usado"


def test_redundant_requires_reads_on_the_index_that_covers_it():
    # (c) se usa y (c, k) no: lo que sobra es (c, k), no (c)
    indexes = [_index("idx_c", "c", 300), _index("idx_c_k", "c, k", 0)]
    findings = _by_index(_detect(_snapshot(indexes, [_scan("sbtest1", "idx_c", "sbtest1")])))
    assert set(findings) == {("sbtest1", "idx_c_k")}
    assert findings[("sbtest1", "idx_c_k")].evidencia["subtipo"] == "no_usado"


def test_two_unused_overlapping_indexes_are_both_unused_not_redundant():
    indexes = [_index("idx_c", "c", 0), _index("idx_c_k", "c, k", 0)]
    snapshot = _snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan")])
    findings = _by_index(_detect(snapshot))
    assert {key: f.evidencia["subtipo"] for key, f in findings.items()} == {
        ("sbtest1", "idx_c"): "no_usado",
        ("sbtest1", "idx_c_k"): "no_usado",
    }


def test_fresh_stats_window_reports_redundant_without_looking_at_reads():
    # los contadores recien empiezan: no dicen nada y "no usado" no se evalua
    start = (CAPTURED_AT - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S.%f")
    indexes = [_index("idx_c", "c", 0), _index("idx_c_k", "c, k", 0)]
    snapshot = _snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan")])
    snapshot.server_start_timestamp[0].server_start_time = start
    finding, = _detect(snapshot)
    assert finding.evidencia["index_name"] == "idx_c"
    assert finding.evidencia["subtipo"] == "redundante"


def test_duplicate_seen_in_a_plan_is_the_one_kept():
    # los dos con el contador en 0, pero el plan ya usa k_2 (contador atrasado):
    # se conserva k_2 aunque k_1 vaya primero por nombre
    indexes = [_index("k_1", "k", 0), _index("k_2", "k", 0)]
    finding, = _detect(_snapshot(indexes, [_scan("sbtest1", "k_2", "sbtest1")]))
    assert finding.evidencia["index_name"] == "k_1"
    assert finding.evidencia["subtipo"] == "redundante"
    assert finding.evidencia["cubierto_por"] == "k_2"


def test_composite_foreign_key_matches_the_leading_columns_in_any_order():
    indexes = [_index("idx_c_k", "c, k", 0), _index("idx_pad", "pad", 0)]
    snapshot = _snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan")],
                         foreign_keys=_fk(["k", "c"]))
    assert set(_by_index(_detect(snapshot))) == {("sbtest1", "idx_pad")}


def test_recent_stats_reset_holds_unused_but_not_redundant():
    # pg_stat_reset() hace 5 min: todos los contadores en 0 no prueban nada
    reset = (CAPTURED_AT - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S.%f")
    snapshot = _snapshot(_sysbench_indexes(), [_scan("sbtest1", "k_1", "sbtest1")], stats_reset_time=reset)
    findings = _by_index(_detect(snapshot))
    assert set(findings) == {("sbtest1", "k_1_dup")}


def test_window_is_measured_against_captured_at():
    # el servidor arranco 5 min antes del snapshot, aunque hoy sea mucho despues
    start = (CAPTURED_AT - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S.%f")
    snapshot = _snapshot([_index("idx_pad", "pad", 0)], [_scan("sbtest1", "k_1", "sbtest1")])
    snapshot.server_start_timestamp[0].server_start_time = start
    assert _detect(snapshot) == []


def test_unique_indexes_are_never_reported():
    indexes = [_index("uq_pad", "pad", 0, unique=True), _index("uq_pad_2", "pad", 0, unique=True)]
    assert _detect(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")])) == []


def test_prefix_of_a_wider_index_is_redundant():
    indexes = [_index("k_1", "k", 500), _index("idx_k_c", "k, c", 10)]
    finding, = _detect(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")]))
    assert finding.evidencia["index_name"] == "k_1"
    assert finding.evidencia["tipo_redundancia"] == "prefijo"
    assert finding.severidad == "medium"  # el planificador lo eligio en un plan real
    assert finding.evidencia["confianza_eliminar"] == "baja"


def test_a_partial_wider_index_does_not_cover_a_full_one():
    indexes = [_index("k_1", "k", 500), _index("idx_k_c", "k, c", 10, where=" WHERE (pad IS NOT NULL)")]
    assert _detect(_snapshot(indexes, [_scan("sbtest1", "k_1", "sbtest1")])) == []


def test_requires_a_query_that_ran_since_the_previous_snapshot():
    snapshot = _snapshot(_sysbench_indexes(), [_scan("sbtest1", "k_1", "sbtest1")], execution_count=30)
    assert _detect(snapshot, _history(execution_count=30)) == []
