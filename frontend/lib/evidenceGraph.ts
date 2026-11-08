// Convierte el JSON de evidencia de un hallazgo en un grafo de nodos y relaciones. Cada antipatrón
// cuenta una historia distinta (qué consulta lee qué tabla, por qué columna filtra, qué índice
// existe o falta); las claves son las que emite detection_engine. Lo que falta en el JSON
// simplemente no se dibuja.
import type { Finding } from "@/types/database";

export type NodeKind = "query" | "table" | "column" | "index" | "predicate" | "metric" | "session";
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

    case "n_plus_one": {
      // El protagonista es la consulta padre que devuelve las filas y la hija que se repite por cada una.
      nodes.length = 0;
      const parentId = e.parent_query_id != null ? String(e.parent_query_id) : null;
      const parentText = str(e, "parent_query_text") ?? str(e, "parent_canonic_query");
      const childText = str(e, "query_text") ?? str(e, "canonic_query");
      const rows = num(e, "parent_window_rows");
      const calls = num(e, "child_window_calls");
      const dev = num(e, "desvio");
      const childTable = str(e, "child_table") ?? tables[0];
      const filterCols = strs(e, "child_filter_columns").join(", ");
      const fk = str(e, "constraint_name");

      add({ id: "pq", layer: 0, kind: "query", title: parentId ? `query ${parentId}` : "consulta padre", sub: parentText ? cut(parentText, 48) : "la que lee la lista", tone: "neutral" });
      if (rows != null) {
        add({ id: "r", layer: 1, kind: "metric", title: `${INT.format(rows)} filas`, sub: "devueltas en la ventana", tone: "warn" });
        link("pq", "r", "warn", "devuelve");
      }
      const child = add(queryNode(finding, e));
      if (child) {
        child.layer = 2;
        child.tone = "neg";
        child.sub = childText ? cut(childText, 48) : "la que se repite";
        link(rows != null ? "r" : "pq", "q", "neg", "dispara una por fila");
      }
      if (calls != null) {
        add({ id: "k", layer: 3, kind: "metric", title: `${INT.format(calls)} ejecuciones`, sub: dev != null ? `desvío de ${DEC.format(dev * 100)} % frente a las filas` : "en la misma ventana", tone: "neg" });
        link("q", "k", "neg", "se ejecutó");
      }
      if (childTable) {
        add({ id: "t0", layer: 3, kind: "table", title: childTable, sub: filterCols ? `filtra por ${filterCols}` : undefined, tone: "neutral" });
        link("q", "t0", "neutral", fk ? "por la clave" : "lee");
      }
      if (fk) {
        add({ id: "fk", layer: 4, kind: "index", title: fk, sub: "clave foránea que une ambas", tone: "warn", dashed: true });
        link("t0", "fk", "warn", "unida por", true);
      }
      break;
    }

    case "inconsistent_lock_order_deadlock":
    case "lock_mode_conversion_deadlock":
    case "range_contention_deadlock":
    case "lock_amplification_deadlock":
    case "unclassified_deadlock": {
      // El ciclo de espera: cada flecha va de quien bloquea a quien espera, y la última cierra el ciclo.
      nodes.length = 0;
      const trxs = (Array.isArray(e.transactions) ? (e.transactions as Evidence[]) : []).filter((t) => num(t, "process_id") != null).slice(0, 8);
      const blockersOf = new Map<number, number[]>();
      for (const t of trxs) {
        const by = Array.isArray(t.blocked_by) ? t.blocked_by.filter((p): p is number => typeof p === "number") : [];
        blockersOf.set(num(t, "process_id")!, by);
      }
      // Orden de capas siguiendo la cadena de bloqueo; lo que no se alcanza va al final.
      const order: number[] = [];
      let cur: number | undefined = trxs.length ? num(trxs[0], "process_id")! : undefined;
      while (cur != null && !order.includes(cur)) {
        order.push(cur);
        const here: number = cur;
        cur = [...blockersOf.entries()].find(([, by]) => by.includes(here))?.[0];
      }
      for (const t of trxs) if (!order.includes(num(t, "process_id")!)) order.push(num(t, "process_id")!);

      const victim = num(e, "victim_process_id");
      for (const t of trxs) {
        const pid = num(t, "process_id")!;
        const text = str(t, "canonic_query");
        add({
          id: `p${pid}`, layer: order.indexOf(pid), kind: "session", title: `pid ${pid}`,
          sub: [pid === victim && "víctima", text && cut(text, 40)].filter(Boolean).join(" · ") || "sesión",
          tone: pid === victim ? "neg" : "warn",
        });
      }
      for (const [pid, by] of blockersOf) {
        for (const b of by) link(`p${b}`, `p${pid}`, b === victim ? "neg" : "warn", "bloquea");
      }
      break;
    }

    case "long_transaction_cascade": {
      // Aquí no hay una consulta única: el grafo es el de espera entre sesiones.
      nodes.length = 0;
      const root = e.root && typeof e.root === "object" ? (e.root as Evidence) : null;
      const rootPid = root ? num(root, "process_id") : null;
      const blocked = (Array.isArray(e.blocked_sessions) ? (e.blocked_sessions as Evidence[]) : [])
        .filter((s) => num(s, "process_id") != null)
        .slice(0, 12);
      if (!root || rootPid == null) break;

      const blockersOf = new Map<number, number[]>();
      for (const s of blocked) {
        const pids = Array.isArray(s.blocking_pids) ? s.blocking_pids.filter((p): p is number => typeof p === "number") : [];
        blockersOf.set(num(s, "process_id")!, pids);
      }
      // Todo lo que está aguas arriba de una sesión (con guarda por si hay un ciclo).
      const upstream = (pid: number, seen = new Set<number>()): Set<number> => {
        for (const b of blockersOf.get(pid) ?? []) {
          if (!seen.has(b)) {
            seen.add(b);
            upstream(b, seen);
          }
        }
        return seen;
      };
      // Profundidad en la cola: el camino de espera más largo hasta la raíz.
      const depthOf = (pid: number, seen = new Set<number>()): number => {
        if (pid === rootPid || seen.has(pid)) return 0;
        seen.add(pid);
        const ds = (blockersOf.get(pid) ?? []).map((b) => depthOf(b, new Set(seen)));
        return 1 + (ds.length ? Math.max(...ds) : 0);
      };

      const age = num(root, "transaction_age_s");
      add({
        id: `p${rootPid}`, layer: 0, kind: "session", title: `pid ${rootPid}`, tone: "neg",
        sub: [str(root, "session_state"), age != null && `${DEC.format(age)} s abierta`].filter(Boolean).join(" · ") || "sesión raíz",
      });
      for (const s of blocked) {
        const pid = num(s, "process_id")!;
        const wait = num(s, "wait_s");
        add({
          id: `p${pid}`, layer: depthOf(pid), kind: "session", title: `pid ${pid}`, tone: "warn",
          sub: wait != null ? `espera ${DEC.format(wait)} s` : "en espera",
        });
      }
      // Solo el bloqueo directo: si 2332 espera a 2330 y 2331, y 2331 ya espera a 2330, basta 2331 → 2332.
      for (const [pid, blockers] of blockersOf) {
        for (const b of blockers) {
          if (blockers.some((w) => w !== b && upstream(w).has(b))) continue;
          link(`p${b}`, `p${pid}`, b === rootPid ? "neg" : "warn", "bloquea");
        }
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
