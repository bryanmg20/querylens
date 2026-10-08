from collections import Counter
import json

import pytest

from models.snapshot import (
    CanonicExplain,
    CanonicalPlan,
    SnapshotPayload,
)

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    "snapshot_name", ["postgres_snapshot", "mysql_snapshot"]
)
def test_goldens_shape_counters_match_physical_operations(request, snapshot_name):
    """Cada contador de LogicalShape debe tener su tipo en physical_operations.

    El golden es la unica fuente que demuestra el contrato sobre planes reales
    de ambos motores: si un normalizador cuenta y luego descarta, los contadores
    cuadran con cero operaciones y este test cae.
    """
    snapshot = request.getfixturevalue(snapshot_name)
    field_to_type = {
        "scans": "scan",
        "joins": "join",
        "aggregates": "aggregate",
        "sorts": "sort",
        "subqueries": "subquery",
        "distinct": "distinct",
    }
    for explain in snapshot["canonic_explains"]:
        plan = explain["canonical_plan"]
        counted = Counter(op["type"] for op in plan["physical_operations"])
        for field, operation_type in field_to_type.items():
            assert plan["logical_shape"][field] == counted.get(operation_type, 0), (
                f"{explain['query_id']}: {field}="
                f"{plan['logical_shape'][field]} pero hay "
                f"{counted.get(operation_type, 0)} operaciones '{operation_type}'"
            )


def test_mysql_golden_validates(mysql_snapshot):
    payload = SnapshotPayload.from_snapshot(mysql_snapshot)
    assert payload.db_id == mysql_snapshot["db_id"]


def test_postgres_golden_validates(postgres_snapshot):
    payload = SnapshotPayload.from_snapshot(postgres_snapshot)
    assert payload.db_id == postgres_snapshot["db_id"]


def test_payload_does_not_carry_query_sample_text(mysql_snapshot, postgres_snapshot):
    """query_sample_text es flujo interno, NO es campo del contrato.

    MySQL lo emite con literales reales (QUERY_SAMPLE_TEXT) para armar el plan
    explain_source="sample"; Postgres no produce la columna. El requerimiento
    del proyecto es NO persistir informacion sensible o real de las queries, asi
    que el payload debe descartarlo en ambos motores y solo viajar los textos
    normalizados (query_text con placeholders). extra='ignore' es el mecanismo,
    este test es la reja: si alguien vuelve a declarar el campo, los literales
    reales vuelven al JSON encolado.
    """
    for snapshot in (mysql_snapshot, postgres_snapshot):
        payload = SnapshotPayload.from_snapshot(snapshot)
        dumped = payload.to_json().replace(" ", "")
        assert '"query_sample_text"' not in dumped


# La huella del pipeline: queries de driver y de explain, no de aplicacion.
_PIPELINE_PREFIXES = (
    "set names",
    "use ",
    "select schema",
    "select ?",
    "set `autocommit`",
    "rollback",
    "explain format = json",
)


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_goldens_have_application_workload(snapshot_fixture, request):
    """El golden tiene que traer trafico de aplicacion, no la huella del collector.

    Este es el invariante que faltaba y por eso un golden vacio pasaba: con dos
    queries del driver y sus explains sin operaciones, el test de conteos
    comparaba 0 contra 0. Exigir volumen y variedad de operaciones sobre tablas
    reales hace imposible que eso vuelva a colarse.
    """
    raw = request.getfixturevalue(snapshot_fixture)

    assert len(raw["statements"]) >= 8, (
        f"solo {len(raw['statements'])} statements: el golden se genero sin carga"
    )
    assert len(raw["canonic_explains"]) >= 5, (
        f"solo {len(raw['canonic_explains'])} explains: no hay material que"
        f"verificar en los normalizadores"
    )

    total_operations = sum(
        len(e["canonical_plan"]["physical_operations"])
        for e in raw["canonic_explains"]
    )
    assert total_operations >= 20, (
        f"solo {total_operations} operaciones canonicas: los planes no se"
        f"materializaron"
    )

    candidates = raw["top_impact_queries"]
    assert len(candidates) >= 5

    app_queries = [
        c for c in candidates
        if not str(c.get("query_text") or "").strip().lower().startswith(
            _PIPELINE_PREFIXES
        )
    ]
    assert len(app_queries) >= 5, (
        f"solo {len(app_queries)} de {len(candidates)} candidatos son de"
        f" aplicacion: el resto es huella del pipeline"
    )

    explained_types = set()
    for explain in raw["canonic_explains"]:
        for operation in explain["canonical_plan"]["physical_operations"]:
            explained_types.add(operation["type"])
    assert {"scan"} <= explained_types
    assert len(explained_types) >= 4, (
        f"los planes solo alcanzan {sorted(explained_types)}: el golden no"
        f" ejercita los seis tipos de operacion"
    )


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_predicates_are_canonicalized(snapshot_fixture, request):
    """Un predicate canonico no lleva el schema de MySQL ni comillas: es lo que
    permite comparar planes entre motores. Si sobrevive la marca de schema, la
    limpieza de NormalizeStage no corrio sobre el snapshot."""
    raw = request.getfixturevalue(snapshot_fixture)
    checked = 0
    for explain in raw["canonic_explains"]:
        for operation in explain["canonical_plan"]["physical_operations"]:
            predicate = operation.get("predicate")
            if not predicate:
                continue
            checked += 1
            assert "`" not in predicate, f"quedan backticks: {predicate}"
            assert "ql_demo." not in predicate, f"queda el schema: {predicate}"
            assert "<cache>" not in predicate, f"queda la marca cache: {predicate}"
    assert checked > 0, "ningun predicate en el golden, no hay nada que verificar"


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
def test_candidate_with_plan_has_canonical_explain(snapshot_fixture, request):
    """Un candidato explicado llega a canonic_explains con plan materializado.

    Reemplaza al test que exigia que todo query_id de active_queries estuviera en
    statements. Esa dependencia desaparecio con el explain por via nativa: los
    candidatos salen de statements y el plan se obtiene del motor, sin cruzar
    contra pg_stat_activity. Exigir el cruce fijaba un contrato que ya no aplica.
    """
    raw = request.getfixturevalue(snapshot_fixture)
    by_id = {e["query_id"]: e for e in raw["canonic_explains"]}

    explained = [
        c for c in raw["top_impact_queries"] if c.get("query_id") in by_id
    ]
    assert explained, "ningun candidato tiene explain canonico"

    for candidate in explained:
        plan = by_id[candidate["query_id"]]["canonical_plan"]
        assert plan["physical_operations"], (
            f"{candidate['query_id']}: aparece en canonic_explains pero su "
            f"physical_operations esta vacio"
        )
        assert any(plan["logical_shape"].values()), (
            f"{candidate['query_id']}: plan vacio"
        )


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_canonical_explains_belong_to_candidates(snapshot_fixture, request):
    """canonic_explains no puede traer queries que no son candidatas: significaria
    que ExplainStagenership algo que select_candidates_to_explain descarto."""
    raw = request.getfixturevalue(snapshot_fixture)
    candidate_ids = {c["query_id"] for c in raw["top_impact_queries"]}
    for explain in raw["canonic_explains"]:
        assert explain["query_id"] in candidate_ids


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_explains_carry_engine_source(snapshot_fixture, request):
    """Postgres explica por plan generico y MySQL por muestra. El source lo dice,
    asi que un motor explains con la via del otro es detectable."""
    raw = request.getfixturevalue(snapshot_fixture)
    expected = "generic" if "postgres" in snapshot_fixture else "sample"
    for explain in raw["canonic_explains"]:
        assert explain["explain_source"] == expected


