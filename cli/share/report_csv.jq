# Reporte en CSV a partir del JSON de report.sql (jq -r): una fila por
# problema. severidad es la mas alta del rango; severidad_actual, la de la
# ultima deteccion. La columna evidencia solo sale con --include-evidence.
(.hallazgos | any(has("evidencia") and .evidencia != null)) as $ev
| ["severidad", "severidad_actual", "antipatron", "tabla", "query_id", "indice", "columna",
   "veces", "primera", "ultima",
   "altas", "medias", "bajas", "veces_historico", "primera_historica",
   "explicacion", "recomendacion"] + (if $ev then ["evidencia"] else [] end),
  (.hallazgos[]
   | [.severidad, .severidad_actual, .antipatron, .table_name, .query_id, .indice, .columna,
      .veces, .primera, .ultima,
      (.por_severidad.high // 0), (.por_severidad.medium // 0), (.por_severidad.low // 0),
      .veces_historico, .primera_historica,
      .explicacion, .recomendacion]
     + (if $ev then [.evidencia | if . == null then null else tojson end] else [] end))
| @csv
