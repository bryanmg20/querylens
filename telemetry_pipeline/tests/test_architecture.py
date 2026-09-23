import pytest
from pydantic import ValidationError

from collectors.base import DB_Engine_Collector
from collectors.factory import Engine_Factory
from collectors.mysql.collector import Mysql_Collector
from collectors.postgres.collector import Postgres_Collector
from models.snapshot import SnapshotPayload
from orchestrator import Orchestrator
from stages import selectors
from stages.candidates import CandidatesStage
from stages.collect import CollectStage
from stages.enrich import EnrichStage
from stages.explain import ExplainStage
from stages.explain_normalizer import PostgresExplainNormalizer, MysqlExplainNormalizer, EXPLAIN_NORMALIZERS
from stages.log_backfill import LogsBackfillStage
from stages.normalize import NormalizeStage

pytestmark = pytest.mark.contract


class TestCollectorsStrategy:
    def test_both_collectors_implement_base(self):
        assert issubclass(Mysql_Collector, DB_Engine_Collector)
        assert issubclass(Postgres_Collector, DB_Engine_Collector)

    @pytest.mark.parametrize(
        "cls,dialect",
        [(Mysql_Collector, "mysql"), (Postgres_Collector, "postgres")],
    )
    def test_common_interface(self, cls, dialect):
        collector = cls(engine=None)
        assert collector.source_dialect == dialect
        assert isinstance(collector.queries, dict)
        assert collector.queries.keys() >= {
            "indexes", "tables", "statements", "locks",
            "active_queries", "stats_reset_timestamp", "columns",
        }
        assert callable(collector.preprocess_statements)
        assert callable(collector.normalize_engine_artifacts)

    def test_preprocess_hook_specialized_by_strategy(self):
        mysql = Mysql_Collector(engine=None)
        pg = Postgres_Collector(engine=None)
        assert "calculate_stddev_coeff" in type(mysql).__dict__
        assert "calculate_stddev_coeff" not in type(pg).__dict__


class TestEngineFactory:
    def test_factory_creates_correct_strategy(self):
        assert isinstance(Engine_Factory.create_collector("postgres", None), Postgres_Collector)
        assert isinstance(Engine_Factory.create_collector("mysql", None), Mysql_Collector)

    def test_factory_rejects_unknown_dialect(self):
        with pytest.raises(ValueError):
            Engine_Factory.create_collector("oracle", None)


class TestExplainNormalizerRegistry:
    def test_registry_covers_both_dialects(self):
        assert set(EXPLAIN_NORMALIZERS.keys()) == {"postgres", "mysql"}

    def test_registered_normalizers_satisfy_interface(self):
        for normalizer_cls in EXPLAIN_NORMALIZERS.values():
            normalizer = normalizer_cls()
            assert callable(normalizer.normalize)
        assert EXPLAIN_NORMALIZERS["postgres"] is PostgresExplainNormalizer
        assert EXPLAIN_NORMALIZERS["mysql"] is MysqlExplainNormalizer


class TestPipelineStages:
    @pytest.mark.parametrize(
        "stage_cls",
        [CollectStage, CandidatesStage, ExplainStage, NormalizeStage, EnrichStage, LogsBackfillStage],
    )
    def test_every_stage_exposes_execute(self, stage_cls):
        collector = DB_Engine_Collector()
        assert callable(stage_cls(collector).execute)

    def test_orchestrator_composes_all_stages_in_order(self):
        orchestrator = Orchestrator(DB_Engine_Collector())
        assert isinstance(orchestrator.collect, CollectStage)
        assert isinstance(orchestrator.candidates, CandidatesStage)
        assert isinstance(orchestrator.log_backfill, LogsBackfillStage)
        assert isinstance(orchestrator.explain, ExplainStage)
        assert isinstance(orchestrator.normalize, NormalizeStage)
        assert isinstance(orchestrator.enrich, EnrichStage)


class TestSnapshotFacade:
    def test_round_trip_preserves_payload(self, mysql_snapshot):
        payload = SnapshotPayload.from_snapshot(mysql_snapshot)
        restored = SnapshotPayload.model_validate_json(payload.to_json())
        assert restored.db_id == payload.db_id
        assert len(restored.statements) == len(payload.statements)

    def test_to_json_is_enqueueable(self, mysql_snapshot):
        payload = SnapshotPayload.from_snapshot(mysql_snapshot)
        assert payload.to_json().startswith("{")
        assert '"db_id"' in payload.to_json()

    def test_facade_rejects_invalid_payload(self):
        with pytest.raises(ValidationError):
            SnapshotPayload.from_snapshot({"db_id": "x", "statements": {"not": "list"}})


class TestDeadCodeVitals:
    def test_selector_functions_composed_by_stage(self):
        for name in (
            "select_high_impact_time_statements",
            "select_unstable_statements",
            "select_disk_spill_indicator",
            "select_candidates_to_explain",
            "select_explain_ready",
        ):
            assert callable(getattr(selectors, name))

    def test_base_strategy_normalize_returns_stats_unchanged(self):
        collector = DB_Engine_Collector()
        stats = {"k": "v"}
        assert collector.normalize_engine_artifacts(stats) is stats