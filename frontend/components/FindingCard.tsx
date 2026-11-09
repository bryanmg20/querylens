"use client";

import Link from "next/link";
import { useState } from "react";
import { getDoc } from "@/lib/antipatterns";
import { buildEvidenceGraph } from "@/lib/evidenceGraph";
import { findingMetrics, findingQuery, findingTables } from "@/lib/evidence";
import { formatDateTime, timeAgo } from "@/lib/format";
import { SEVERITY_META, formatCount, severityBreakdown, severityKey } from "@/lib/insights";
import type { Finding } from "@/types/database";
import EvidenceGraphView from "./antipatterns/EvidenceGraph";

// Un hallazgo completo: qué es, la consulta, las métricas, la recomendación y la evidencia (en
// JSON y como grafo, los dos plegables). "¿Qué es esto?" lleva a la explicación animada del patrón.
export default function FindingCard({
  finding,
  serverTime,
  index = 0,
}: {
  finding: Finding;
  serverTime: string;
  index?: number;
}) {
  const [jsonOpen, setJsonOpen] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  // El grafo se construye y se monta la primera vez que se abre, no con cada tarjeta de la lista.
  const [graphMounted, setGraphMounted] = useState(false);
  const meta = SEVERITY_META[severityKey(finding.severidad)];
  // La etiqueta es la severidad más alta de la sesión; si la última detección tuvo otra, se dice al lado.
  const current = severityKey(finding.severidad_actual) !== severityKey(finding.severidad)
    ? SEVERITY_META[severityKey(finding.severidad_actual)].label.toLowerCase()
    : null;
  const breakdown = severityBreakdown(finding);
  const doc = getDoc(finding.antipatron);
  const metrics = findingMetrics(finding);
  const query = findingQuery(finding);
  const tables = findingTables(finding);
  const graph = graphMounted ? buildEvidenceGraph(finding) : null;
  const jsonId = `evidence-${finding.id}`;
  const graphId = `graph-${finding.id}`;

  return (
    <article className="rise card p-5" style={{ ["--i" as string]: Math.min(index, 8) }}>
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <span className={meta.pill} title="La severidad más alta que tuvo en esta sesión">
            {meta.label}
          </span>
          {current && <span className="text-[13px] text-muted">ahora {current}</span>}
          {doc && <span className="pill-neu">{doc.code}</span>}
          <h3 className="text-[16px] font-semibold">{doc?.title ?? finding.antipatron}</h3>
        </div>
        <p className="flex flex-wrap gap-x-4 text-[13px] text-muted">
          {tables.length > 0 && (
            <span>
              {tables.length === 1 ? "Tabla" : "Tablas"} <code className="font-mono text-fg">{tables.join(", ")}</code>
            </span>
          )}
          {finding.query_id && (
            <span>
              query <code className="font-mono text-fg">{finding.query_id}</code>
            </span>
          )}
        </p>
      </header>

      <p className="mt-3 max-w-[60rem] text-[13px] leading-5">{finding.explicacion}</p>

      {query && (
        <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-ground px-3 py-2.5 font-mono text-[13px] leading-5 text-muted">
          {query}
        </pre>
      )}

      {metrics.length > 0 && (
        <dl className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2">
          {metrics.map((m) => (
            <div key={m.label} className="min-w-0 rounded bg-ground px-3 py-2">
              <dt className="text-[13px] leading-4 text-muted">{m.label}</dt>
              <dd className="mt-0.5 truncate text-[16px] font-semibold" title={m.value}>
                {m.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <p className="notice-pos mt-3 leading-5">
        <span className="font-semibold">Recomendación </span>
        {finding.recomendacion}
      </p>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[13px]">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <Toggle
            open={graphOpen}
            controls={graphId}
            onClick={() => {
              setGraphMounted(true);
              setGraphOpen((v) => !v);
            }}
          >
            Grafo de la evidencia
          </Toggle>
          <Toggle open={jsonOpen} controls={jsonId} onClick={() => setJsonOpen((v) => !v)}>
            Evidencia (JSON)
          </Toggle>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted">
          <span title={breakdown ? `En esta sesión: ${breakdown}` : undefined}>
            {formatCount(finding.veces_sesion)} {finding.veces_sesion === 1 ? "vez" : "veces"} en esta sesión
            {breakdown && ` (${breakdown})`}
          </span>
          <span title={`Primera detección: ${formatDateTime(finding.primera_deteccion)}`}>
            {formatCount(finding.veces_total)} en total
          </span>
          <span title={formatDateTime(finding.detectado_en)}>Última {timeAgo(finding.detectado_en, serverTime)}</span>
          {doc && (
            <Link
              href={`/diagnostics/${doc.id}`}
              className="group flex items-center gap-1.5 font-medium text-fg transition-colors duration-150 hover:text-accent"
            >
              ¿Qué es esto?
              <span aria-hidden="true" className="transition-transform duration-200 ease-[var(--ease-out)] group-hover:translate-x-0.5">
                →
              </span>
            </Link>
          )}
        </div>
      </div>

      <div id={graphId} className="reveal" data-open={graphOpen}>
        <div className="overflow-hidden">
          {graph && graph.nodes.length > 1 ? (
            <div className="mt-3 rounded bg-ground p-4">
              <EvidenceGraphView graph={graph} />
            </div>
          ) : (
            graphMounted && <p className="empty-frame mt-3">Este hallazgo no trae relaciones suficientes para dibujar un grafo.</p>
          )}
        </div>
      </div>

      <div id={jsonId} className="reveal" data-open={jsonOpen}>
        <div className="overflow-hidden">
          <pre className="mt-3 max-h-56 overflow-auto rounded bg-ground px-3 py-2.5 font-mono text-[13px] leading-5">
            {JSON.stringify(finding.evidencia, null, 2)}
          </pre>
        </div>
      </div>
    </article>
  );
}

function Toggle({
  open,
  controls,
  onClick,
  children,
}: {
  open: boolean;
  controls: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onClick}
      className="group flex items-center gap-2 text-muted transition-colors duration-150 hover:text-fg"
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 flex-none border-b-[1.5px] border-r-[1.5px] border-current transition-transform duration-200 ${
          open ? "rotate-45" : "-rotate-45"
        }`}
      />
      {children}
    </button>
  );
}
