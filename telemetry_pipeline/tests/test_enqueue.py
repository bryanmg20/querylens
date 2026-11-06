import pytest

from enqueue import send_to_queue

pytestmark = pytest.mark.unit


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _MappingResult([self._row])


class _FakeConn:
    def __init__(self, msg_id=7):
        self.sent = []
        self.params = None
        self._msg_id = msg_id

    def execute(self, stmt, params=None):
        self.sent.append(str(stmt))
        self.params = params
        return _FakeResult({"msg_id": self._msg_id})


class _FakeEngine:
    def __init__(self, msg_id=7):
        self.conn = _FakeConn(msg_id)
        self.begin_calls = 0

    def begin(self):
        self.begin_calls += 1
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def test_returns_the_message_id_returned_by_pgmq():
    engine = _FakeEngine(msg_id=4242)
    assert send_to_queue('{"db_id": "x"}', engine) == 4242


def test_uses_a_transaction_from_the_engine():
    engine = _FakeEngine()
    send_to_queue('{"db_id": "x"}', engine)
    assert engine.begin_calls == 1


def test_calls_pgmq_send_with_the_payload_cast_to_jsonb():
    engine = _FakeEngine()
    send_to_queue('{"db_id": "x"}', engine)
    sql = engine.conn.sent[0]
    assert "pgmq.send" in sql
    assert "CAST(:payload AS JSONB)" in sql


def test_defaults_to_analyze_job_queue():
    engine = _FakeEngine()
    send_to_queue('{"db_id": "x"}', engine)
    assert engine.conn.params["queue_name"] == "analyze_job"


def test_queue_name_is_a_bound_parameter_not_interpolated():
    engine = _FakeEngine()
    send_to_queue('{"db_id": "x"}', engine, queue_name="other_queue")
    assert engine.conn.params["queue_name"] == "other_queue"
    assert "other_queue" not in engine.conn.sent[0]


def test_payload_is_passed_as_a_bound_parameter():
    payload = '{"db_id": "x", "statements": []}'
    engine = _FakeEngine()
    send_to_queue(payload, engine)
    assert engine.conn.params["payload"] == payload
    assert payload not in engine.conn.sent[0]
