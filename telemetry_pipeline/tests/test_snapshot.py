"""Validadores de SnapshotPayload.

Los BeforeValidator de models/snapshot.py son la frontera donde el snapshot deja
de ser un dict arbitrario y pasa a ser el contrato que consume el pipeline de
analisis. Un validador que deja pasar lo incorrecto no falla ruidosamente: el
analizador recibe is_granted como string o blocking_pids como string y falla
mas tarde, en un sitio que ya no apunta al origen.

Por eso cada test de esta archivo afirma sobre el valor ya convertido, no sobre
que la llamada no levante excepcion.
"""
import pytest

from datetime import datetime, timedelta, timezone

from models.snapshot import (
    ActiveQueryRow,
    LockRow,
    SnapshotPayload,
    StatementCandidate,
)

pytestmark = pytest.mark.contract


def _base(**overrides):
    """Snapshot minimo valido. Cada test cambia solo el campo que le importa."""
    snapshot = {
        "db_id": "querylens-db-01",
        "statements": [],
        "top_impact_queries": [],
        "non_explainable_candidates": [],
        "locks": [],
        "active_queries": [],
        "indexes": [],
        "tables": [],
        "columns": [],
        "stats_reset_timestamp": [],
        "canonic_explains": [],
    }
    snapshot.update(overrides)
    return snapshot


class TestListOrNone:
    """_list_or_empty: None es 'no se pudo recolectar', no 'lista vacia'.

    El collector deja None cuando una query de telemetry falla. Distinguir eso de
    una seccion genuinamente vacia importa: vacio significa 'no hay nada', None
    significa 'no lo sabemos'.
    """

    @pytest.mark.parametrize(
        "section",
        ["statements", "locks", "active_queries", "canonic_explains"],
    )
    def test_none_section_becomes_empty_list(self, section):
        payload = SnapshotPayload.from_snapshot(_base(**{section: None}))
        assert getattr(payload, section) == []

    def test_populated_section_is_preserved(self):
        rows = [{
            "query_id": 1,
            "query_text": "SELECT 1",
            "execution_count": 3,
            "rows_returned": 3,
        }]
        payload = SnapshotPayload.from_snapshot(_base(statements=rows))
        assert payload.statements[0].query_text == "SELECT 1"
        assert payload.statements[0].query_id == 1

    def test_missing_section_is_rejected(self):
        """Asimetria deliberada: None significa 'el collector fallo y no sabemos',
        y se convierte en lista vacia para no romper el payload. Una seccion
        ausente del todo es un snapshot mal formado y se rechaza.

        El collector siempre emite sus 8 claves (collect.py las escribe o las
        pone en None), asi que en produccion no ocurre. Las 3 claves derivadas
        (top_impact_queries, non_explainable_candidates, canonic_explains) si
        pueden faltar legitimamente: Orchestrator salta esas stages cuando
        statements viene en None (ver test_derived_sections_default...).
        Fijarlo evita que alguien vuelva las secciones opcionales y deje pasar
        un snapshot truncado en silencio, que es el fallo que este archivo
        previene.
        """
        raw = _base()
        del raw["locks"]
        with pytest.raises(Exception):
            SnapshotPayload.from_snapshot(raw)

    def test_collector_output_shape_is_accepted(self):
        """La forma que el collector produce de verdad: once claves, con None
        donde fallo la query."""
        raw = _base(locks=None, active_queries=None)
        payload = SnapshotPayload.from_snapshot(raw)
        assert payload.locks == []
        assert payload.active_queries == []

    def test_derived_sections_default_when_stage_skipped(self):
        """Default de las 3 claves derivadas: si statements vino en None,
        Orchestrator no corre CandidatesStage/ExplainStage y esas claves no
        existen en la fuente. Eso no debe invalidar el snapshot: el default
        [] las completa. Las 8 claves del collector, en cambio, siguen yendo
        escritas por CollectStage (o en None), nunca ausentes."""
        raw = _base(statements=None)
        del raw["top_impact_queries"]
        del raw["non_explainable_candidates"]
        del raw["canonic_explains"]
        payload = SnapshotPayload.from_snapshot(raw)
        assert payload.top_impact_queries == []
        assert payload.non_explainable_candidates == []
        assert payload.canonic_explains == []


