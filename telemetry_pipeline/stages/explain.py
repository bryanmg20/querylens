import json

from sqlalchemy import text

from activity.sampler import usable_sample_text
from health.classify import classify
from health.report import HealthReport
from logger import get_logger
from models.stats import Stats
from stages import explain_normalizer as en
# is_single_statement se re-exporta: tests y mutantes lo importan desde aqui.
from stages.sql_text import is_explainable_command, is_single_statement  # noqa: F401

logger = get_logger(__name__)

EXPLAIN_NORMALIZERS = en.EXPLAIN_NORMALIZERS


class ExplainStage:
    def __init__(
        self,
        collector,
        report: HealthReport | None = None,
        samples=None,
        db_id: str | None = None,
    ):
        self.collector = collector
        self.report = report if report is not None else HealthReport()
        # ActivitySampleBuffer del runner (solo Postgres). None (main.py, tests,
        # sandbox sin db_id): todo candidato usa el plan generico, como antes.
        self.samples = samples
        self.db_id = db_id
        self._foreign_engines = {}
        # Seam para tests: inyecta un builder de "engine" (url -> engine) para
        # probar el ruteo sin abrir conexiones reales contra otra base.
        self._foreign_engine_factory = None

    @staticmethod
    def _search_path_sql(schema_name, engine):
        if not schema_name:
            return "SET LOCAL search_path TO DEFAULT"
        quoted = engine.dialect.identifier_preparer.quote(str(schema_name))
        return f"SET LOCAL search_path TO {quoted}"

    @staticmethod
    def _connected_database(engine):
        return (engine.url.database or "").strip() or None

    @staticmethod
    def _schema_context_sql(schema_name, database_name, dialect, engine):
        if dialect == "postgres":
            return ExplainStage._search_path_sql(schema_name, engine)
        context = schema_name or database_name
        if context:
            quoted = engine.dialect.identifier_preparer.quote(str(context))
            return f"USE {quoted}"
        return None

    def _connection_for(self, query, conn):
        """Un candidato de Postgres que vive en otra base (database_name != la
        base conectada del target) se explica en una conexion dedicada a esa
        base: una conexion de Postgres no puede cambiar de base con USE, el
        plan solo puede armarse en el contexto real de cada base.

        MySQL sin schema (la app corrio la query sin base por defecto) va a una
        sesion nueva sin base: la conexion compartida arrastra el USE del
        candidato anterior y armaria el plan de una query sin calificar contra
        otra base. Sin base, la query calificada (base.tabla) se explica igual
        que corrio, y la sin calificar falla como fallo en la app."""
        if self.collector.source_dialect != "postgres":
            if query.get("schema_name") or query.get("database_name"):
                return conn
            return self._foreign_connection("")
        database_name = query.get("database_name")
        if not database_name:
            return conn
        connected_db = self._connected_database(self.collector.engine)
        if database_name == connected_db:
            return conn
        return self._foreign_connection(database_name)

    def _foreign_connection(self, database_name):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        from config.connections import mysql_connect_args, postgres_connect_args

        engine = self._foreign_engines.get(database_name)
        if engine is not None:
            return engine.connect()
        url = self.collector.engine.url.set(database=database_name)
        if self._foreign_engine_factory is not None:
            engine = self._foreign_engine_factory(url)
        else:
            connect_args = (
                postgres_connect_args()
                if self.collector.source_dialect == "postgres"
                else mysql_connect_args()
            )
            engine = create_engine(url, connect_args=connect_args, poolclass=NullPool)
        self._foreign_engines[database_name] = engine
        return engine.connect()

    def _dispose_foreign_engines(self):
        for engine in self._foreign_engines.values():
            engine.dispose()
        self._foreign_engines.clear()

    def _sample_text(self, query):
        """Texto real del buffer para este candidato, o None.

        La puerta de solo-lectura se re-aplica aqui aunque el sampler ya la
        paso: este es el texto que de verdad llega al EXPLAIN."""
        if self.samples is None or self.db_id is None:
            return None
        sample = self.samples.lookup(self.db_id, query.get("database_name"), query.get("query_id"))
        if sample is None or not usable_sample_text(sample.query_text):
            return None
        return sample.query_text

    @staticmethod
    def _run_plan(conn, context_sql, explain_sql):
        """Transaccion corta (M-12): SET LOCAL + EXPLAIN y se cierra."""
        with conn.begin():
            if context_sql:
                conn.execute(text(context_sql))
            result = conn.execute(text(explain_sql))
            return [dict(row) for row in result.mappings()]

    def _explain_postgres(self, conn, query, context_sql):
        """(plan, explain_source). Con sample del buffer, el plan de esa
        ejecucion real; si no hay o su EXPLAIN falla (la tabla cambio desde que
        se vio), el generico de pg_stat_statements."""
        query_id = query.get("query_id")
        sample_text = self._sample_text(query)
        if sample_text is not None:
            try:
                # text() lee ":nombre" como bind param; en un literal del sample
                # ('10:30') tiene que llegar tal cual. "\:" lo escapa.
                escaped = sample_text.replace(":", "\\:")
                return self._run_plan(conn, context_sql, f"EXPLAIN (FORMAT JSON) {escaped}"), "sample"
            except Exception as e:
                # Solo el tipo: el mensaje de Postgres puede citar literales del sample.
                logger.debug(
                    f"postgres | EXPLAIN sample | query_id={query_id} "
                    f"| {type(e).__name__} | uso el plan generico"
                )
        plan = self._run_plan(
            conn, context_sql, f"EXPLAIN (GENERIC_PLAN, FORMAT JSON) {query.get('query_text')}"
        )
        return plan, "generic"

    def execute(self, stats: Stats, conn):
        stats["query_explain"] = []
        failures = 0

        try:
            for query in stats.get("top_impact_queries", []):
                query_id = query.get("query_id")
                if query_id is None or not query.get("ready_for_explain"):
                    continue

                dialect = self.collector.source_dialect
                query_text = (
                    query.get("query_text")
                    if dialect == "postgres"
                    else query.get("query_sample_text")
                )
                # El selector filtro por query_text, pero MySQL explica
                # query_sample_text: la puerta de solo-lectura se re-aplica al
                # texto que de verdad llega al EXPLAIN.
                if not is_explainable_command(query_text, dialect):
                    logger.warning(
                        f"{dialect} | EXPLAIN | query_id={query_id} "
                        "| skipped: not a read-only SELECT/WITH"
                    )
                    continue

                if not is_single_statement(query_text, dialect):
                    logger.warning(
                        f"{dialect} | EXPLAIN | query_id={query_id} "
                        "| skipped multi-statement query"
                    )
                    continue

                context_sql = self._schema_context_sql(
                    query.get("schema_name"),
                    query.get("database_name"),
                    dialect,
                    self.collector.engine,
                )
                try:
                    active = self._connection_for(query, conn)
                except Exception as e:
                    # Base ajena sin CONNECT (u otra falla al abrirla): se
                    # pierde este candidato, no el resto del ciclo.
                    failures += 1
                    logger.error(f"{dialect} | EXPLAIN | query_id={query_id} | connect | {e}")
                    code, params = classify(e, dialect=dialect, scope="explain", phase="connect")
                    self.report.add(code, **params)
                    continue
                try:
                    # Transaccion corta por candidato (M-12): el collect ya hizo
                    # COMMIT sobre esta conexion; el BEGIN...COMMIT aísla el
                    # contexto (SET LOCAL / USE) + EXPLAIN. En Postgres SET LOCAL
                    # es transaccional y se deshace al cerrar; en MySQL el USE es
                    # estado de sesion y sobrevive al COMMIT.
                    if dialect == "postgres":
                        plan, source = self._explain_postgres(active, query, context_sql)
                        stats["query_explain"].append({
                            "query_id": query_id,
                            "explain_source": source,
                            "plan": plan,
                        })
                        continue
                    with active.begin():
                        if context_sql:
                            active.execute(text(context_sql))
                        result = active.execute(text(f"EXPLAIN FORMAT=JSON {query_text}"))
                        plan_row = next(result.mappings(), None)
                        if plan_row is None:
                            logger.error(f"mysql | EXPLAIN | query_id={query_id} | returned no plan row")
                            failures += 1
                            self.report.add("EXPLAIN_FAILED")
                            continue
                        stats["query_explain"].append({
                            "query_id": query_id,
                            "explain_source": "sample",
                            "plan": json.loads(plan_row["EXPLAIN"]),
                        })
                except Exception as e:
                    logger.error(f"{dialect} | EXPLAIN | query_id={query_id} | {e}")
                    failures += 1
                    # Agrupado por code (params.count): nunca query_id ni texto.
                    code, params = classify(e, dialect=dialect, scope="explain")
                    self.report.add(code, **params)
                finally:
                    # N-2: la conexion extranjera (abierta con engine.connect()
                    # en _foreign_connection) no la cierra ningun `with`: el
                    # `with active.begin()` cierra la TRANSACCION, no la
                    # conexion, y dispose() no toca lo enganchado. Hay que
                    # cerrarla explicitamente. El guard es clave: conn (la
                    # conexion compartida del ciclo) la cierra el orchestrator.
                    if active is not conn:
                        active.close()
        finally:
            self._dispose_foreign_engines()

        # Hubo candidatos y ninguno dio plan sin que nada fallara: todos se
        # filtraron (DML, multi-statement). Es informativo, no un error.
        had_candidates = stats.get("top_impact_queries") or stats.get("non_explainable_candidates")
        if had_candidates and not stats["query_explain"] and not failures:
            self.report.add("NO_EXPLAINABLE_CANDIDATES")
        self.report.checked("explain")

        normalizer = EXPLAIN_NORMALIZERS[self.collector.source_dialect]()
        normalizer.normalize(stats)
        stats.pop("query_explain", None)

        return stats