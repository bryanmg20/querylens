"""Arnes de mutation testing.

Cada mutante es una sustitucion de texto sobre codigo de produccion. El arnes
lo aplica, corre la suite y restaura el archivo. Un mutante que sobrevive es un
hueco real de cobertura: el codigo puede estar roto y los tests siguen verdes.
"""
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent
PY = str(PIPELINE / "venv" / "Scripts" / "python.exe")


def mut(path, old, new, name):
    return {
        "name": name,
        "file": PIPELINE / path,
        "old": old,
        "new": new,
    }


MUTANTS = [
    # ---- explain_normalizer: conteos y materializacion ----
    mut("stages/explain_normalizer.py",
        'if node_type in ("Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan"):',
        'if node_type in ("Seq Scan", "Index Scan", "Index Only Scan"):',
        "normalizer/pg: descartar Bitmap scans"),
    mut("stages/explain_normalizer.py",
        'elif node_type in ("Nested Loop", "Hash Join", "Merge Join"):',
        'elif node_type in ("Nested Loop", "Hash Join"):',
        "normalizer/pg: descartar Merge Join"),
    mut("stages/explain_normalizer.py",
        'elif node_type in ("Aggregate", "Group", "GroupAggregate"):',
        'elif node_type in ("Aggregate", "Group"):',
        "normalizer/pg: descartar GroupAggregate"),
    mut("stages/explain_normalizer.py",
        'elif node_type in ("Sort", "Incremental Sort"):',
        'elif node_type in ("Sort",):',
        "normalizer/pg: descartar Incremental Sort"),
    mut("stages/explain_normalizer.py",
        'elif node_type in ("Aggregate", "Group", "GroupAggregate"):\n            canonical_plan["logical_shape"]["aggregates"] += 1\n            operation = self._aggregate_operation(node)',
        'elif node_type in ("Aggregate", "Group", "GroupAggregate"):\n            canonical_plan["logical_shape"]["aggregates"] += 1\n            operation = None',
        "normalizer/pg: no materializar aggregate (caja vacia)"),
    mut("stages/explain_normalizer.py",
        'canonical_plan["logical_shape"]["sorts"] += 1\n            operation = self._sort_operation(node)',
        'canonical_plan["logical_shape"]["sorts"] += 1\n            operation = None',
        "normalizer/pg: no materializar sort"),
    mut("stages/explain_normalizer.py",
        'canonical_plan["logical_shape"]["distinct"] += 1\n            operation = self._distinct_operation(node)',
        'canonical_plan["logical_shape"]["distinct"] += 1\n            operation = None',
        "normalizer/pg: no materializar distinct"),
    mut("stages/explain_normalizer.py",
        'canonical_plan["logical_shape"]["joins"] += 1\n            operation = self._join_operation(node)',
        'canonical_plan["logical_shape"]["joins"] += 1\n            operation = None',
        "normalizer/pg: no materializar join"),
    mut("stages/explain_normalizer.py",
        'if operation["type"] == "join":\n                operation["predicate"] = (\n                    node.get("Join Filter")\n                    or node.get("Hash Cond")\n                    or node.get("Merge Cond")\n                )',
        'if False:\n                operation["predicate"] = (\n                    node.get("Join Filter")\n                    or node.get("Hash Cond")\n                    or node.get("Merge Cond")\n                )',
        "normalizer/pg: nunca extraer el predicado del join"),
    mut("stages/explain_normalizer.py",
        'operation["predicate"] = (\n                    node.get("Join Filter")\n                    or node.get("Hash Cond")\n                    or node.get("Merge Cond")\n                )',
        'operation["predicate"] = None',
        "normalizer/pg: predicado de join siempre None"),
    mut("stages/explain_normalizer.py",
        'if operation is None:\n                operation = self._subquery_operation(node)\n            else:\n                operations.append(operation)\n                operation = self._subquery_operation(node)',
        'if operation is None:\n                operation = self._subquery_operation(node)',
        "normalizer/pg: SubPlan no emite la segunda operacion"),
    mut("stages/explain_normalizer.py",
        'or "Subplan Name" in node',
        'or False',
        "normalizer/pg: ignorar Subplan Name"),
    mut("stages/explain_normalizer.py",
        'canonical_plan["logical_shape"]["subqueries"] += 1',
        'canonical_plan["logical_shape"]["subqueries"] += 2',
        "normalizer/pg: contar subqueries de mas"),
    mut("stages/explain_normalizer.py",
        'for child in node.get("Plans", []):',
        'for child in node.get("Plans", [])[:1]:',
        "normalizer/pg: no descender a todos los hijos"),
    mut("stages/explain_normalizer.py",
        'access_methods = {\n            "Seq Scan": "full_table_scan",',
        'access_methods = {\n            "Seq Scan": "ALL_SCAN",',
        "normalizer/pg: access_method equivocado"),
    mut("stages/explain_normalizer.py",
        '"Plan Rows": "estimated_rows",\n            "Filter": "predicate",',
        '"Total Cost": "estimated_rows",\n            "Filter": "predicate",',
        "normalizer/pg: leer Total Cost como estimated_rows"),
    mut("stages/explain_normalizer.py",
        'if "Total Cost" in node:\n            estimates["total_cost"] = to_number(node["Total Cost"])',
        'if "Total Cost" in node:\n            estimates["total_cost"] = None',
        "normalizer/pg: no extraer total_cost"),
    mut("stages/explain_normalizer.py",
        'for explain in stats.get("query_explain") or []:',
        'for explain in (stats.get("query_explain") or [])[:1]:',
        "normalizer/pg: procesar solo el primer explain"),

    # ---- explain_normalizer: MySQL ----
    mut("stages/explain_normalizer.py",
        'if key == "table" and isinstance(node, dict):',
        'if key == "table":',
        "normalizer/mysql: contar table sin verificar dict"),
    mut("stages/explain_normalizer.py",
        'elif key == "nested_loop":',
        'elif key == "NESTED_LOOP":',
        "normalizer/mysql: no detectar nested_loop"),
    mut("stages/explain_normalizer.py",
        'elif key == "grouping_operation" and isinstance(node, dict):',
        'elif key == "grouping_op":',
        "normalizer/mysql: no detectar grouping_operation"),
    mut("stages/explain_normalizer.py",
        'elif key == "ordering_operation" and isinstance(node, dict):',
        'elif key == "ordering_op":',
        "normalizer/mysql: no detectar ordering_operation"),
    mut("stages/explain_normalizer.py",
        'elif key == "duplicates_removal":',
        'elif key == "duplicates_removal_x":',
        "normalizer/mysql: no detectar duplicates_removal"),
    mut("stages/explain_normalizer.py",
        'elif key in ("subquery", "attached_subqueries", "dependent_subquery"):',
        'elif key in ("attached_subqueries", "dependent_subquery"):',
        "normalizer/mysql: no detectar subquery"),
    mut("stages/explain_normalizer.py",
        'operation["predicate"] = _flag(node, "join_condition")',
        'operation["predicate"] = None',
        "normalizer/mysql: no extraer join_condition"),
    mut("stages/explain_normalizer.py",
        'operation["predicate"] = _flag(node, "using_temporary_table")',
        'operation["predicate"] = None',
        "normalizer/mysql: no extraer using_temporary_table"),
    mut("stages/explain_normalizer.py",
        'operation["predicate"] = _flag(node, "using_filesort")',
        'operation["predicate"] = None',
        "normalizer/mysql: no extraer using_filesort"),
    mut("stages/explain_normalizer.py",
        'if value is False:',
        'if False:',
        "normalizer/mysql/_flag: flag False como texto"),
    mut("stages/explain_normalizer.py",
        'return str(value)',
        'return None',
        "normalizer/mysql/_flag: perder el valor de la flag"),
    mut("stages/explain_normalizer.py",
        'query_cost = cost_info.get("query_cost") if isinstance(cost_info, dict) else None',
        'query_cost = None',
        "normalizer/mysql: no extraer query_cost"),
    mut("stages/explain_normalizer.py",
        '"ALL": "full_table_scan",',
        '"ALL": "table_scan",',
        "normalizer/mysql: access_method equivocado"),
    mut("stages/explain_normalizer.py",
        '"eq_ref": "index_lookup",',
        '"eq_ref": None,',
        "normalizer/mysql: eq_ref mal clasificado"),
    mut("stages/explain_normalizer.py",
        'canonical_plan["logical_shape"]["scans"] += 1\n                operation = self._scan_operation(node)',
        'canonical_plan["logical_shape"]["scans"] += 1\n                operation = None',
        "normalizer/mysql: no materializar scan"),

    # ---- selectors ----
    mut("stages/selectors.py",
        "stats['high_impact_statements'] = stats['statements'][:10]",
        "stats['high_impact_statements'] = stats['statements'][:5]",
        "selectors: top 10 en vez de top 5"),
    mut("stages/selectors.py",
        "stmd['coeff_of_variation'] > 2 and stmd.get('mean_time_ms', 0) > 10",
        "stmd['coeff_of_variation'] > 2",
        "selectors:.sin filtro de mean_time"),
    mut("stages/selectors.py",
        "stmd.get('disk_spill_indicator', 0) > 0",
        "stmd.get('disk_spill_indicator', 0) >= 0",
        "selectors: derrame incluye los que no derraman"),
    mut("stages/selectors.py",
        'explainable_commands = (\n            "SELECT", "WITH", "INSERT", "UPDATE", "DELETE"\n        )',
        'explainable_commands = (\n            "SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "SET"\n        )',
        "selectors: SET pasa a ser explicable"),
    mut("stages/selectors.py",
        'normalized_query = query_text.strip().upper()',
        'normalized_query = query_text.strip()',
        "selectors: no normalizar a mayusculas"),
    mut("stages/selectors.py",
        'if query_id is None or not query_text:\n                    continue',
        'if query_id is None:\n                    continue',
        "selectors: aceptar candidatos sin query_text"),
    mut("stages/selectors.py",
        'stats.pop("high_impact_statements", None)',
        'pass',
        "selectors: no limpiar high_impact_statements"),
    mut("stages/selectors.py",
        'readys[query_id] = {**candidate, "ready_for_explain": False}',
        'readys[query_id] = {**candidate, "ready_for_explain": True}',
        "selectors: marcar ready_for_explain True"),
    mut("stages/selectors.py",
        'if query_id is not None:\n                readys[query_id] = {**candidate, "ready_for_explain": False}',
        'if query_id is not None:\n                readys[query_id] = candidate',
        "selectors: no inyectar ready_for_explain"),
    mut("stages/selectors.py",
        'if reason not in selected_by:\n                    selected_by.append(reason)',
        'selected_by.append(reason)',
        "selectors: selected_by con duplicados"),
    mut("stages/selectors.py",
        'stats["top_impact_queries"] = list(candidates.values())',
        'stats["top_impact_queries"] = list(candidates.values())[::-1]',
        "selectors: invertir el orden de candidatos"),
    mut("stages/selectors.py",
        'if query_id not in target:',
        'if query_id not in candidates:',
        "selectors: dedupe cruzado entre explicables y no explicables"),

    # ---- explain.py ----
    mut("stages/explain.py",
        'if query_id is None or not query.get("ready_for_explain"):\n                    continue',
        'if query_id is None:\n                    continue',
        "explain: ignorar ready_for_explain"),
    mut("stages/explain.py",
        'return ";" not in trimmed',
        'return True',
        "explain: aceptar multi-statement"),
    mut("stages/explain.py",
        'query.get("query_text")\n                    if dialect == "postgres"\n                    else query.get("query_sample_text")',
        'query.get("query_text") or query.get("query_sample_text")',
        "explain: usar query_text en ambos motores"),
    mut("stages/explain.py",
        'if not is_single_statement(query_text):',
        'if False:',
        "explain: no saltar multi-statement"),
    mut("stages/explain.py",
        'if context_sql:\n                            active.execute(text(context_sql))',
        'if False:\n                            active.execute(text(context_sql))',
        "explain: no aplicar search_path / USE"),
    mut("stages/explain.py",
        'with active.begin():',
        'if False:',
        "explain: sin transaccion corta por candidato"),
    mut("stages/explain.py",
        'text(f"EXPLAIN (GENERIC_PLAN, FORMAT JSON) {query_text}")',
        'text(f"EXPLAIN (FORMAT JSON) {query_text}")',
        "explain: postgres pierde GENERIC_PLAN"),
    mut("stages/explain.py",
        '"explain_source": "generic",',
        '"explain_source": "sample",',
        "explain: source de postgres incorrecto"),
    mut("stages/explain.py",
        '"explain_source": "sample",',
        '"explain_source": "generic",',
        "explain: source de mysql incorrecto"),
    mut("stages/explain.py",
        'normalizer.normalize(stats)\n        stats.pop("query_explain", None)',
        'normalizer.normalize(stats)',
        "explain: dejar query_explain en el snapshot"),
    mut("stages/explain.py",
        'stats.pop("query_explain", None)',
        'pass',
        "explain: no limpiar query_explain"),
    mut("stages/explain.py",
        'plan_row = next(result.mappings(), None)',
        'plan_row = None',
        "explain: mysql nunca lee la fila del plan"),
    mut("stages/explain.py",
        '"plan": json.loads(plan_row["EXPLAIN"]),',
        '"plan": {"query_block": {"table": {"table_name": "x", "access_type": "ALL"}}},',
        "explain: mysql devuelve plan fijo"),
    mut("stages/explain.py",
        'return "SET LOCAL search_path TO DEFAULT"',
        'return ""',
        "explain: search_path vacio sin schema"),
    mut("stages/explain.py",
        'if context:\n            quoted = engine.dialect.identifier_preparer.quote(str(context))\n            return f"USE {quoted}"',
        'if context:\n            return f"USE {context}"',
        "explain: USE sin quote"),

    # ---- collect.py ----
    mut("stages/collect.py",
        'stats[key] = None\n                logger.error',
        'stats[key] = []\n                logger.error',
        "collect: query fallida deja [] en vez de None"),
    mut("stages/collect.py",
        'except Exception as e:\n                conn.rollback()',
        'except Exception as e:\n                pass',
        "collect: no hacer rollback tras fallo"),
    mut("stages/collect.py",
        'stats[key] = [dict(row) for row in result.mappings()]',
        'stats[key] = list(result.mappings())',
        "collect: no materializar las filas"),
    mut("stages/collect.py",
        'for key, query in self.collector.queries.items():',
        'for key, query in list(self.collector.queries.items())[:1]:',
        "collect: recolectar solo la primera seccion"),
    mut("stages/collect.py",
        'for key, query in self.collector.queries.items():',
        'for key, query in reversed(list(self.collector.queries.items())):',
        "collect: iterar secciones al reves"),

    # ---- orchestrator.py ----
    mut("orchestrator.py",
        'if stats.get("statements") is not None:',
        'if stats.get("statements"):',
        "orchestrator: statements vacio dispara el pipeline"),
    mut("orchestrator.py",
        'conn.commit()',
        'pass',
        "orchestrator: no commit tras collect"),
    mut("orchestrator.py",
        'self.collector.stats = stats',
        'pass',
        "orchestrator: no guardar stats en el collector"),
    mut("orchestrator.py",
        'self.enrich.execute(stats)',
        'pass',
        "orchestrator: saltear EnrichStage"),
    mut("orchestrator.py",
        'self.normalize.execute(stats)',
        'pass',
        "orchestrator: saltear NormalizeStage"),
    mut("orchestrator.py",
        'self.explain.execute(stats, conn)',
        'pass',
        "orchestrator: saltear ExplainStage"),
    mut("orchestrator.py",
        'self.candidates.execute(stats)',
        'pass',
        "orchestrator: saltear CandidatesStage"),

    # ---- candidates.py ----
    mut("stages/candidates.py",
        'self.collector.mark_explainable(stats)',
        'pass',
        "candidates: saltear mark_explainable"),
    mut("stages/candidates.py",
        'selectors.init_ready_for_explain(stats)',
        'pass',
        "candidates: saltear init_ready_for_explain"),
    mut("stages/candidates.py",
        'selectors.select_candidates_to_explain(stats)',
        'pass',
        "candidates: saltear select_candidates_to_explain"),
    mut("stages/candidates.py",
        'self.collector.preprocess_statements(stats)',
        'pass',
        "candidates: saltear preprocess_statements"),
    mut("stages/candidates.py",
        'if self.collector.source_dialect == "postgres":\n            schema_resolver.resolve_statements_schema(stats)',
        'schema_resolver.resolve_statements_schema(stats)',
        "candidates: resolver schema tambien en mysql"),
    mut("stages/candidates.py",
        'if self.collector.source_dialect == "postgres":\n            schema_resolver.resolve_statements_schema(stats)',
        'pass',
        "candidates: no resolver schema en postgres"),

    # ---- schema_resolver.py ----
    mut("stages/schema_resolver.py",
        'schema_by_user.setdefault(user_id, resolved_schema)',
        'schema_by_user[user_id] = resolved_schema',
        "schema_resolver: la ultima fila gana"),
    mut("stages/schema_resolver.py",
        'for key in ("statements", "top_impact_queries", "non_explainable_candidates"):',
        'for key in ("statements",):',
        "schema_resolver: solo resolver statements"),
    mut("stages/schema_resolver.py",
        'stmt["schema_name"] = schema_by_user.get(user_id)',
        'stmt["schema_name"] = resolved_schema',
        "schema_resolver: schema equivocado"),
    mut("stages/schema_resolver.py",
        'stats.pop("schema_resolver", None)',
        'pass',
        "schema_resolver: dejar schema_resolver en el snapshot"),
    mut("stages/schema_resolver.py",
        'if not resolver_rows:\n        return stats',
        'if not resolver_rows:\n        return stats\n    stats["schema_resolver"] = resolver_rows',
        "schema_resolver: conservar las filas crudas"),

    # ---- normalize.py ----
    mut("stages/normalize.py",
        'stats = self.collector.normalize_engine_artifacts(stats)',
        'pass',
        "normalize: saltear normalize_engine_artifacts"),
    mut("stages/normalize.py",
        'canonicalizers.normalize_querytext_active(stats, self.collector.source_dialect)',
        'pass',
        "normalize: saltear normalize_querytext_active"),
    mut("stages/normalize.py",
        'clean_mysql_explain_predicate_dynamic(predicate)',
        'predicate',
        "normalize: no limpiar el predicado"),
    mut("stages/normalize.py",
        "if is_granted == \"GRANTED\":",
        "if is_granted == \"GRANTED_\":",
        "normalize: no mapear GRANTED"),
    mut("stages/normalize.py",
        "if is_granted == \"WAITING\":\n            lock[\"is_granted\"] = False",
        "if is_granted == \"WAITING\":\n            lock[\"is_granted\"] = True",
        "normalize: WAITING mapeado a True"),
    mut("stages/normalize.py",
        'if pid.strip().isdigit()]',
        ']',
        "normalize: no filtrar pids no numericos"),
    mut("stages/normalize.py",
        'if isinstance(ts, datetime):\n            stmt["transaction_start_time"] = _to_naive_utc(ts).isoformat(sep=" ", timespec="microseconds")',
        'if isinstance(ts, datetime):\n            pass',
        "normalize: no castear datetime"),
    mut("stages/normalize.py",
        'preprocessed = pred_text.replace(\'<cache>\', \'\').replace(\'</cache>\', \'\')',
        'preprocessed = pred_text',
        "normalize: no quitar las marcas cache"),
    mut("stages/normalize.py",
        'return float(value)',
        'return value',
        "normalize/_to_number: no castear a float"),

    # ---- enqueue.py ----
    mut("enqueue.py",
        'queue_name="analyze_job"',
        'queue_name="wrong_queue"',
        "enqueue: cola equivocada"),
    mut("enqueue.py",
        'with engine.begin() as conn:',
        'with engine.connect() as conn:',
        "enqueue: sin transaccion"),
    mut("enqueue.py",
        'return result.mappings().first()["msg_id"]',
        'return None',
        "enqueue: no devolver msg_id"),
    mut("enqueue.py",
        'CAST(:payload AS JSONB)',
        'CAST(:payload AS TEXT)',
        "enqueue: payload como TEXT"),

    # ---- main.py ----
    mut("main.py",
        '        logger.error(f"{dialect} | snapshot_validation | {e}")\n        return None',
        '        logger.error(f"{dialect} | snapshot_validation | {e}")',
        "main: romper ante un snapshot invalido (sin return None)"),
    mut("main.py",
        'payload_json = snapshot.to_json()',
        'payload_json = str(payload)',
        "main: encolar el dict crudo en vez del JSON"),
    mut("main.py",
        '    if db_id:\n        payload["db_id"] = db_id',
        '    if False:\n        payload["db_id"] = db_id',
        "main: ignorar el database_identifier de la fila"),
    mut("main.py",
        '        try:\n            run_engine(target.dialect, target.factory, target.db_id)\n        except Exception as e:\n            label = target.db_id or target.dialect\n            logger.error(f"{label} | engine_failed | {e}")',
        '        run_engine(target.dialect, target.factory, target.db_id)',
        "main: no aislar un target que revienta"),
    mut("main.py",
        '    finally:\n        _dispose(target_engine)\n        _dispose(querylens_engine)',
        '    finally:\n        pass',
        "main: no hacer dispose de los engines"),

    # ---- runner.py ----
    mut("runner.py",
        'self.stop.wait(max(0.0, started + self.interval - self.clock()))',
        'self.stop.wait(self.interval)',
        "runner: espera fija por ciclo (acumula ticks atrasados)"),
    mut("runner.py",
        '        if active == self._active:\n            return',
        '        pass',
        "runner: loguear el estado en cada ciclo"),
    mut("runner.py",
        '        self.backoff = min(self.backoff * 2, MAX_BACKOFF_S)',
        '        self.backoff = self.backoff',
        "runner: no subir el backoff tras un ciclo fallido"),

    # ---- main_sandbox.py ----
    mut("main_sandbox.py",
        'ENGINES = (\n    ("postgres", get_connection_postgres),\n    ("mysql", get_connection_mysql),\n)',
        'ENGINES = (\n    ("postgres", get_connection_postgres),\n)',
        "sandbox: dejar de procesar mysql"),
    mut("main_sandbox.py",
        'ENGINES = (\n    ("postgres", get_connection_postgres),\n    ("mysql", get_connection_mysql),\n)',
        'ENGINES = (\n    ("mysql", get_connection_mysql),\n)',
        "sandbox: dejar de procesar postgres"),

    # ---- enrich.py ----
    mut("stages/enrich.py",
        'stats["db_id"] = DB_ID',
        'stats["db_id"] = "wrong-db"',
        "enrich: db_id equivocado"),

    # ---- factory.py ----
    mut("collectors/factory.py",
        'if db_type == "postgres":',
        'if db_type == "postgresql":',
        "factory: no aceptar 'postgres'"),
    mut("collectors/factory.py",
        'return Postgres_Collector(engine)',
        'return Mysql_Collector(engine)',
        "factory: devolver el collector equivocado"),
    mut("collectors/factory.py",
        'raise ValueError(f"Unsupported database type: {db_type}")',
        'return None',
        "factory: no rechazar dialecto desconocido"),

    # ---- base.py ----
    mut("collectors/base.py",
        'def preprocess_statements(self, stats: Stats) -> Stats:\n        return stats',
        'def preprocess_statements(self, stats: Stats) -> Stats:\n        return {}',
        "base: preprocess vacio el stats"),

    # ---- snapshot.py ----
    mut("models/snapshot.py",
        'return [] if value is None else value',
        'return value',
        "snapshot: None no se convierte en lista"),
    mut("models/snapshot.py",
        'if normalized == "GRANTED":\n            return True',
        'if normalized == "GRANTED":\n            return False',
        "snapshot: GRANTED mapeado a False"),
    mut("models/snapshot.py",
        'return [int(pid) for pid in value.split(",") if pid.strip().isdigit()]',
        'return [int(value)]',
        "snapshot: blocking_pids no se parten"),
    mut("models/snapshot.py",
        'return cls.model_validate(stats)',
        'return cls.model_construct(**stats)',
        "snapshot: saltar la validacion"),
    mut("models/snapshot.py",
        'return self.model_dump_json(exclude_none=False)',
        'return self.model_dump_json(exclude_none=True)',
        "snapshot: excluir los None del JSON"),
    mut("models/snapshot.py",
        'if isinstance(dt, datetime) and dt.tzinfo is not None:\n        return dt.astimezone(timezone.utc).replace(tzinfo=None)',
        'if False:\n        return dt.astimezone(timezone.utc).replace(tzinfo=None)',
        "snapshot: no castear datetime a iso"),
    mut("models/snapshot.py",
        'ready_for_explain: bool = False',
        'ready_for_explain: bool = True',
        "snapshot: default de ready_for_explain True"),

    # ---- logger.py ----
    mut("logger.py",
        'logger.propagate = False',
        'logger.propagate = True',
        "logger: duplicar salida via root"),
    mut("logger.py",
        '    if _file_handler is not None:\n        return _file_handler',
        '    pass',
        "logger: reconstruir el handler en cada llamada"),
    mut("logger.py",
        'maxBytes=MAX_BYTES,\n            backupCount=BACKUP_COUNT,',
        'maxBytes=1024,\n            backupCount=1,',
        "logger: rotacion incorrecta"),
    mut("logger.py",
        'already_writing = any(',
        'already_writing = False or any(',
        "logger: anadir el handler siempre"),
]


