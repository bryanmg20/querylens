from models.stats import Stats

DB_ID = "querylens-db-01"


class EnrichStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats: Stats) -> Stats:
        stats["db_id"] = DB_ID
        return stats