import { ANTIPATTERN_DOCS, LANDING_ORDER } from "@/lib/antipatterns";
import type { DiagnosticsResponse } from "@/types/database";

// Contenido de la página de presentación. Los antipatrones y sus reglas salen de
// detection_engine/anti_patterns; los ejemplos de consulta y las cifras de
// evidencia son ilustrativos y así se rotulan en la página.

export interface AntipatternInfo {
  id: string;
  title: string;
  summary: string;
  rule: string;
  bad: string;
  fix: string;
  evidence: [string, string][];
}

// La misma fuente que la página de detalle, en el orden de la presentación.
export const ANTIPATTERNS: AntipatternInfo[] = LANDING_ORDER.map((id) => {
  const d = ANTIPATTERN_DOCS[id];
  return { id, title: d.title, summary: d.summary, rule: d.rule, bad: d.bad, fix: d.fix, evidence: d.evidence };
});

export const STAGES = [
  {
    title: "Se conecta",
    text: "Con las credenciales que registras, de forma no intrusiva. No se instala nada dentro de tu servidor.",
    sample: "postgresql://usuario@host:5432/base",
  },
  {
    title: "Extrae",
    text: "Lee estadísticas de consultas y planes EXPLAIN en ciclos de unos 10 segundos.",
    sample: "pg_stat_statements\nEXPLAIN (FORMAT JSON)",
  },
  {
    title: "Detecta",
    text: "Cruza estadísticas, planes e historial y aplica una regla por cada antipatrón.",
    sample: "latencia media ≥ 2 × mediana\nde las 10 ventanas previas",
  },
  {
    title: "Recomienda",
    text: "Cada hallazgo llega con su evidencia, una explicación clara y la acción a seguir.",
    sample: "CREATE INDEX idx_pedidos_cliente\n  ON pedidos (cliente_id);",
  },
];

export type DemoSeverity = "high" | "medium" | "low";

export const FEED_POOL: { severity: DemoSeverity; name: string; subject: string }[] = [
  { severity: "high", name: "missing_index", subject: "pedidos" },
  { severity: "medium", name: "non_sargable_predicate", subject: "clientes" },
  { severity: "low", name: "unused_index", subject: "idx_pedidos_notas" },
  { severity: "high", name: "disk_spill", subject: "q_c4e2" },
  { severity: "medium", name: "avoidable_full_scan", subject: "movimientos" },
  { severity: "medium", name: "baseline_degradation", subject: "q_7c10" },
  { severity: "low", name: "cardinality_misestimate", subject: "envios" },
  { severity: "medium", name: "n_plus_one", subject: "pedidos" },
];

// Respuesta de ejemplo de GET /diagnostics para la vista previa del tablero. La hora del
// servidor es fija para que el texto relativo ("hace 12 min") no cambie entre servidor y navegador.
const SAMPLE_NOW = Date.parse("2026-10-25T17:00:00.000Z");
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const at = (msAgo: number) => new Date(SAMPLE_NOW - msAgo).toISOString();
const byId = (id: string) => ANTIPATTERNS.find((a) => a.id === id)!;

const SAMPLE_RECOMMENDATIONS: Record<string, string> = {
  missing_index: "Crear un índice sobre la columna que filtra la consulta.",
  avoidable_full_scan: "Crear un índice parcial que cubra solo las filas que la consulta busca.",
  non_sargable_predicate: "Crear un índice sobre la expresión o comparar la columna sin aplicarle funciones.",
  unused_index: "Eliminar el índice si ninguna consulta lo necesita.",
  baseline_degradation: "Comparar el plan actual con el anterior y revisar las estadísticas de la tabla.",
  cardinality_misestimate: "Actualizar las estadísticas de la tabla con ANALYZE.",
  disk_spill: "Aumentar la memoria de trabajo para esa consulta o reducir el tamaño del ordenamiento.",
  n_plus_one: "Traer las filas relacionadas con un JOIN, o todas juntas con WHERE ... IN (...).",
};

const SAMPLE_ROWS: [string, "high" | "medium" | "low", string | null, string | null, number][] = [
  ["missing_index", "high", "pedidos", "q_2a91", 12 * MIN],
  ["disk_spill", "high", null, "q_c4e2", 47 * MIN],
  ["non_sargable_predicate", "medium", "clientes", "q_91ab", 3 * HOUR],
  ["avoidable_full_scan", "medium", "movimientos", "q_c4e9", 9 * HOUR],
  ["baseline_degradation", "medium", null, "q_7c10", 20 * HOUR],
  ["n_plus_one", "medium", "pedidos", "q_5d08", 22 * HOUR],
  ["unused_index", "low", "pedidos", null, 2 * DAY],
  ["cardinality_misestimate", "low", "envios", "q_0b77", 3 * DAY],
  ["missing_index", "high", "facturas", "q_a1d3", 4 * DAY],
];

export const SAMPLE_DIAGNOSTICS: DiagnosticsResponse = {
  database_identifier: "db_7f3a91c2",
  connection_name: "Ventas - producción",
  engine: "postgresql",
  is_active: true,
  monitoring_enabled: true,
  server_time: at(0),
  diagnostics: SAMPLE_ROWS.map(([id, severidad, table_name, query_id, ago], i) => ({
    id: 100 - i,
    antipatron: id,
    severidad,
    table_name,
    query_id,
    evidencia: Object.fromEntries(byId(id).evidence),
    explicacion: byId(id).summary,
    recomendacion: SAMPLE_RECOMMENDATIONS[id],
    detectado_en: at(ago),
  })),
  health: {
    last_run: {
      status: "ok",
      started_at: at(52_000),
      finished_at: at(40_000),
      sections_ok: ["consultas", "planes", "tablas_indices", "bloqueos"],
      sections_failed: [],
      msg_id: 1832,
      engine_version: "16.2",
    },
    issues: [],
  },
};
