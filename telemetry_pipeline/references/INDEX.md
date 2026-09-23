# Índice de referencias

Recopilación de documentación externa y decisiones del pipeline, antes mezcladas en `references.md`, separadas por tema.

| Archivo | Contenido |
|---------|-----------|
| [001_postgresql_views.md](001_postgresql_views.md) | Vistas de catálogo de PostgreSQL que alimentan la colección (`pg_stat_*`, `pg_locks`) |
| [002_antipatrones_senales.md](002_antipatrones_senales.md) | Catálogo de 8 anti-patrones + deadlocks + fuentes de las señales de selección (Señal 1-5) |
| [003_banco_pruebas_simulacion.md](003_banco_pruebas_simulacion.md) | Cómo generar carga y ver queries en el banco de pruebas (`simulate_queries.sh`, contenedores) |
| [004_decisiones_contrato.md](004_decisiones_contrato.md) | Decisiones de contrato del snapshot (query_id, disk_spill, avg_rows, source) |
| [005_evaluacion_alternativas_query_real.md](005_evaluacion_alternativas_query_real.md) | Evaluación de alternativas (sección 9): recuperación de la query real por tabla de actividad vs logs del servidor |