class TestLockCoercion:
    """_to_bool: InnoDB reporta el estado como texto, el contrato lo quiere bool."""

    def test_granted_becomes_true(self):
        assert LockRow(process_id=1, lock_mode="X", is_granted="GRANTED").is_granted is True

    def test_waiting_becomes_false(self):
        assert LockRow(process_id=1, lock_mode="X", is_granted="WAITING").is_granted is False

    @pytest.mark.parametrize("raw", ["granted", "Granted", "GRANTED "])
    def test_case_and_space_tolerant(self, raw):
        """MySQL no garantiza la caja; un granted en minusculas no debe
        degradarse a string ni a None."""
        assert LockRow(process_id=1, lock_mode="X", is_granted=raw).is_granted is True

    @pytest.mark.parametrize("raw", ["waiting", "WAITING"])
    def test_waiting_case_insensitive(self, raw):
        assert LockRow(process_id=1, lock_mode="X", is_granted=raw).is_granted is False

    def test_already_bool_passes_through(self):
        assert LockRow(process_id=1, lock_mode="X", is_granted=True).is_granted is True
        assert LockRow(process_id=1, lock_mode="X", is_granted=False).is_granted is False

    def test_unknown_string_is_rejected(self):
        """Un estado que no es GRANTED ni WAITING no puede convertirse a False:
        seria indistinguible de un lock realmente en espera."""
        with pytest.raises(Exception):
            LockRow(process_id=1, lock_mode="X", is_granted="UNKNOWN")

    def test_end_to_end_in_snapshot(self):
        payload = SnapshotPayload.from_snapshot(
            _base(locks=[{"process_id": 7, "lock_mode": "X", "is_granted": "WAITING"}])
        )
        assert payload.locks[0].is_granted is False
        assert isinstance(payload.locks[0].is_granted, bool)


