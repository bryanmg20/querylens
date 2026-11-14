# Reporte en Markdown a partir del JSON de report.sql (jq -r).
{"high": 0, "medium": 1, "low": 2} as $order
| {"high": "Alta", "medium": "Media", "low": "Baja"} as $names
| (.nombres // {}) as $patterns
| def sev: $names[.] // .;
  # nombre en espanol del patron (report.sql, nombres); si falta, el interno
  def nombre: $patterns[.] // .;
  def ts: if . == null then "-" else .[0:16] | sub("T"; " ") end;
  def cell: tostring | gsub("\\|"; "\\|") | gsub("\n"; " ");
  def num: if . == null then "sin dato" else tostring end;
  # milisegundos en la unidad que se lee mejor
  def dur: if . == null then "sin medir"
           elif . >= 60000 then "\(. / 60000 * 10 | round / 10) min"
           elif . >= 1000 then "\(. / 1000 * 10 | round / 10) s"
           else "\(. * 10 | round / 10) ms" end;
"# Reporte de hallazgos: \(.base.connection_name)",
"",
"- Base: \(if .base.engine == "mysql" then "toda la base" else "`\(.base.database_name)`" end) (\(.base.engine)), id `\(.base.database_identifier)`",
"- Rango: \(.desde | ts) a \(.hasta | ts)",
"- Sesión de detección actual desde: \(.base.sesion_desde | ts)",
"- Generado: \(.generado | ts)",
"",
"## Resumen",
"",
if .resumen.hallazgos == 0 then
  "Sin hallazgos en el rango."
else
  "\(.resumen.hallazgos) problemas distintos (\(.resumen.detecciones) detecciones en el rango).",
  "",
  "Cada problema cuenta con la severidad más alta que tuvo en el rango.",
  "",
  "| Severidad | Problemas |",
  "|---|---|",
  (.resumen.por_severidad | to_entries | sort_by($order[.key] // 9)[]
   | "| \(.key | sev) | \(.value) |"),
  "",
  "| Antipatrón | Problemas |",
  "|---|---|",
  (.resumen.por_antipatron | to_entries[] | "| \(.key | nombre | cell) | \(.value) |"),
  "",
  "## Detalle",
  "",
  "Ordenado por impacto: el tiempo de base de datos que costó cada problema en el rango (en contención, la espera acumulada de las sesiones afectadas). Los empates van por severidad y por código. Los abrazos mortales no traen impacto: el informe del motor no dice cuánto esperó cada transacción.",
  (.hallazgos | to_entries[]
   | .key as $i | .value
   | "",
     "### \($i + 1). \(if .codigo then "\(.codigo) " else "" end)\(.antipatron | nombre)\(if .table_name then " en `\(.table_name)`" else "" end)",
     "",
     "- Severidad: \(.severidad | sev) · Impacto en el rango: \(.impacto_ms | dur)",
     (if .database_name then "- Base: `\(.database_name)`" else empty end),
     (if .query_id then "- query_id: `\(.query_id)`" else empty end),
     (if .indice then "- Índice: `\(.indice)`" else empty end),
     (if .columna then "- Columna: `\(.columna)`" else empty end),
     "- Detectado \(.veces) \(if .veces == 1 then "vez" else "veces" end) en el rango, entre \(.primera | ts) y \(.ultima | ts)",
     (if (.por_severidad | length) > 1 then
        "- Por severidad: \(.por_severidad | to_entries | sort_by($order[.key] // 9) | map("\(.key | sev) \(.value)") | join(", "))"
      else empty end),
     (if .severidad_actual != .severidad then "- Severidad de la última detección: \(.severidad_actual | sev)" else empty end),
     "- Histórico: \(.veces_historico) \(if .veces_historico == 1 then "detección" else "detecciones" end) desde \(.primera_historica | ts)",
     "",
     "**Explicación:** \(.explicacion)",
     "",
     "**Recomendación:** \(.recomendacion)",
     (if .riesgo then "", "**Riesgo de aplicarla:** \(.riesgo)" else empty end),
     (if .evidencia != null then "", "Evidencia:", "", "```json", (.evidencia | tojson), "```" else empty end))
end
,
"",
"## Estado de evaluación por patología",
"",
((.ciclos_fallidos // []) as $fallidos
 | (if (.evaluacion | length) == 0 then
   if ($fallidos | length) > 0 then
     "Ninguna patología se pudo evaluar en el rango: QueryLens no pudo extraer la telemetría de la base (ver los tramos sin extracción abajo)."
   else
     "Sin datos del detector en el rango: no se sabe qué patologías se evaluaron."
   end
 else
   "Ventanas de 10 s del rango en que cada patología se pudo evaluar. Sin hallazgos no es lo mismo que no evaluada.",
   "",
   "| Patología | Evaluada | En parte | No evaluada | Motivo |",
   "|---|---|---|---|---|",
   (.evaluacion[]
    | if .aplica then
        "| \(.codigo) \(.antipatron | nombre | cell) | \(.ventanas.evaluada) | \(.ventanas.parcial) | \(.ventanas.no_evaluada) | \(.motivos | map(.codigo) | unique | join(", ") | if . == "" then "-" else cell end) |"
      else
        "| \(.codigo) \(.antipatron | nombre | cell) | - | - | - | No aplica en PostgreSQL |"
      end),
   "",
   (.evaluacion | map(select(.aplica) | .motivos[]) | unique_by(.codigo) | if length > 0 then
      "Qué hacer:",
      "",
      (.[] | "- **\(.codigo)**: \(.mensaje | cell) \(.accion | cell)")
    else empty end)
 end),
  (if ($fallidos | length) > 0 then
     "",
     "Tramos sin extracción: QueryLens no pudo leer la base, el detector no corrió y ninguna patología se evaluó en ese tiempo. Esas ventanas no se cuentan en el estado de evaluación.",
     "",
     ($fallidos[]
      | (if (.desde | ts) == (.hasta | ts) then "el \(.desde | ts)" else "de \(.desde | ts) a \(.hasta | ts)" end) as $cuando
      | (if .ciclos == null then "" else ", \(.ciclos) \(if .ciclos == 1 then "ciclo" else "ciclos" end)" end) as $n
      | "- **\(.codigo)**, \($cuando)\($n)\(if .abierto then " (sigue abierto)" else "" end): \(.mensaje | cell) \(.accion | cell)")
   else empty end))
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
        ($q.por_patron | to_entries[] | "| \(.key | nombre | cell) | \(.value) |"),
        ""
      else empty end),
     (if $q.alcance == "servidor" then
        "En MySQL el contador y los encabezados de abrazos son de todo el servidor, no solo de esta base.",
        ""
      else empty end),
     "CT-05 (cascada por transacción larga) no es un abrazo mortal: no entra en esta cuenta. En los bordes del rango puede haber un abrazo de diferencia."
   end)
