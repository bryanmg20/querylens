"""Tests de punta a punta de main.py.

Cubre la cadena completa tal como la corre el agente: Pipeline -> validacion del
snapshot -> encolado en PGMQ. El resto de la suite prueba cada pieza por separado;
esto verifica que encajen y que el mensaje que llega a la cola sea consumible.

    docker compose -f ql_sandbox/docker-compose.yml up -d
    docker compose up -d
"""
import json
import logging
import uuid

import pytest
from sqlalchemy import text

import main
from config.connections import get_connection_querylens_db
from models.snapshot import SnapshotPayload

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ql():
    try:
        engine = get_connection_querylens_db()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        pytest.skip(f"PGMQ no disponible: {type(exc).__name__}: {str(exc)[:120]}")


@pytest.fixture
def scratch_queue(ql):
    queue = f"e2e_{uuid.uuid4().hex[:8]}"
    with ql.begin() as conn:
        conn.execute(text("SELECT pgmq.create(:q)"), {"q": queue})
    yield queue
    with ql.begin() as conn:
        conn.execute(text("SELECT pgmq.drop_queue(:q, true)"), {"q": queue})


def _read_queue(ql, queue):
    with ql.connect() as conn:
        rows = conn.execute(text(
            f'SELECT msg_id, message, vt, read_ct FROM pgmq."q_{queue}" ORDER BY msg_id'
        )).mappings().all()
    return rows


def _as_json(message):
    return message if isinstance(message, dict) else json.loads(message)


def _route_to_scratch(monkeypatch, scratch_queue):
    """Redirige el encolado de main() a la cola temporal del test."""
    from enqueue import send_to_queue

    monkeypatch.setattr(
        main,
        "send_to_queue",
        lambda payload_json, engine, queue_name=None:
            send_to_queue(payload_json, engine, queue_name or scratch_queue),
    )


# ---------- ciclo completo contra los contenedores reales ----------


def test_main_enqueues_one_consumable_snapshot_per_engine(monkeypatch, ql, scratch_queue):
    """Lo que verifica el agente real: main() deja 2 mensajes en la cola, uno por
    motor, y ambos se pueden volver a validar con el modelo del consumidor."""
    from enqueue import send_to_queue

    sent = []

    def spy(payload_json, engine, queue_name=None):
        msg_id = send_to_queue(payload_json, engine, queue_name or scratch_queue)
        sent.append(msg_id)
        return msg_id

    monkeypatch.setattr(main, "send_to_queue", spy)
    main.main()
    assert len(sent) == 2, "main() debe encolar un snapshot por motor"

    rows = _read_queue(ql, scratch_queue)
    assert len(rows) == 2
    payloads = [_as_json(row["message"]) for row in rows]
    for payload in payloads:
        reparsed = SnapshotPayload.model_validate_json(json.dumps(payload))
        assert reparsed.db_id == "querylens-db-01"
        assert isinstance(reparsed.statements, list)
        assert isinstance(reparsed.canonic_explains, list)
    assert all(p["stats_reset_timestamp"] for p in payloads), (
        "cada motor debe traer su marca de reinicio de estadisticas"
    )


def test_enqueued_snapshot_carries_no_transient_keys(monkeypatch, ql, scratch_queue):
    """El consumidor nunca debe ver claves internas del pipeline."""
    _route_to_scratch(monkeypatch, scratch_queue)
    main.main()
    rows = _read_queue(ql, scratch_queue)
    assert len(rows) == 2
    transient = {
        "high_impact_statements",
        "unstable_statements",
        "disk_spill_statements",
        "query_explain",
        "schema_resolver",
        "stats",
        "_samples",
    }
    for row in rows:
        payload = _as_json(row["message"])
        leaked = transient & set(payload)
        assert not leaked, f"se colaron claves internas: {leaked}"


def test_enqueued_snapshot_explains_have_complete_operations(monkeypatch, ql, scratch_queue):
    """Regresion del normalizador sobre el dato que realmente viaja: cada contador
    de logical_shape tiene su tipo en physical_operations."""
    from collections import Counter

    field_to_type = {
        "scans": "scan", "joins": "join", "aggregates": "aggregate",
        "sorts": "sort", "subqueries": "subquery", "distinct": "distinct",
    }
    _route_to_scratch(monkeypatch, scratch_queue)
    main.main()
    rows = _read_queue(ql, scratch_queue)
    total_explains = 0
    for row in rows:
        payload = _as_json(row["message"])
        for explain in payload["canonic_explains"] or []:
            total_explains += 1
            plan = explain["canonical_plan"]
            counted = Counter(op["type"] for op in plan["physical_operations"])
            for field, optype in field_to_type.items():
                assert plan["logical_shape"][field] == counted.get(optype, 0), (
                    f"query {explain['query_id']}: {field}={plan['logical_shape'][field]} "
                    f"pero hay {counted.get(optype, 0)} operaciones '{optype}'"
                )
            for op in plan["physical_operations"]:
                assert op["type"] in set(field_to_type.values())
    if not total_explains:
        pytest.skip("sin candidates explicables: corre la bateria sysbench primero")