class TestBlockingPids:
    """_to_int_list: pg_blocking_pids devuelve un csv de texto o null."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", [1]),
            ("1,2", [1, 2]),
            ("1, 2,3", [1, 2, 3]),
            (None, []),
        ],
    )
    def test_coercion(self, raw, expected):
        row = ActiveQueryRow(process_id=1, query_id="q", blocking_pids=raw)
        assert row.blocking_pids == expected

    def test_non_numeric_entries_are_dropped(self):
        """Postgres puede devolver entradas que no son pid; un pid no numerico
        no puede entrar en el contrato, que es list[int]."""
        assert ActiveQueryRow(
            process_id=1, query_id="q", blocking_pids="1,abc,3"
        ).blocking_pids == [1, 3]

    def test_empty_string_gives_empty_list(self):
        assert ActiveQueryRow(process_id=1, query_id="q", blocking_pids="").blocking_pids == []

    def test_list_passes_through(self):
        assert ActiveQueryRow(process_id=1, query_id="q", blocking_pids=[4, 5]).blocking_pids == [4, 5]

    def test_every_element_is_int(self):
        row = ActiveQueryRow(process_id=1, query_id="q", blocking_pids="10,20")
        assert all(isinstance(pid, int) for pid in row.blocking_pids)


class TestReadyForExplainDefault:
    """El default del contrato es False. Si el codigo lo invirtiera, un candidato
    sin verificar llegaria a EXPLAIN como si estuviera listo."""

    def test_default_is_false(self):
        row = StatementCandidate(
            query_id=1,
            query_text="SELECT 1",
            execution_count=1,
            rows_returned=1,
        )
        assert row.ready_for_explain is False

    def test_default_applies_without_explicit_field(self):
        """El campo no debe venir en el payload de init_ready_for_explain para que
        el default sea el que decide."""
        row = StatementCandidate.model_validate({
            "query_id": 1,
            "query_text": "SELECT 1",
            "execution_count": 1,
            "rows_returned": 1,
        })
        assert row.ready_for_explain is False

    def test_true_is_preserved_when_set_explicitly(self):
        row = StatementCandidate(
            query_id=1,
            query_text="SELECT 1",
            execution_count=1,
            rows_returned=1,
            ready_for_explain=True,
        )
        assert row.ready_for_explain is True

    def test_serialized_json_carries_false(self):
        """to_json usa exclude_none=False, asi que False debe aparecer. Si el
        default se perdiera en la serializacion, el consumidor no podria
        distinguir 'no verificado' de 'no medido'."""
        payload = SnapshotPayload.from_snapshot(
            _base(top_impact_queries=[{
                "query_id": 1,
                "query_text": "SELECT 1",
                "execution_count": 1,
                "rows_returned": 1,
            }])
        )
        assert '"ready_for_explain":false' in payload.to_json().replace(" ", "")


class TestValidationIsNotBypassed:
    """from_snapshot tiene que validar de verdad: model_construct saltaria los
    validadores y dejaria pasar el snapshot sin convertir."""

    def test_wrong_statements_type_is_rejected(self):
        with pytest.raises(Exception):
            SnapshotPayload.from_snapshot(_base(statements={"not": "a list"}))

    def test_missing_required_field_is_rejected(self):
        with pytest.raises(Exception):
            SnapshotPayload.from_snapshot(_base(db_id=None))

    def test_unknown_physical_operation_is_rejected(self):
        """El Literal de PhysicalOperation es la parte que el fix del normalizer
        hizo cumplir: un tipo fuera del contrato no puede entrar."""
        with pytest.raises(Exception):
            SnapshotPayload.from_snapshot(_base(canonic_explains=[{
                "query_id": 1,
                "canonical_plan": {
                    "logical_shape": {},
                    "physical_operations": [{"type": "window_function"}],
                    "estimates": {},
                },
            }]))


class TestSerialization:
    def test_none_values_are_kept(self):
        """exclude_none=False: el consumidor necesita distinguir 'null' de
        'campo ausente' para saber si faltó una recoleccion. Se comprueba con un
        campo que el collector si emite con valor None."""
        payload = SnapshotPayload.from_snapshot(_base(statements=[{
            "query_id": 1,
            "query_text": "SELECT 1",
            "execution_count": 3,
            "rows_returned": 3,
            "schema_name": None,
        }]))
        assert '"schema_name":null' in payload.to_json().replace(" ", "")

    def test_aware_datetime_is_normalized_to_utc_naive(self):
        tz_bogota = timezone(timedelta(hours=-5))
        payload = SnapshotPayload.from_snapshot(_base(indexes=[{
            "schema_name": "public",
            "table_name": "tabla_medicion",
            "index_name": "tabla_medicion_pkey",
            "last_index_scan": datetime(2026, 1, 1, 7, 0, tzinfo=tz_bogota),
        }]))
        assert payload.indexes[0].last_index_scan == "2026-01-01 12:00:00.000000"

    def test_json_round_trips(self):
        payload = SnapshotPayload.from_snapshot(
            _base(statements=[{
                "query_id": "abc",
                "query_text": "SELECT 1",
                "execution_count": 5,
                "rows_returned": 5,
            }])
        )
        again = SnapshotPayload.from_snapshot(payload.model_dump())
        assert again.statements[0].query_id == "abc"

    def test_unknown_keys_are_ignored(self):
        """extra='ignore': query_explain es interno del pipeline y no debe
        romper la validacion si sobrevive en el dict."""
        payload = SnapshotPayload.from_snapshot(
            _base(statements=[], query_explain=[{"plan": "raw"}])
        )
        assert payload.db_id == "querylens-db-01"
