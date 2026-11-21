// Saca de la evidencia de un hallazgo de contención (CT-01 a CT-05) las sesiones implicadas: quién
// es, qué sentencia corre, qué bloqueo espera y cuáles mantiene. Las claves son las que emite
// detection_engine (deadlocks/cycles.py para los abrazos y ct05_long_transaction_cascade.py).
import type { Finding } from "@/types/database";

type Ev = Record<string, unknown>;

export interface LockInfo {
  /** Dónde: `schema.tabla`, y el índice si el motor lo dice. */
  resource: string | null;
  /** Modo tal como lo nombra el motor (X, X,GAP, ShareLock…). */
  mode: string | null;
  /** Fila, hueco, fila+hueco, inserción en un hueco, tabla u otro. */
  scope: string | null;
  /** Identificadores físicos del recurso (página, tupla…). */
  targets: string[];
}

export interface ContentionParty {
  pid: number;
  /** Etiquetas cortas: víctima, raíz… */
  tags: string[];
  statement: string | null;
  /** El bloqueo que espera (abrazos). */
  waits: LockInfo | null;
  /** Los que mantiene; null si el motor no los informa. */
  holds: LockInfo[] | null;
  /** Procesos que la bloquean. */
  blockedBy: number[];
  /** Datos sueltos de la sesión: antigüedad, espera, estado. */
  facts: string[];
}

export interface Contention {
  kind: "cycle" | "cascade";
  parties: ContentionParty[];
  /** De dónde salió la fila de cada espera cuando el motor no la nombra (Postgres, #80). */
  targetsSource: "row_key" | "ctid" | null;
}

const SCOPE_LABEL: Record<string, string> = {
  row: "fila",
  gap: "hueco",
  "row+gap": "fila y hueco",
  insert_intention: "inserción en un hueco",
  table: "tabla",
  other: "otro recurso",
};

export const scopeLabel = (scope: string | null) => (scope ? (SCOPE_LABEL[scope] ?? scope) : null);

const isObj = (v: unknown): v is Ev => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : null);
const int = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const ints = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : []);
const seconds = (n: number) => `${new Intl.NumberFormat("es-CO", { maximumFractionDigits: 1 }).format(n)} s`;

function lockInfo(raw: unknown): LockInfo | null {
  if (!isObj(raw)) return null;
  const table = [text(raw.schema_name), text(raw.table_name)].filter(Boolean).join(".");
  const index = text(raw.index_name);
  const resource = [table, index && `índice ${index}`].filter(Boolean).join(" · ") || null;
  const info: LockInfo = {
    resource,
    mode: text(raw.lock_mode) ?? text(raw.mode),
    scope: text(raw.scope),
    targets: Array.isArray(raw.targets) ? raw.targets.filter((t): t is string => typeof t === "string") : [],
  };
  return info.resource || info.mode || info.scope || info.targets.length ? info : null;
}

/** Las sesiones de un hallazgo de contención, o null si el hallazgo no es de ese tipo. */
export function findingContention(finding: Finding): Contention | null {
  const e = finding.evidencia as Ev;

  if (Array.isArray(e.transactions)) {
    const parties: ContentionParty[] = (e.transactions as unknown[]).filter(isObj).flatMap((t) => {
      const pid = int(t.process_id);
      if (pid == null) return [];
      const facts = [
        int(t.rows_locked) != null && `${t.rows_locked} ${t.rows_locked === 1 ? "fila bloqueada" : "filas bloqueadas"}`,
        int(t.undo_log_entries) != null &&
          `${t.undo_log_entries} ${t.undo_log_entries === 1 ? "cambio sin confirmar" : "cambios sin confirmar"}`,
      ].filter((f): f is string => typeof f === "string");
      return [
        {
          pid,
          tags: t.victim === true ? ["víctima"] : [],
          statement: text(t.canonic_query),
          waits: lockInfo(t.waits_for),
          holds: Array.isArray(t.holds) ? (t.holds as unknown[]).map(lockInfo).filter((l): l is LockInfo => l != null) : null,
          blockedBy: ints(t.blocked_by),
          facts,
        },
      ];
    });
    if (!parties.length) return null;
    const ctid = (e.transactions as unknown[]).some(
      (t) => isObj(t) && isObj(t.waits_for) && t.waits_for.targets_source === "ctid",
    );
    const source = (e.transactions as unknown[]).some(
      (t) => isObj(t) && isObj(t.waits_for) && t.waits_for.targets_source === "row_key",
    );
    return { kind: "cycle", parties, targetsSource: ctid ? "ctid" : source ? "row_key" : null };
  }

  if (isObj(e.root)) {
    const party = (s: Ev, tag?: string): ContentionParty[] => {
      const pid = int(s.process_id);
      if (pid == null) return [];
      const age = int(s.transaction_age_s);
      const wait = int(s.wait_s);
      return [
        {
          pid,
          tags: tag ? [tag] : [],
          statement: text(s.query_text) ?? text(s.canonic_query),
          waits: null,
          holds: null,
          blockedBy: ints(s.blocking_pids),
          facts: [
            text(s.session_state),
            age != null && `transacción abierta hace ${seconds(age)}`,
            wait != null && `espera ${seconds(wait)}`,
          ].filter((f): f is string => typeof f === "string"),
        },
      ];
    };
    const blocked = Array.isArray(e.blocked_sessions) ? (e.blocked_sessions as unknown[]).filter(isObj) : [];
    const parties = [...party(e.root, "sesión raíz"), ...blocked.flatMap((s) => party(s))];
    return parties.length ? { kind: "cascade", parties, targetsSource: null } : null;
  }

  return null;
}