def test_active_queries_have_canonic_field(mysql_snapshot):
    """El golden de MySQL puede llegar sin sesiones activas (la bateria termina
    y no deja nada corriendo; una captura es un instante), como le pasa a los
    locks. Donde hay filas, el texto real nunca sobrevive: el campo query_text es
    opcional en el contrato y to_json lo emite como null cuando no hubo contexto;
    canonic_query lleva el texto normalizado o "Not available". La generacion en
    si se verifica en test_canonicalizers."""
    active = mysql_snapshot["active_queries"] or []
    assert isinstance(active, list)
    for row in active:
        assert row.get("query_text") is None
        assert "canonic_query" in row


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


def test_locks_boolean_coercion(mysql_snapshot, postgres_snapshot):
    """El golden de MySQL suele llegar sin locks (no habia transaccion InnoDB
    abierta al capturar), asi que el bucle sobre el no exertia presion. La
    coercion se verifica donde hay datos y el vacio se comprueba aparte.
    """
    payload = SnapshotPayload.from_snapshot(postgres_snapshot)
    assert payload.locks, (
        "el golden de postgres deberia traer locks: sin ellos este test no "
        "verifica la coercion de is_granted"
    )
    for lock in payload.locks:
        assert lock.is_granted is True or lock.is_granted is False
    assert all(lock.is_granted is not None for lock in payload.locks)

    # MySQL: el contrato tiene que aceptar la lista vacia sin inventar valores.
    vacio = SnapshotPayload.from_snapshot(mysql_snapshot)
    assert vacio.locks == []


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


@pytest.mark.parametrize(
    "snapshot_fixture",
    ["mysql_snapshot", "postgres_snapshot"],
)
def test_rows_carry_database_name(snapshot_fixture, request):
    """database_name atribuye cada fila a la base donde corrio, para que un
    snapshot server-wide no mezcle filas de bases distintas bajo un solo db_id.
    La columna debe viajar declarada y presente en cada seccion."""
    raw = request.getfixturevalue(snapshot_fixture)
    for stmt in raw["statements"]:
        assert "database_name" in stmt, stmt["query_id"]
    for lock in raw["locks"]:
        assert "database_name" in lock
    for active in raw["active_queries"] or []:
        assert "database_name" in active
    payload = SnapshotPayload.from_snapshot(raw)
    dumped = payload.to_json().replace(" ", "")
    assert '"database_name":' in dumped


def test_mysql_database_name_matches_schema(mysql_snapshot):
    """En MySQL no existe la distincion base/schema: la base del statement es su
    schema_name. No deben divergir o un consumidor leeria dos bases distintas."""
    for stmt in mysql_snapshot["statements"]:
        assert stmt["database_name"] == stmt["schema_name"]


def test_postgres_statements_attributed_to_target_database(postgres_snapshot):
    """El golden de Postgres sale de la bateria en ql_demo: toda fila debe
    estar atribuida a esa base, el sintoma del scope server-wide es justamente
    que aparezcan datnames de otras bases."""
    for stmt in postgres_snapshot["statements"]:
        assert stmt["database_name"] == "ql_demo"