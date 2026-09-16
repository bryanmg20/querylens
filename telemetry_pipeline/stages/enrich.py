DB_ID = "querylens-db-01"


class EnrichStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats):
        stats["db_id"] = DB_ID
        stats["source"] = self.collector.source_dialect
        return stats