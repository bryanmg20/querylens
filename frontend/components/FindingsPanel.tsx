import { formatDateTime } from "@/lib/format";
import type { Finding } from "@/types/database";

const SEVERITY_STYLE: Record<string, { label: string; border: string; badge: string }> = {
  high: { label: "Alta", border: "border-l-alert", badge: "bg-alert-soft text-alert" },
  medium: { label: "Media", border: "border-l-warn", badge: "bg-warn-soft text-warn" },
  low: { label: "Baja", border: "border-l-graphite-300", badge: "bg-paper text-graphite-600" },
};
const FALLBACK_STYLE = {
  label: "",
  border: "border-l-graphite-300",
  badge: "bg-paper text-graphite-600",
};

// Antipatrones detectados por el detection_engine, del más reciente al más viejo.
export default function FindingsPanel({
  findings,
  waiting,
}: {
  findings: Finding[];
  waiting: boolean;
}) {
  return (
    <section>
      <h2 className="mb-3 text-lg text-ink">Antipatrones detectados</h2>

      {findings.length === 0 ? (
        <p className="rounded border border-graphite-200 bg-surface px-4 py-3 text-sm text-graphite-600">
          {waiting
            ? "Aún no hay hallazgos: se esperan después de la primera extracción."
            : "No se han detectado antipatrones en esta base de datos."}
        </p>
      ) : (
        <ul className="space-y-3">
          {findings.map((finding) => {
            const style = SEVERITY_STYLE[finding.severidad] ?? FALLBACK_STYLE;
            return (
              <li
                key={finding.id}
                className={`rounded border border-graphite-200 border-l-4 ${style.border} bg-surface px-4 py-3`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded px-2 py-0.5 text-xs ${style.badge}`}>
                    {style.label || finding.severidad}
                  </span>
                  <span className="font-mono text-sm text-ink">{finding.antipatron}</span>
                  {finding.table_name && (
                    <span className="font-mono text-xs text-graphite-600">· tabla {finding.table_name}</span>
                  )}
                  {finding.query_id && (
                    <span className="font-mono text-xs text-graphite-600">· query {finding.query_id}</span>
                  )}
                </div>

                <p className="mt-2 text-sm text-ink">{finding.explicacion}</p>
                <p className="mt-2 rounded bg-signal-soft px-3 py-2 text-sm text-ink">
                  <span className="text-graphite-600">Recomendación: </span>
                  {finding.recomendacion}
                </p>

                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-graphite-600">Evidencia</summary>
                  <pre className="mt-2 max-h-56 overflow-auto rounded bg-paper px-3 py-2 font-mono text-xs text-ink">
                    {JSON.stringify(finding.evidencia, null, 2)}
                  </pre>
                </details>

                <p className="mt-2 text-xs text-graphite-600">
                  Detectado el {formatDateTime(finding.detectado_en)}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
