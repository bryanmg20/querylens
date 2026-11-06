// Convierte el JSON de evidencia de un hallazgo en un grafo de nodos y relaciones. Cada antipatrón
// cuenta una historia distinta (qué consulta lee qué tabla, por qué columna filtra, qué índice
// existe o falta); las claves son las que emite detection_engine. Lo que falta en el JSON
// simplemente no se dibuja.
import type { Finding } from "@/types/database";

export type NodeKind = "query" | "table" | "column" | "index" | "predicate" | "metric";
export type Tone = "neutral" | "neg" | "pos" | "warn" | "accent";

export interface GNode {
  id: string;
  layer: number;
  kind: NodeKind;
  title: string;
  sub?: string;
  tone: Tone;
  dashed?: boolean;
  /** Serie para dibujar una línea pequeña dentro del nodo (degradación). */
  series?: number[];
  median?: number;
}

export interface GEdge {
  from: string;
  to: string;
  label?: string;
  tone: Tone;
  dashed?: boolean;
}

export interface EvidenceGraph {
  nodes: GNode[];
  edges: GEdge[];
}

type Evidence = Record<string, unknown>;

const INT = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const DEC = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 1 });

const str = (e: Evidence, k: string): string | null => {
  const v = e[k];
  return typeof v === "string" && v.trim() ? v.trim() : null;
};
const num = (e: Evidence, k: string): number | null => {
  const v = e[k];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
};
const strs = (e: Evidence, k: string): string[] => {
  const v = e[k];
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.length > 0) : [];
};
const cut = (s: string, n: number) => {
  const flat = s.replace(/\s+/g, " ");
  return flat.length > n ? `${flat.slice(0, n - 1)}…` : flat;
};
const ms = (n: number) => `${DEC.format(n)} ms`;

function bytes(n: number): string {
  return n >= 1_048_576 ? `${DEC.format(n / 1_048_576)} MB` : `${DEC.format(n / 1024)} KB`;
}

const SUBTYPES: Record<string, string> = {
  funcion_sobre_columna: "función sobre la columna",
  aritmetica_sobre_columna: "operación sobre la columna",
  cast_implicito: "conversión de tipo implícita",
  like_con_comodin_inicial: "comodín al inicio del LIKE",
  subestimacion: "subestimación",
  sobreestimacion: "sobreestimación",
};

function humanize(raw: string): string {
  return SUBTYPES[raw] ?? raw.replace(/_/g, " ");
}

function queryNode(finding: Finding, e: Evidence): GNode | null {
  const id = finding.query_id ?? (e.query_id != null ? String(e.query_id) : null);
  if (id == null) return null;
  const text = str(e, "query_text");
  return { id: "q", layer: 0, kind: "query", title: `query ${id}`, sub: text ? cut(text, 48) : undefined, tone: "neutral" };
}

function tablesOf(finding: Finding, e: Evidence): string[] {
  const set = new Set<string>();
  const relation = str(e, "relation");
  if (relation) set.add(relation);
  if (finding.table_name) set.add(finding.table_name);
  for (const t of strs(e, "tables")) set.add(t);
  return [...set];
}

/** Columnas por tabla de `filter_columns`: acepta {tabla: [cols]} o una lista plana. */
function filterColumns(e: Evidence): [string | null, string][] {
  const v = e.filter_columns;
  const out: [string | null, string][] = [];
  if (Array.isArray(v)) {
    for (const c of v) if (typeof c === "string") out.push([null, c]);
  } else if (v && typeof v === "object") {
    for (const [table, cols] of Object.entries(v as Record<string, unknown>)) {
      if (Array.isArray(cols)) for (const c of cols) if (typeof c === "string") out.push([table, c]);
    }
  }
  return out.slice(0, 6);
}

