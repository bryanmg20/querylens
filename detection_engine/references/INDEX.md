# Índice de referencias del detection_engine

Decisiones de diseño y propuestas del motor de detección.

| Archivo | Contenido |
|---------|-----------|
| [001_ciclo_vida_hallazgos.md](001_ciclo_vida_hallazgos.md) | Propuesta (sin implementar): hallazgos abiertos/cerrados en vez de una fila por ventana; resuelve que AP-01 deje de avisar a las 5 ventanas de una degradación permanente |
| [002_ap07_alcance_sentencias.md](002_ap07_alcance_sentencias.md) | Propuesta (sin decidir): que AP-07 recorra todos los statements y solo el trabajo de consulta (sin mantenimiento); evidencia del sandbox, opciones e impacto en los escenarios |
| [003_redaccion_patron_like.md](003_redaccion_patron_like.md) | Pedido del detector: redactar el patrón de LIKE conservando los comodines de los extremos (`'%$1'`, `'$1%'`) para que AP-04 índice ausente distinga prefijo de sufijo |
| [004_ap08_filas_misma_ejecucion.md](004_ap08_filas_misma_ejecucion.md) | Decisión (implementada sin respaldo): AP-08 compara la estimación con las filas de la misma ejecución (`actual_rows`); mediciones de cobertura en MySQL, costo de `log_analyze` en Postgres y limitaciones a evaluar |
