// Saca de la evidencia de un hallazgo las métricas que se muestran en su tarjeta. Las claves son
// las que emite detection_engine; lo que no se reconoce queda en el JSON de evidencia.
import type { Finding } from "@/types/database";

export interface Metric {
  label: string;
  value: string;
}

const NUMBER = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 1 });
const INT = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });

const fmtMs = (n: number) => `${NUMBER.format(n)} ms`;
const fmtRatio = (n: number) => `${NUMBER.format(n)} ×`;
const fmtPercent = (n: number) => `${new Intl.NumberFormat("es-CO", { maximumFractionDigits: n < 0.01 ? 4 : 2 }).format(n * 100)} %`;
const fmtBytes = (n: number) => (n >= 1_048_576 ? `${NUMBER.format(n / 1_048_576)} MB` : `${NUMBER.format(n / 1024)} KB`);

// Orden de aparición = orden de la lista; la primera clave presente de cada grupo se queda.
const KNOWN: { keys: string[]; label: string; format: (v: number) => string }[] = [
  { keys: ["live_rows"], label: "Filas vivas", format: (n) => INT.format(n) },
  { keys: ["estimated_rows"], label: "Filas estimadas", format: (n) => INT.format(n) },
  { keys: ["actual_rows_per_call"], label: "Filas reales por ejecución", format: (n) => INT.format(n) },
  { keys: ["selectivity"], label: "Selectividad", format: fmtPercent },
  { keys: ["q_error"], label: "Error de estimación", format: fmtRatio },
  { keys: ["ratio"], label: "Razón frente a su mediana", format: fmtRatio },
  { keys: ["baseline_median_ms"], label: "Mediana previa", format: fmtMs },
  { keys: ["window_mean_ms", "mean_time_ms"], label: "Tiempo medio", format: fmtMs },
  { keys: ["window_calls", "execution_count"], label: "Ejecuciones", format: (n) => INT.format(n) },
  { keys: ["child_window_calls"], label: "Ejecuciones de la hija", format: (n) => INT.format(n) },
  { keys: ["parent_window_rows"], label: "Filas del padre", format: (n) => INT.format(n) },
  { keys: ["window_time_ms", "total_time_ms", "extra_time_ms"], label: "Tiempo total", format: fmtMs },
  { keys: ["window_disk_spill"], label: "Escritura temporal", format: (n) => INT.format(n) },
  { keys: ["index_scans"], label: "Lecturas del índice", format: (n) => INT.format(n) },
  { keys: ["index_size_bytes"], label: "Tamaño del índice", format: fmtBytes },
];

const TEXT: { key: string; label: string }[] = [
  { key: "relation", label: "Tabla" },
  { key: "constraint_name", label: "Clave foránea" },
  { key: "index_name", label: "Índice" },
  { key: "suggested_index", label: "Índice sugerido" },
  { key: "column", label: "Columna" },
  { key: "predicate", label: "Predicado" },
];

export function findingMetrics(finding: Finding, limit = 6): Metric[] {
  const e = finding.evidencia as Record<string, unknown>;
  const out: Metric[] = [];

  for (const { keys, label, format } of KNOWN) {
    const key = keys.find((k) => typeof e[k] === "number" && Number.isFinite(e[k] as number));
    if (key) out.push({ label, value: format(e[key] as number) });
  }
  for (const { key, label } of TEXT) {
    const v = e[key];
    if (typeof v === "string" && v.trim()) out.push({ label, value: v });
  }
  return out.slice(0, limit);
}

/** La consulta SQL de la evidencia, si el detector la incluyó. */
export function findingQuery(finding: Finding): string | null {
  const q = (finding.evidencia as Record<string, unknown>).query_text;
  return typeof q === "string" && q.trim() ? q.trim() : null;
}

/** Tablas que toca un hallazgo: la propia o las que lista la evidencia. */
export function findingTables(finding: Finding): string[] {
  const set = new Set<string>();
  if (finding.table_name) set.add(finding.table_name);
  const tables = (finding.evidencia as Record<string, unknown>).tables;
  if (Array.isArray(tables)) for (const t of tables) if (typeof t === "string") set.add(t);
  return [...set];
}
