from logger import get_logger
from models.stats import Stats
from stages.canonicalizers import redact_active_queries
from stages.candidates import CandidatesStage
from stages.collect import CollectStage
from stages.enrich import EnrichStage
from stages.explain import ExplainStage
from stages.normalize import NormalizeStage

logger = get_logger(__name__)


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
            else:
                # Q3: una falla de la seccion statements degrada el snapshot en
                # silencio; se avisa para que no parezca una seccion legitima vacia.
                logger.warning(
                    "orchestrator | statements no disponibles | se omiten "
                    "candidates/explain/normalize"
                )
                # Q4: la reja de privacidad corre siempre. Sin statements no hay
                # normalizacion, pero active_queries.no puede viajar con texto real.
                redact_active_queries(stats)
            self.enrich.execute(stats)
        self.collector.stats = stats
        return stats