export function buildEvidenceGraph(finding: Finding): EvidenceGraph {
  const e = finding.evidencia as Evidence;
  const nodes: GNode[] = [];
  const edges: GEdge[] = [];
  const add = (n: GNode | null) => {
    if (n) nodes.push(n);
    return n;
  };
  const link = (from: string, to: string, tone: Tone, label?: string, dashed?: boolean) => {
    if (nodes.some((n) => n.id === from) && nodes.some((n) => n.id === to)) edges.push({ from, to, tone, label, dashed });
  };

  const q = add(queryNode(finding, e));
  const tables = tablesOf(finding, e);

  switch (finding.antipatron) {
    case "missing_index": {
      const table = tables[0];
      const live = num(e, "live_rows");
      const est = num(e, "estimated_rows");
      if (table) {
        add({
          id: "t0", layer: 1, kind: "table", title: table, tone: "neg",
          sub: [live != null && `${INT.format(live)} filas vivas`, est != null && `${INT.format(est)} estimadas`].filter(Boolean).join(" · ") || undefined,
        });
        link("q", "t0", "neg", "recorre toda la tabla");
      }
      const eq = strs(e, "equality_columns");
      const range = strs(e, "range_columns");
      [...eq.map((c) => [c, "igualdad"] as const), ...range.map((c) => [c, "rango"] as const)].slice(0, 4).forEach(([c, kind], i) => {
        add({ id: `c${i}`, layer: 2, kind: "column", title: c, sub: `filtra por ${kind}`, tone: "warn" });
        link("t0", `c${i}`, "neutral", i === 0 ? "filtra por" : undefined);
      });
      strs(e, "existing_indexes").slice(0, 3).forEach((ix, i) => {
        add({ id: `x${i}`, layer: 3, kind: "index", title: ix, sub: "existente", tone: "neutral", dashed: true });
        link("t0", `x${i}`, "neutral", i === 0 ? "no cubren el filtro" : undefined, true);
      });
      const suggested = str(e, "suggested_index");
      if (suggested) {
        add({ id: "sx", layer: 3, kind: "index", title: "Índice propuesto", sub: cut(suggested, 52), tone: "pos", dashed: true });
        const cols = nodes.filter((n) => n.kind === "column");
        if (cols.length) cols.forEach((c) => link(c.id, "sx", "pos", "lo cubriría", true));
        else link("t0", "sx", "pos", "lo cubriría", true);
      }
      break;
    }

    case "avoidable_full_scan": {
      const table = tables[0];
      const live = num(e, "live_rows");
      if (table) {
        add({ id: "t0", layer: 1, kind: "table", title: table, tone: "neg", sub: live != null ? `${INT.format(live)} filas vivas` : undefined });
        link("q", "t0", "neg", "lee toda la tabla");
      }
      const predicate = str(e, "predicate");
      const sel = num(e, "selectivity");
      if (predicate) {
        add({ id: "p", layer: 2, kind: "predicate", title: cut(predicate, 40), tone: "warn", sub: sel != null ? `deja pasar el ${DEC.format(sel * 100)} %` : "filtro muy selectivo" });
        link("t0", "p", "neutral", "filtra por");
      }
      const est = num(e, "estimated_rows");
      if (est != null) {
        add({ id: "m", layer: 3, kind: "metric", title: `${INT.format(est)} filas`, sub: "las que necesita", tone: "pos" });
        link("p", "m", "pos", "devuelve");
      }
      break;
    }

    case "non_sargable_predicate": {
      const predicate = str(e, "predicate");
      const column = str(e, "column");
      const relation = str(e, "relation") ?? tables[0];
      const subtype = str(e, "subtipo");
      if (predicate) {
        add({ id: "p", layer: 1, kind: "predicate", title: cut(predicate, 42), sub: subtype ? humanize(subtype) : undefined, tone: "neg" });
        link("q", "p", "neutral", str(e, "access_method")?.replace(/_/g, " ") ?? "filtra por");
      }
      if (column) {
        add({ id: "c", layer: 2, kind: "column", title: column, sub: relation ? `en ${relation}` : undefined, tone: "warn" });
        link("p", "c", "neg", "transforma la columna");
      }
      const exists = e.index_exists;
      if (typeof exists === "boolean") {
        add(
          exists
            ? { id: "x", layer: 3, kind: "index", title: "Índice existente", sub: "no se puede usar", tone: "neutral", dashed: true }
            : { id: "x", layer: 3, kind: "index", title: "Sin índice", sub: "sobre esa columna", tone: "neg", dashed: true }
        );
        link(column ? "c" : "p", "x", exists ? "neg" : "neutral", exists ? "índice ignorado" : undefined, true);
      }
      break;
    }

    case "unused_index": {
      const table = str(e, "table_name") ?? tables[0];
      const redundant = str(e, "subtipo") === "redundante";
      const index = str(e, "index_name");
      const scans = num(e, "index_scans");
      const size = num(e, "index_size_bytes");
      const queries = Array.isArray(e.queries_que_filtran_la_tabla)
        ? (e.queries_que_filtran_la_tabla as Record<string, unknown>[]).slice(0, 3)
        : [];

      queries.forEach((qq, i) => {
        const text = typeof qq.query_text === "string" ? qq.query_text : null;
        add({ id: `q${i}`, layer: 0, kind: "query", title: `query ${qq.query_id ?? i + 1}`, sub: text ? cut(text, 44) : undefined, tone: "neutral" });
      });
      if (table) add({ id: "t0", layer: 1, kind: "table", title: table, tone: "neutral" });
      queries.forEach((qq, i) => {
        const cols = Array.isArray(qq.filtra_por) ? (qq.filtra_por as string[]).join(", ") : "";
        link(`q${i}`, "t0", "neutral", cols ? `filtra por ${cut(cols, 24)}` : undefined);
      });

      if (index) {
        add({
          id: "x", layer: 2, kind: "index", title: index, tone: "neg",
          sub: redundant
            ? `redundante (${humanize(str(e, "tipo_redundancia") ?? "prefijo")})`
            : [scans != null && `${INT.format(scans)} lecturas`, size != null && bytes(size)].filter(Boolean).join(" · ") || undefined,
        });
        link("t0", "x", "neutral", "tiene");
        queries.forEach((qq, i) => {
          if (qq.filtra_por_columna_del_indice) link(`q${i}`, "x", "pos", "filtra por su columna", true);
        });
      }
      const wider = str(e, "cubierto_por");
      if (redundant && wider) {
        add({ id: "w", layer: 3, kind: "index", title: wider, sub: "lo cubre", tone: "pos" });
        link("t0", "w", "neutral", "tiene");
        link("x", "w", "warn", "cubierto por", true);
      }
      break;
    }

    case "baseline_degradation": {
      const series = Array.isArray(e.baseline_series_ms) ? (e.baseline_series_ms as unknown[]).filter((x): x is number => typeof x === "number") : [];
      const median = num(e, "baseline_median_ms");
      const mean = num(e, "window_mean_ms");
      const calls = num(e, "window_calls");
      const ratio = num(e, "ratio");
      const extra = num(e, "extra_time_ms");
      if (series.length || median != null) {
        add({
          id: "h", layer: 1, kind: "metric", title: "Ventanas previas", tone: "neutral",
          sub: median != null ? `mediana ${ms(median)}` : undefined, series, median: median ?? undefined,
        });
        link("q", "h", "neutral", "su histórico");
      }
      if (mean != null) {
        add({ id: "w", layer: 2, kind: "metric", title: ms(mean), sub: calls != null ? `${INT.format(calls)} ejecuciones` : "ventana actual", tone: "neg" });
        link(nodes.some((n) => n.id === "h") ? "h" : "q", "w", "neg", ratio != null ? `${DEC.format(ratio)} ×` : "se aleja");
      }
      if (extra != null) {
        add({ id: "x", layer: 3, kind: "metric", title: ms(extra), sub: "tiempo extra en la ventana", tone: "warn" });
        link("w", "x", "warn", "costó");
      }
      break;
    }

    case "cardinality_misestimate": {
      tables.slice(0, 3).forEach((t, i) => {
        add({ id: `t${i}`, layer: 1, kind: "table", title: t, tone: "neutral" });
        link("q", `t${i}`, "neutral", i === 0 ? "lee" : undefined);
      });
      filterColumns(e).forEach(([t, c], i) => {
        add({ id: `c${i}`, layer: 2, kind: "column", title: c, sub: "filtra por", tone: "warn" });
        const owner = t ? tables.indexOf(t) : tables.length === 1 ? 0 : -1;
        if (owner >= 0 && owner < 3) link(`t${owner}`, `c${i}`, "neutral");
      });
      const est = num(e, "estimated_rows");
      const actual = num(e, "actual_rows_per_call");
      const qerr = num(e, "q_error");
      const dir = str(e, "direccion");
      if (est != null) {
        add({ id: "e", layer: 1, kind: "metric", title: INT.format(est), sub: "filas estimadas", tone: "warn" });
        link("q", "e", "warn", "el plan esperaba");
      }
      if (actual != null) {
        add({ id: "a", layer: 1, kind: "metric", title: INT.format(actual), sub: "filas reales por ejecución", tone: "accent" });
        link("q", "a", "accent", "devolvió");
      }
      if (qerr != null) {
        add({ id: "d", layer: 2, kind: "metric", title: `${INT.format(qerr)} ×`, sub: dir ? humanize(dir) : "diferencia", tone: "neg" });
        link("e", "d", "neg");
        link("a", "d", "neg");
      }
      break;
    }

    case "disk_spill": {
      tables.slice(0, 3).forEach((t, i) => {
        add({ id: `t${i}`, layer: 1, kind: "table", title: t, tone: "neutral" });
        link("q", `t${i}`, "neutral", i === 0 ? "lee" : undefined);
      });
      const spill = num(e, "window_disk_spill");
      const unit = str(e, "disk_spill_unit");
      const time = num(e, "window_time_ms");
      const calls = num(e, "window_calls");
      add({ id: "m", layer: 1, kind: "metric", title: "Memoria de trabajo", sub: "no alcanza", tone: "warn" });
      link("q", "m", "warn", "ordena o agrupa");
      if (spill != null) {
        add({ id: "k", layer: 2, kind: "metric", title: `${INT.format(spill)} ${unit ? cut(unit, 22) : "escrituras"}`, sub: "temporales en disco", tone: "neg" });
        link("m", "k", "neg", "se desborda");
      }
      if (time != null) {
        add({ id: "i", layer: 3, kind: "metric", title: ms(time), sub: calls != null ? `${INT.format(calls)} ejecuciones` : "en la ventana", tone: "warn" });
        link(spill != null ? "k" : "m", "i", "warn", "cuesta");
      }
      break;
    }

    default: {
      tables.slice(0, 4).forEach((t, i) => {
        add({ id: `t${i}`, layer: 1, kind: "table", title: t, tone: "neutral" });
        link("q", `t${i}`, "neutral", i === 0 ? "toca" : undefined);
      });
    }
  }

  return { nodes, edges };
}
