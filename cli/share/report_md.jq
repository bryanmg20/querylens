# Reporte en Markdown a partir del JSON de report.sql (jq -r).
{"high": 0, "medium": 1, "low": 2} as $order
| {"high": "Alta", "medium": "Media", "low": "Baja"} as $names
| def sev: $names[.] // .;
  def ts: if . == null then "-" else .[0:16] | sub("T"; " ") end;
  def cell: tostring | gsub("\\|"; "\\|") | gsub("\n"; " ");
  def num: if . == null then "sin dato" else tostring end;
"# Reporte de hallazgos: \(.base.connection_name)",
"",
"- Base: `\(.base.database_name)` (\(.base.engine)), id `\(.base.database_identifier)`",
"- Rango: \(.desde | ts) a \(.hasta | ts)",
"- Generado: \(.generado | ts)",
"",
"## Resumen",
"",
if .resumen.hallazgos == 0 then
  "Sin hallazgos en el rango."
else
  "\(.resumen.hallazgos) hallazgos distintos (\(.resumen.detecciones) detecciones en total).",
  "",
  "| Severidad | Hallazgos |",
  "|---|---|",
  (.resumen.por_severidad | to_entries | sort_by($order[.key] // 9)[]
   | "| \(.key | sev) | \(.value) |"),
  "",
  "| Antipatrón | Hallazgos |",
  "|---|---|",
  (.resumen.por_antipatron | to_entries[] | "| \(.key | cell) | \(.value) |"),
  "",
  "## Detalle",
  (.hallazgos | group_by($order[.severidad] // 9)[]
   | "",
     "### Severidad \(.[0].severidad | sev)",
     (.[]
      | "",
        "#### \(.antipatron)\(if .table_name then " en `\(.table_name)`" else "" end)",
        "",
        (if .query_id then "- query_id: `\(.query_id)`" else empty end),
        "- Detectado \(.veces) \(if .veces == 1 then "vez" else "veces" end), entre \(.primera | ts) y \(.ultima | ts)",
        "",
        "**Explicación:** \(.explicacion)",
        "",
        "**Recomendación:** \(.recomendacion)",
        (if .evidencia != null then "", "Evidencia:", "", "```json", (.evidencia | tojson), "```" else empty end))
  )
end
,
"",
"## Calidad de la captura de abrazos mortales",
"",
(.base.engine as $engine
 | .calidad_deadlocks as $q
 | if $q.ciclos == 0 then
     "Sin muestras del contador en el rango: no se puede comparar."
   else
     "| Medida | Valor |",
     "|---|---|",
     "| Abrazos que contó el motor | \($q.hubo | num) |",
     "| Capturados por QueryLens | \($q.capturados) (\($q.con_detalle) con detalle, \($q.sin_detalle) sin detalle) |",
     "| Perdidos | \($q.perdidos | num) |",
     (if ($q.capturados_de_mas // 0) > 0 then "| Capturados de más (borde del rango o reinicio) | \($q.capturados_de_mas) |" else empty end),
     (if $q.reinicios_contador > 0 then "| Reinicios del contador | \($q.reinicios_contador) |" else empty end),
     "| Ciclos medidos | \($q.ciclos) |",
     # solo Postgres lee los abrazos del log
     (if $engine == "postgresql" then
        "| Ciclos con la ventana del log desbordada | \($q.ciclos_con_desborde) |",
        "| Ritmo del log, promedio / máximo (KB/s) | \($q.log_kb_s_promedio | num) / \($q.log_kb_s_max | num) |"
      else empty end),
     "| Hueco máximo entre ciclos (s) | \($q.hueco_max_s | num) |",
     (if $q.huecos_largos > 0 then "| Huecos de más de 120 s | \($q.huecos_largos) |" else empty end),
     "",
     (if ($q.por_patron | length) > 0 then
        "| Patrón (capturados con detalle) | Abrazos |",
        "|---|---|",
        ($q.por_patron | to_entries[] | "| \(.key | cell) | \(.value) |"),
        ""
      else empty end),
     (if $q.alcance == "servidor" then
        "En MySQL el contador y los encabezados de abrazos son de todo el servidor, no solo de esta base.",
        ""
      else empty end),
     "CT-05 (cascada por transacción larga) no es un abrazo mortal: no entra en esta cuenta. En los bordes del rango puede haber un abrazo de diferencia."
   end)
