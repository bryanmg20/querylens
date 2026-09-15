class EnrichStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats):
        return stats