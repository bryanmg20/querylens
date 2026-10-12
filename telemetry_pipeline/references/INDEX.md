# Índice de referencias

Recopilación de documentación externa y decisiones del pipeline, antes mezcladas en `references.md`, separadas por tema.

| Archivo | Contenido |
|---------|-----------|
| [001_postgresql_views.md](001_postgresql_views.md) | Vistas de catálogo de PostgreSQL que alimentan la colección (`pg_stat_*`, `pg_locks`) |
| [002_antipatrones_senales.md](002_antipatrones_senales.md) | Catálogo de 8 anti-patrones + deadlocks + fuentes de las señales de selección (Señal 1-5) |
| [003_banco_pruebas_simulacion.md](003_banco_pruebas_simulacion.md) | Cómo generar carga y ver queries en el banco de pruebas (`simulate_queries.sh`, contenedores) |
| [004_decisiones_contrato.md](004_decisiones_contrato.md) | Decisiones de contrato del snapshot (query_id, disk_spill, avg_rows, source, schema_name, db_id, counters_epoch, plan canónico, validación previa) |
| [005_evaluacion_alternativas_query_real.md](005_evaluacion_alternativas_query_real.md) | Evaluación de alternativas (sección 9): recuperación de la query real por tabla de actividad vs logs del servidor |
| [006_instalacion_auto_explain.md](006_instalacion_auto_explain.md) | Guía para el DBA: activar `auto_explain` + log JSON y crear las funciones `querylens.log_files()` / `querylens.read_log()` (Postgres self-hosted) |
| [007_problemas_plan_canonico.md](007_problemas_plan_canonico.md) | Revisión 2026-10-12 desde los detectores: problemas del plan canónico (bitmap, tipos de acceso, condiciones perdidas, subconsultas, joins, estimaciones), de la redacción y candidatos sin ejecución, con la solución propuesta en el pipeline; batería en `ql_sandbox/explain_predicados/` |
