# Reporte en CSV a partir del JSON de report.sql (jq -r): una fila por
# problema, en el orden por impacto del reporte. severidad es la mas alta del
# rango; severidad_actual, la de la ultima deteccion. impacto_ms es la suma
# del impacto de las detecciones del rango (vacio si no se pudo medir). base es
# donde ocurrio (en MySQL, el schema). La columna evidencia solo sale con
# --include-evidence. nombre es el del patron en espanol (el del front);
# antipatron, su identificador interno, para filtrar o cruzar datos.
(.nombres // {}) as $patterns
| (.hallazgos | any(has("evidencia") and .evidencia != null)) as $ev
| ["severidad", "severidad_actual", "codigo", "nombre", "antipatron", "impacto_ms", "base", "tabla", "query_id",
   "indice", "columna",
   "veces", "primera", "ultima",
   "altas", "medias", "bajas", "veces_historico", "primera_historica",
   "explicacion", "recomendacion", "riesgo"] + (if $ev then ["evidencia"] else [] end),
  (.hallazgos[]
   | [.severidad, .severidad_actual, .codigo, ($patterns[.antipatron] // .antipatron), .antipatron, .impacto_ms, .database_name, .table_name, .query_id,
      .indice, .columna,
      .veces, .primera, .ultima,
      (.por_severidad.high // 0), (.por_severidad.medium // 0), (.por_severidad.low // 0),
      .veces_historico, .primera_historica,
      .explicacion, .recomendacion, .riesgo]
     + (if $ev then [.evidencia | if . == null then null else tojson end] else [] end))
| @csv
