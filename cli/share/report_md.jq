# Reporte en Markdown a partir del JSON de report.sql (jq -r).
{"high": 0, "medium": 1, "low": 2} as $order
| {"high": "Alta", "medium": "Media", "low": "Baja"} as $names
| def sev: $names[.] // .;
  def ts: if . == null then "-" else .[0:16] | sub("T"; " ") end;
  def cell: tostring | gsub("\\|"; "\\|") | gsub("\n"; " ");
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
