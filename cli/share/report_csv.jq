# Reporte en CSV a partir del JSON de report.sql (jq -r): una fila por
# hallazgo agrupado. La columna evidencia solo sale con --include-evidence.
(.hallazgos | any(has("evidencia") and .evidencia != null)) as $ev
| ["severidad", "antipatron", "tabla", "query_id", "veces", "primera", "ultima",
   "explicacion", "recomendacion"] + (if $ev then ["evidencia"] else [] end),
  (.hallazgos[]
   | [.severidad, .antipatron, .table_name, .query_id, .veces, .primera, .ultima,
      .explicacion, .recomendacion]
     + (if $ev then [.evidencia | if . == null then null else tojson end] else [] end))
| @csv
