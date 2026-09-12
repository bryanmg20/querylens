from abc import ABC, abstractmethod
import json 

class DB_Engine_Collector(ABC):
    @abstractmethod
    def collect_telemetry(self):
        pass

    
    def select_high_impact_time_statements(self):      
            self.stats['high_impact_statements'] = self.stats['statements'][:10]
    
    def select_unstable_statements(self):
            self.stats['unstable_statements'] = [stmd for stmd in self.stats.get('statements', []) if stmd.get('coeff_of_variation') is not None and stmd['coeff_of_variation'] > 2 and stmd.get('mean_time_ms', 0) > 10]
      
    #def select_io_heavy_statements(self):
            #self.stats['io_heavy_statements'] = [stmd for stmd in self.stats.get('statements', []) if stmd['pct_shared_blocks_hit'] is not None and stmd.get('pct_shared_blocks_hit', 0) < 95 and stmd.get('shared_blocks_read', 0) > 999]
    
    def select_disk_spill_indicator(self):
            self.stats['disk_spill_statements'] = [stmd for stmd in self.stats.get('statements', []) if stmd.get('disk_spill_indicator', 0) > 0]

    def eliminate_querytext_active(self):
            for stmdt in self.stats.get("active_queries", []):
                del stmdt["query_text"]

    def select_candidates_to_explain(self):
        candidates = {}
        skipped_candidates = {}

        statement_groups = [
            ("time_high_impact", "high_impact_statements"),
            ("unstable", "unstable_statements"),
            ("disk_spill", "disk_spill_statements"),
        ]

        explainable_commands = (
            "SELECT"
        )

        for reason, stats_key in statement_groups:
            statements = self.stats.get(stats_key) or []

            for statement in statements:
                query_id = statement.get("query_id")
                query_text = statement.get("query_text")

                if query_id is None or not query_text:
                    continue

                normalized_query = query_text.strip().upper()

                target = candidates

                if not normalized_query.startswith(explainable_commands):
                    target = skipped_candidates

                if query_id not in target:
                    target[query_id] = {
                        **statement,
                        "selected_by": [],
                    }

                selected_by = target[query_id]["selected_by"]

                if reason not in selected_by:
                    selected_by.append(reason)

        self.stats["explain_candidates"] = list(candidates.values())
        self.stats["non_explainable_candidates"] = list(
            skipped_candidates.values()
        )

    def select_explain_ready(self):
        readys = {}

        for candidate in self.stats.get("explain_candidates", []):

            query_id = candidate.get('query_id')
            if query_id is not None:
                readys[query_id] = {**candidate, "real_query_found": False}

        for stmd in self.stats.get("active_queries", []):
            query_id = stmd.get("query_id")
            if query_id in readys:
                readys[query_id]["real_query_found"] = True
                readys[query_id]["query_text"] = stmd.get("query_text")

        self.stats["explain_candidates"] = list(readys.values())

    def anonimize_query_text(self):

        dict_statements = {
            item["query_id"]: {k: v for k, v in item.items() if k != "query_id"}
            for item in self.stats.get("statements", [])
        }
        for stmd in self.stats.get("explain_candidates", []):
            query_id = stmd.get("query_id")
            if query_id in dict_statements:
                stmd["query_text"] = dict_statements[query_id].get("query_text")
            
                
    def get_statements(self):
        return json.dumps(self.stats, indent=4, default=str)

    def get_query_explain(self):
        if 'query_explain' not in self.stats:
            return json.dumps([], indent=4, default=str)
        
        return json.dumps(self.stats.get('query_explain'), indent=4, default=str)

    def get_candidates(self):
        return json.dumps(self.stats.get('explain_candidates', []), indent=4, default=str)

    def get_non_explainable_candidates(self):
        return json.dumps(self.stats.get('non_explainable_candidates', []), indent=4, default=str)

    def get_active_queries(self):
        return json.dumps(self.stats.get('active_queries', []), indent=4, default=str)

    def get_stats_complete(self):
        with open('stats_complete.json', 'w') as f:
            json.dump(self.stats, f, indent=4, default=str)
