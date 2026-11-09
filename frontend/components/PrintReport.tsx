import { getDoc } from "@/lib/antipatterns";
import { findingMetrics, findingQuery, findingTables } from "@/lib/evidence";
import { formatDateTime } from "@/lib/format";
import { SEVERITY_KEYS, SEVERITY_META, countBySeverity, formatCount, severityBreakdown, severityKey } from "@/lib/insights";
import type { DiagnosticsResponse, Finding } from "@/types/database";

// El informe que sale al imprimir la página de Diagnósticos (Exportar PDF). En pantalla está
// oculto; al imprimir es lo único que se ve. Lleva los hallazgos que estén filtrados, en texto y
// con colores fijos (negro sobre blanco) para que no dependa del tema ni de los fondos del navegador.
export default function PrintReport({
  data,
  findings,
  filterNote,
}: {
  data: DiagnosticsResponse;
  findings: Finding[];
  filterNote: string | null;
}) {
  const counts = countBySeverity(findings);

  return (
    <div className="hidden bg-white text-[12px] leading-[1.45] text-black print:block">
      <header className="border-b border-black pb-3">
        <p className="text-[11px] uppercase tracking-wide text-neutral-600">QueryLens</p>
        <h1 className="mt-1 text-[22px] font-semibold">Informe de hallazgos</h1>
        <p className="mt-1 text-[12px]">
          {data.connection_name} · {data.engine === "mysql" ? "MySQL" : "PostgreSQL"}
        </p>
        <p className="text-[11px] text-neutral-600">
          Generado el {formatDateTime(new Date().toISOString())} · datos del servidor al {formatDateTime(data.server_time)}
        </p>
        <p className="text-[11px] text-neutral-600">
          Sesión de detección desde el {formatDateTime(data.detection_session_started_at)}: cada hallazgo cuenta sus
          detecciones de la sesión y en total, con la severidad más alta que tuvo en la sesión.
        </p>
      </header>

      <section className="mt-4">
        <p className="text-[13px]">
          <strong>{findings.length}</strong> {findings.length === 1 ? "hallazgo" : "hallazgos"}
          {SEVERITY_KEYS.filter((k) => counts[k] > 0).map((k) => (
            <span key={k}>
              {" · "}
              {counts[k]} {SEVERITY_META[k].label.toLowerCase()}
            </span>
          ))}
        </p>
        {filterNote && <p className="mt-1 text-[11px] text-neutral-600">Filtros aplicados: {filterNote}</p>}
      </section>

      {findings.length === 0 ? (
        <p className="mt-6">No hay hallazgos para los filtros aplicados.</p>
      ) : (
        <div className="mt-4 space-y-4">
          {findings.map((f) => {
            const doc = getDoc(f.antipatron);
            const tables = findingTables(f);
            const query = findingQuery(f);
            const metrics = findingMetrics(f);
            const breakdown = severityBreakdown(f);
            const current = severityKey(f.severidad_actual) !== severityKey(f.severidad)
              ? SEVERITY_META[severityKey(f.severidad_actual)].label.toLowerCase()
              : null;
            return (
              <article key={f.id} className="break-inside-avoid border border-neutral-400 border-l-[4px] border-l-black p-3">
                <header className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <h2 className="text-[14px] font-semibold">
                    {doc ? `${doc.code} · ${doc.title}` : f.antipatron}
                  </h2>
                  <span className="font-semibold">
                    Severidad {SEVERITY_META[severityKey(f.severidad)].label.toLowerCase()}
                    {current && ` (ahora ${current})`}
                  </span>
                </header>
                <p className="mt-0.5 text-[11px] text-neutral-600">
                  {[tables.length > 0 && `${tables.length === 1 ? "Tabla" : "Tablas"} ${tables.join(", ")}`, f.query_id && `query ${f.query_id}`]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                <p className="text-[11px] text-neutral-600">
                  {formatCount(f.veces_sesion)} {f.veces_sesion === 1 ? "vez" : "veces"} en esta sesión
                  {breakdown && ` (${breakdown})`} · {formatCount(f.veces_total)} en total desde el{" "}
                  {formatDateTime(f.primera_deteccion)} · última {formatDateTime(f.detectado_en)}
                </p>

                <p className="mt-2">{f.explicacion}</p>

                {query && (
                  <pre className="mt-2 whitespace-pre-wrap break-words border border-neutral-300 p-2 font-mono text-[10.5px] leading-[1.35]">{query}</pre>
                )}

                {metrics.length > 0 && (
                  <p className="mt-2 text-[11px]">
                    {metrics.map((m, i) => (
                      <span key={m.label}>
                        {i > 0 && " · "}
                        {m.label}: <strong>{m.value}</strong>
                      </span>
                    ))}
                  </p>
                )}

                <p className="mt-2">
                  <strong>Recomendación: </strong>
                  {f.recomendacion}
                </p>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
