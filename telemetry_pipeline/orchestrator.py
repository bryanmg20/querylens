from models.stats import Stats
from stages.candidates import CandidatesStage
from stages.collect import CollectStage
from stages.enrich import EnrichStage
from stages.explain import ExplainStage
from stages.normalize import NormalizeStage


class Orchestrator:
    def __init__(self, collector):
        self.collector = collector
        self.collect = CollectStage(collector)
        self.candidates = CandidatesStage(collector)
        self.explain = ExplainStage(collector)
        self.normalize = NormalizeStage(collector)
        self.enrich = EnrichStage(collector)

    def run_pipeline(self) -> Stats:
        stats: Stats = {}
        with self.collector.engine.connect() as conn:
            self.collect.execute(stats, conn)
            # M-12: cerrar ya la transaccion de lectura. El snapshot no necesita
            # la conexion del target abierta mas alla del collect; mantenerla
            # abierta retiene vacuum/snapshot en Postgres y undo/MVCC en MySQL
            # durante el EXPLAIN y el encolado.
            conn.commit()
            if stats.get("statements") is not None:
                self.candidates.execute(stats)
                self.explain.execute(stats, conn)
                self.normalize.execute(stats)
            self.enrich.execute(stats)
        self.collector.stats = stats
        return stats