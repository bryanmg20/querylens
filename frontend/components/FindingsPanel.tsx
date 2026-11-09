"use client";

import { useState } from "react";
import { ANTIPATTERN_GROUPS, ANTIPATTERN_ORDER, getDoc } from "@/lib/antipatterns";
import { findingQuery, findingTables } from "@/lib/evidence";
import { SEVERITY_KEYS, SEVERITY_META, countBySeverity, severityKey } from "@/lib/insights";
import type { Finding } from "@/types/database";
import SectionTabs from "./antipatterns/SectionTabs";
import FindingCard from "./FindingCard";
import type { SeverityFilter } from "./KpiStrip";

// Cuántos hallazgos se pintan de una vez; "Mostrar más" abre el resto.
const PAGE_SIZE = 12;

export interface Filters {
  severity: SeverityFilter;
  pattern: string; // "all" o el id del antipatrón
  query: string;
}

export const NO_FILTERS: Filters = { severity: "all", pattern: "all", query: "" };

/** Los hallazgos que pasan los filtros: lo que se ve en pantalla y lo que sale en el PDF. */
export function filterFindings(findings: Finding[], filters: Filters): Finding[] {
  const q = filters.query.trim().toLowerCase();
  return findings.filter((f) => {
    if (filters.severity !== "all" && severityKey(f.severidad) !== filters.severity) return false;
    if (filters.pattern !== "all" && f.antipatron !== filters.pattern) return false;
    if (q) {
      const hay = [...findingTables(f), f.query_id ?? "", findingQuery(f) ?? ""].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// Todos los hallazgos del detection_engine, del más reciente al más viejo, uno por tarjeta. Los
// filtros viven en la página para que la tira de indicadores también pueda cambiarlos.
export default function FindingsPanel({
  findings,
  waiting,
  serverTime,
  filters,
  onFilters,
}: {
  findings: Finding[];
  waiting: boolean;
  serverTime: string;
  filters: Filters;
  onFilters: (next: Filters) => void;
}) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  const counts = countBySeverity(findings);

  const patternCounts = new Map<string, number>();
  for (const f of findings) patternCounts.set(f.antipatron, (patternCounts.get(f.antipatron) ?? 0) + 1);
  const knownIds: string[] = [...ANTIPATTERN_ORDER].filter((id) => patternCounts.has(id));
  const unknownIds = [...patternCounts.keys()].filter((id) => !getDoc(id));

  // Dos secciones; solo salen los patrones con hallazgos. Los que no se reconocen van con los antipatrones.
  const sections = ANTIPATTERN_GROUPS.map((g, i) => ({
    key: g.key,
    title: g.title,
    ids: [...knownIds.filter((id) => (g.ids as readonly string[]).includes(id)), ...(i === 0 ? unknownIds : [])],
  }));
  const [section, setSection] = useState<string | null>(null);
  const activeKey =
    section ??
    (filters.pattern !== "all" ? sections.find((g) => g.ids.includes(filters.pattern))?.key : undefined) ??
    (sections.find((g) => g.ids.length > 0) ?? sections[0]).key;
  const shownSection = sections.find((g) => g.key === activeKey) ?? sections[0];

  const visible = filterFindings(findings, filters);

  const set = (patch: Partial<Filters>) => {
    setLimit(PAGE_SIZE);
    onFilters({ ...filters, ...patch });
  };
  const pickSection = (key: string) => {
    setSection(key);
    const target = sections.find((g) => g.key === key);
    // Un filtro de la sección que se oculta ya no tiene a la vista su chip: se limpia.
    if (filters.pattern !== "all" && target && !target.ids.includes(filters.pattern)) set({ pattern: "all" });
  };
  const tabs: { key: SeverityFilter; label: string; count: number }[] = [
    { key: "all", label: "Todas", count: findings.length },
    ...SEVERITY_KEYS.filter((key) => counts[key] > 0).map((key) => ({
      key,
      label: SEVERITY_META[key].label,
      count: counts[key],
    })),
  ];
  const listKey = `${filters.severity}|${filters.pattern}`;

  return (
    <section aria-labelledby="findings-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="findings-title" className="text-[20px] font-semibold tracking-[-0.01em]">
          Hallazgos detectados
        </h2>
        {findings.length > 0 && (
          <div className="flex items-center gap-4">
            <span className="text-[13px] text-muted">
              {visible.length} {visible.length === 1 ? "hallazgo" : "hallazgos"}
            </span>
            <button
              type="button"
              onClick={() => window.print()}
              disabled={visible.length === 0}
              className="btn-outline h-8"
              title="Abre el diálogo de impresión: elige «Guardar como PDF». Incluye los hallazgos que estén filtrados."
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14" />
              </svg>
              Exportar PDF
            </button>
          </div>
        )}
      </div>

      {findings.length === 0 ? (
        <p className="empty-frame mt-4">
          {waiting
            ? "Aún no hay hallazgos: se esperan después de la primera extracción."
            : "No se han detectado antipatrones ni abrazos mortales en esta sesión de detección."}
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
            <div role="tablist" aria-label="Filtrar por severidad" className="flex gap-5">
              {tabs.map((tab) => {
                const selected = tab.key === filters.severity;
                return (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    onClick={() => set({ severity: tab.key })}
                    className={`relative h-9 text-[13px] transition-colors duration-150 ${
                      selected ? "font-medium text-fg" : "text-muted hover:text-fg"
                    }`}
                  >
                    {tab.label} <span className="text-muted">{tab.count}</span>
                    <span
                      aria-hidden="true"
                      className={`absolute inset-x-0 bottom-0 h-px origin-left bg-accent transition-transform duration-200 ease-[var(--ease-out)] ${
                        selected ? "scale-x-100" : "scale-x-0"
                      }`}
                    />
                  </button>
                );
              })}
            </div>

            <label className="relative block w-full sm:w-64">
              <span className="sr-only">Buscar por tabla o query</span>
              <svg
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
              <input
                type="search"
                value={filters.query}
                onChange={(e) => set({ query: e.target.value })}
                placeholder="Buscar por tabla o query"
                className="input h-9 pl-9"
              />
            </label>
          </div>

          <div className="mt-3" role="group" aria-label="Filtrar por patrón">
            <SectionTabs
              label="Sección de patrones"
              value={shownSection.key}
              onChange={pickSection}
              tabs={sections.map((g) => ({ key: g.key, label: g.title, count: g.ids.reduce((n, id) => n + (patternCounts.get(id) ?? 0), 0) }))}
            />
            <div className="mt-2 flex flex-wrap gap-2">
              <Chip selected={filters.pattern === "all"} onClick={() => set({ pattern: "all" })}>
                Todos los patrones <span className="text-muted">{findings.length}</span>
              </Chip>
              {shownSection.ids.map((id) => {
                const doc = getDoc(id);
                return (
                  <Chip key={id} selected={filters.pattern === id} onClick={() => set({ pattern: filters.pattern === id ? "all" : id })}>
                    {doc && <span className="text-muted">{doc.code}</span>} {doc ? doc.title.toLowerCase() : id}{" "}
                    <span className="text-muted">{patternCounts.get(id)}</span>
                  </Chip>
                );
              })}
              {shownSection.ids.length === 0 && <span className="text-[13px] text-muted">Sin hallazgos de este tipo.</span>}
            </div>
          </div>

          {visible.length === 0 ? (
            <p className="empty-frame mt-4">Ningún hallazgo coincide con estos filtros.</p>
          ) : (
            <div key={listKey} className="mt-4 space-y-4">
              {visible.slice(0, limit).map((finding, i) => (
                <FindingCard key={finding.id} finding={finding} serverTime={serverTime} index={i} />
              ))}
            </div>
          )}

          {visible.length > limit && (
            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="text-[13px] text-muted">
                Mostrando {limit} de {visible.length}
              </span>
              <button type="button" className="btn-outline" onClick={() => setLimit((n) => n + PAGE_SIZE)}>
                Mostrar más
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function Chip({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1 rounded-full px-3.5 text-[13px] transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
        selected ? "bg-raised text-fg" : "text-muted [@media(hover:hover)]:hover:bg-surface [@media(hover:hover)]:hover:text-fg"
      }`}
    >
      {children}
    </button>
  );
}
