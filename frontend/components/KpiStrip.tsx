"use client";

import { findingTables } from "@/lib/evidence";
import { timeAgo } from "@/lib/format";
import { useCountUp } from "@/lib/hooks";
import { SEVERITY_META, countBySeverity, type SeverityKey } from "@/lib/insights";
import type { DiagnosticsResponse } from "@/types/database";

const PIPELINE = {
  ok: { label: "Correcto", color: "var(--pill-pos-ink)" },
  degraded: { label: "Incompleto", color: "var(--pill-warn-ink)" },
  failed: { label: "Fallido", color: "var(--pill-neg-ink)" },
} as const;

const SUBLINES: Record<"high" | "medium" | "low", string> = {
  high: "Atender primero",
  medium: "Planificar",
  low: "Informativo",
};

export type SeverityFilter = "all" | SeverityKey;

// Un solo contenedor dividido en cinco celdas. Las tres de severidad también filtran la lista:
// la seleccionada lleva una línea de 1px en el acento bajo su etiqueta.
export default function KpiStrip({
  data,
  severity,
  onSeverity,
}: {
  data: DiagnosticsResponse;
  severity: SeverityFilter;
  onSeverity: (next: SeverityFilter) => void;
}) {
  const counts = countBySeverity(data.diagnostics);
  const tables = [...new Set(data.diagnostics.flatMap(findingTables))];
  const run = data.health.last_run;
  const pipeline = run ? PIPELINE[run.status] : null;

  return (
    <div className="grid grid-cols-2 overflow-hidden rounded border border-line bg-surface sm:grid-cols-3 lg:grid-cols-5">
      {(["high", "medium", "low"] as const).map((key) => (
        <SeverityCell
          key={key}
          label={`Severidad ${SEVERITY_META[key].label.toLowerCase()}`}
          sub={SUBLINES[key]}
          value={counts[key]}
          color={SEVERITY_META[key].mark}
          selected={severity === key}
          onClick={() => onSeverity(severity === key ? "all" : key)}
        />
      ))}

      <div className="min-w-0 border-t border-line p-4 sm:border-l lg:border-t-0">
        <p className="text-[13px] text-muted">Tablas afectadas</p>
        <TableCount value={tables.length} />
        <p className="mt-1 truncate font-mono text-[13px] text-muted" title={tables.join(", ")}>
          {tables.length ? tables.slice(0, 3).join(" · ") : "—"}
        </p>
      </div>

      <div className="col-span-2 min-w-0 border-t border-line p-4 sm:col-span-1 sm:border-l lg:border-t-0">
        <p className="text-[13px] text-muted">Pipeline</p>
        <p className="mt-1.5 text-[20px] font-semibold leading-7" style={{ color: pipeline?.color }}>
          {pipeline?.label ?? "En espera"}
        </p>
        <p className="mt-1 truncate text-[13px] text-muted">
          {run ? `Último ciclo ${timeAgo(run.finished_at, data.server_time)}` : "Aún sin extracción"}
        </p>
      </div>
    </div>
  );
}

function TableCount({ value }: { value: number }) {
  const shown = useCountUp(value, 500);
  return <p className="mt-1.5 text-[20px] font-semibold leading-7">{shown}</p>;
}

function SeverityCell({
  label,
  sub,
  value,
  color,
  selected,
  onClick,
}: {
  label: string;
  sub: string;
  value: number;
  color: string;
  selected: boolean;
  onClick: () => void;
}) {
  const shown = useCountUp(value, 600);
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className="group relative min-w-0 border-line p-4 text-left transition-colors duration-150 first:border-l-0 [&:not(:first-child)]:sm:border-l [@media(hover:hover)]:hover:bg-raised"
    >
      <span className="flex items-center gap-2 text-[13px] text-muted">
        <span className="h-2 w-2 flex-none rounded-full" style={{ background: color }} />
        <span className="relative">
          {label}
          <span
            aria-hidden="true"
            className={`absolute inset-x-0 -bottom-1 h-px origin-left bg-accent transition-transform duration-300 ease-[var(--ease-out)] ${
              selected ? "scale-x-100" : "scale-x-0"
            }`}
          />
        </span>
      </span>
      <span className="mt-1.5 block text-[20px] font-semibold leading-7">{shown}</span>
      <span className="mt-1 block truncate text-[13px] text-muted">{sub}</span>
    </button>
  );
}
