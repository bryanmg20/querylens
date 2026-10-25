"""Muestra del ciclo para medir la calidad de la captura de abrazos mortales.

DeadlocksStage lee los informes que el motor escribe al revertir la victima;
este stage lee cuantos abrazos conto el motor. La diferencia entre los dos, en
la ventana del reporte, son los abrazos que QueryLens no vio (cli/share/report.sql).

Postgres: pg_stat_database.deadlocks de la base de la conexion, que es la
misma por la que DeadlocksStage filtra los informes. stats_reset y el arranque
del postmaster forman la epoca del contador: si cambia, el contador volvio a
cero. Ademas el listado de los archivos del log (PgLogTail, sin otra consulta):
el writer lo compara con el del ciclo anterior y sabe cuantos bytes se
escribieron entre ciclos. Si fueron mas que LOG_WINDOW_BYTES, la lectura de
este ciclo no alcanzo al final de la anterior y lo del medio no se leyo.

MySQL: lock_deadlocks de information_schema.INNODB_METRICS, de todo el
servidor (InnoDB no lo separa por schema), igual que los encabezados de
error_log que lee DeadlocksStage. No tiene epoca: un reinicio se ve porque el
contador baja.

El stage no calcula diferencias: no guarda estado entre ciclos. Deja la
muestra cruda en report.cycle_sample y health/writer.py la compara con la
anterior al volcarla.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from health.report import HealthReport
from logger import get_logger
from models.stats import Stats
from stages.pg_log import PgLogTail

logger = get_logger(__name__)

PG_DEADLOCKS_SQL = """
SELECT deadlocks,
       coalesce(stats_reset::text, '') || '|' || pg_postmaster_start_time()::text AS counter_epoch
FROM pg_stat_database
WHERE datname = current_database()
"""

# STATUS = 'disabled': el contador no avanza y la diferencia daria cero.
MYSQL_DEADLOCKS_SQL = """
SELECT `COUNT` AS deadlocks, STATUS AS status
FROM information_schema.INNODB_METRICS
WHERE NAME = 'lock_deadlocks'
"""


class EngineCountersStage:
    def __init__(self, collector, report: HealthReport | None = None):
        self.collector = collector
        self.report = report if report is not None else HealthReport()

    def execute(self, stats: Stats, conn, log: PgLogTail | None = None) -> Stats:
        dialect = self.collector.source_dialect
        if dialect not in ("postgres", "mysql"):
            return stats
        sample = {
            "taken_at": datetime.now(timezone.utc),
            "engine_deadlocks": None,
            "counter_epoch": None,
            "log_files": None,
        }
        try:
            # Transaccion corta, como DeadlocksStage: si falta un permiso, el
            # rollback deja la conexion lista para lo que sigue.
            with conn.begin():
                if dialect == "postgres":
                    self._postgres(conn, sample)
                else:
                    self._mysql(conn, sample)
        except Exception as e:
            logger.info(f"{dialect} | ENGINE_COUNTERS | contador de abrazos no disponible | {e}")
            self.report.add("DEADLOCK_COUNTER_UNAVAILABLE", section="deadlocks")
        if dialect == "postgres":
            try:
                with conn.begin():
                    log = log if log is not None else PgLogTail()
                    sample["log_files"] = [
                        {"name": log_file["name"], "size": int(log_file["size"] or 0)}
                        for log_file in log.listing(conn)
                    ]
            except Exception as e:
                # DeadlocksStage ya dejo DEADLOCK_REPORTS_UNAVAILABLE por lo mismo
                logger.info(f"{dialect} | ENGINE_COUNTERS | listado del log no disponible | {e}")
        self.report.cycle_sample = sample
        return stats

    def _postgres(self, conn, sample: dict) -> None:
        row = next(iter(conn.execute(text(PG_DEADLOCKS_SQL)).mappings()), None)
        if row is None:
            self.report.add("DEADLOCK_COUNTER_UNAVAILABLE", section="deadlocks")
            return
        sample["engine_deadlocks"] = int(row["deadlocks"])
        sample["counter_epoch"] = row["counter_epoch"]

    def _mysql(self, conn, sample: dict) -> None:
        row = next(iter(conn.execute(text(MYSQL_DEADLOCKS_SQL)).mappings()), None)
        if row is None or str(row["status"]).lower() != "enabled":
            self.report.add("DEADLOCK_COUNTER_UNAVAILABLE", section="deadlocks")
            return
        sample["engine_deadlocks"] = int(row["deadlocks"])