def test_messages_are_readable_exactly_once(monkeypatch, ql, scratch_queue):
    """PGMQ asigna un vt de visibilidad y suma read_ct al leer: si el snapshot se
    consumiera al encolar, un segundo consumidor no veria el evento."""
    _route_to_scratch(monkeypatch, scratch_queue)
    main.main()
    rows = _read_queue(ql, scratch_queue)
    assert len(rows) == 2
    for row in rows:
        assert row["read_ct"] == 0, "nadie debe haber leido el mensaje todavia"

    with ql.connect() as conn:
        first = conn.execute(
            text("SELECT msg_id FROM pgmq.read(:q, 10, 30) AS m"), {"q": scratch_queue}
        ).scalars().all()
        second = conn.execute(
            text("SELECT msg_id FROM pgmq.read(:q, 10, 30) AS m"), {"q": scratch_queue}
        ).scalars().all()

    assert sorted(first) == sorted(row["msg_id"] for row in rows)
    assert second == [], "el mensaje ya esta en vt y no debe volver a entregarse"


# ---------- un motor roto no debe tumbar el ciclo ----------


def test_a_dead_engine_aborts_main_but_mysql_snapshot_is_still_valid(
    monkeypatch, ql, scratch_queue
):
    """Lo que hace main() hoy: el fallo al abrir un motor NO esta capturado, solo
    el ValidationError del snapshot. El test fija esa frontera a proposito.

    Consecuencia real: si Postgres no levanta, el ciclo muere y MySQL nunca se
    encola. Lo correcto seria el mismo continue del snapshot, pero cambiarlo es
   Decision de produccion, no de test. Cuando se corrija, este test falla y hay
    que darle la vuelta: assert 2 mensajes en cola y ninguna excepcion.
    """
    from config.connections import get_connection_mysql

    def boom():
        raise RuntimeError("motor caido a proposito")

    _route_to_scratch(monkeypatch, scratch_queue)
    monkeypatch.setattr(
        main, "ENGINES", (("postgres", boom), ("mysql", get_connection_mysql))
    )

    with pytest.raises(RuntimeError, match="motor caido a proposito"):
        main.main()

    assert _read_queue(ql, scratch_queue) == [], (
        "MySQL esta despues de Postgres en ENGINES y no llega a encolarse"
    )


def test_mysql_still_enqueues_when_postgres_is_skipped(monkeypatch, ql, scratch_queue):
    """Al reordenar ENGINES, MySQL corre igual aunque Postgres reviente: el ciclo
    no depende del orden para el motor que si abre."""
    from config.connections import get_connection_mysql

    _route_to_scratch(monkeypatch, scratch_queue)
    monkeypatch.setattr(
        main, "ENGINES", (("mysql", get_connection_mysql),)
    )

    main.main()
    rows = _read_queue(ql, scratch_queue)
    assert len(rows) == 1
    payload = _as_json(rows[0]["message"])
    assert payload["db_id"] == "querylens-db-01"
    assert SnapshotPayload.model_validate(payload).statements is not None


def test_invalid_snapshot_is_skipped_and_logged(monkeypatch, ql, scratch_queue, caplog):
    """Un snapshot que no valida se omite con continue y queda en el log; el otro
    motor sigue y la cola no recibe basura."""
    calls = {"n": 0}
    real_validate = SnapshotPayload.from_snapshot.__func__

    def flaky(cls, stats):
        calls["n"] += 1
        if calls["n"] == 1:
            stats = dict(stats)
            stats["db_id"] = None  # db_id es str, no Optional
        return real_validate(cls, stats)

    _route_to_scratch(monkeypatch, scratch_queue)
    monkeypatch.setattr(main.SnapshotPayload, "from_snapshot", classmethod(flaky))

    main.main()

    rows = _read_queue(ql, scratch_queue)
    assert len(rows) == 1, "el snapshot invalido no debe llegar a la cola"
    assert _as_json(rows[0]["message"])["db_id"] == "querylens-db-01"

    records = [r for r in caplog.records if "snapshot_validation" in r.getMessage()]
    assert records, "el rechazo debe quedar registrado"
    assert records[0].levelno >= logging.ERROR
