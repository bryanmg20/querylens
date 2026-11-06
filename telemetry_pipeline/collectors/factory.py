from collectors.base import DB_Engine_Collector

class Engine_Factory:
    @staticmethod
    def create_collector(db_type, engine) -> 'DB_Engine_Collector':
        if db_type == "postgres":
            from collectors.postgres.collector import Postgres_Collector
            return Postgres_Collector(engine)
        elif db_type == "mysql":
            from collectors.mysql.collector import Mysql_Collector
            return Mysql_Collector(engine)
        else:
            raise ValueError(f"Unsupported database type: {db_type}")