def _read(path):
    """Lee preservando el fin de linea del archivo.

    Importa porque apply() y el restore de main() round-tripan el texto. Si se
    lee en modo texto y se escribe en modo texto, un archivo con CRLF en el
    working tree sale con LF, y git marca un cambio espurio en cada corrida
    aunque el contenido sea identico.
    """
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def run_suite(marker=None):
    cmd = [PY, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
    if marker:
        cmd += ["-m", marker]
    proc = subprocess.run(
        cmd, cwd=str(PIPELINE), capture_output=True, text=True, timeout=900
    )
    return proc.returncode, proc.stdout


def apply(m):
    """Aplica el mutante.

    Los patrones se escriben con \n porque son mas legibles en el codigo fuente
    de este archivo, pero los archivos del repo usan CRLF en el working tree.
    Normalizar el patron segun el fin de linea del archivo es lo que permite que
    el mismo mutante empareje en Windows y en Linux, sin duplicar cada patron.
    """
    src = _read(m["file"])
    old = m["old"]
    new = m["new"]
    if "\r\n" in src and "\n" in old:
        old = old.replace("\n", "\r\n")
        new = new.replace("\n", "\r\n")
    if old not in src:
        return None
    return src.replace(old, new, 1)


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None

    code, out = run_suite()
    if code != 0:
        print("La suite falla sin mutaciones. Arranco invalido:")
        print(out[-3000:])
        return 1
    print(f"Baseline OK: {out.strip().splitlines()[-1]}")
    print()

    total = 0
    killed = 0
    survived = []
    invalid = []

    for m in MUTANTS:
        if only and only not in m["name"]:
            continue
        total += 1
        original = _read(m["file"])
        mutated = apply(m)
        if mutated is None:
            invalid.append(m["name"])
            continue
        try:
            _write(m["file"], mutated)
            code, out = run_suite()
        finally:
            _write(m["file"], original)

        if code != 0:
            killed += 1
            status = "KILLED"
        else:
            survived.append(m["name"])
            status = "SURVIVED"

        print(f"{status:9} {m['name']}")

    print()
    print(f"Mutantes: {total} | muertos: {killed} | sobrevividos: {len(survived)}")
    if survived:
        print()
        print("SOBREVIVIERON (el codigo puede fallar y los tests no lo notan):")
        for name in survived:
            print(f"  - {name}")
    if invalid:
        print()
        print("INVALIDOS (el texto no coincide, revisar el mutante):")
        for name in invalid:
            print(f"  - {name}")

    return 0 if not survived and not invalid else 2


if __name__ == "__main__":
    sys.exit(main())
