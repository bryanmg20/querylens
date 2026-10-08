import json

from sqlalchemy import text

from logger import get_logger
from models.stats import Stats
from stages import explain_normalizer as en
# is_single_statement se re-exporta: tests y mutantes lo importan desde aqui.
from stages.sql_text import is_explainable_command, is_single_statement  # noqa: F401

logger = get_logger(__name__)

EXPLAIN_NORMALIZERS = en.EXPLAIN_NORMALIZERS


class ExplainStage:
    def __init__(self, collector):
        self.collector = collector
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
        plan solo puede armarse en el contexto real de cada base."""
        if self.collector.source_dialect != "postgres":
            return conn
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

        from config.connections import postgres_connect_args

        engine = self._foreign_engines.get(database_name)
        if engine is not None:
            return engine.connect()
        url = self.collector.engine.url.set(database=database_name)
        if self._foreign_engine_factory is not None:
            engine = self._foreign_engine_factory(url)
        else:
            engine = create_engine(
                url, connect_args=postgres_connect_args(), poolclass=NullPool
            )
        self._foreign_engines[database_name] = engine
        return engine.connect()

    def _dispose_foreign_engines(self):
        for engine in self._foreign_engines.values():
            engine.dispose()
        self._foreign_engines.clear()

    def execute(self, stats: Stats, conn):
        stats["query_explain"] = []

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
                if dialect == "mysql" and not context_sql:
                    logger.warning(
                        f"mysql | EXPLAIN | query_id={query_id} "
                        "| skipped: no schema context"
                    )
                    continue

                active = self._connection_for(query, conn)
                try:
                    # Transaccion corta por candidato (M-12): el collect ya hizo
                    # COMMIT sobre esta conexion; el BEGIN...COMMIT aísla el
                    # contexto (SET LOCAL / USE) + EXPLAIN. En Postgres SET LOCAL
                    # es transaccional y se deshace al cerrar; en MySQL el USE es
                    # estado de sesion y sobrevive al COMMIT.
                    with active.begin():
                        if context_sql:
                            active.execute(text(context_sql))

                        if dialect == "postgres":
                            result = active.execute(
                                text(f"EXPLAIN (GENERIC_PLAN, FORMAT JSON) {query_text}")
                            )
                            stats["query_explain"].append({
                                "query_id": query_id,
                                "explain_source": "generic",
                                "plan": [dict(row) for row in result.mappings()],
                            })
                        else:
                            result = active.execute(text(f"EXPLAIN FORMAT=JSON {query_text}"))
                            plan_row = next(result.mappings(), None)
                            if plan_row is None:
                                logger.error(f"mysql | EXPLAIN | query_id={query_id} | returned no plan row")
                                continue
                            stats["query_explain"].append({
                                "query_id": query_id,
                                "explain_source": "sample",
                                "plan": json.loads(plan_row["EXPLAIN"]),
                            })
                except Exception as e:
                    logger.error(f"{dialect} | EXPLAIN | query_id={query_id} | {e}")
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

        normalizer = EXPLAIN_NORMALIZERS[self.collector.source_dialect]()
        normalizer.normalize(stats)
        stats.pop("query_explain", None)

        return stats