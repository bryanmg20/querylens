import { formatDateTime, secondsBetween, timeAgo } from "@/lib/format";
import type { DiagnosticsResponse } from "@/types/database";
import RefreshRing from "./charts/RefreshRing";

// Si el último ciclo terminó hace más que esto, el pipeline no está corriendo
// para esta base (el intervalo normal es de ~10 s).
const STALE_AFTER_SECONDS = 5 * 60;

const STATUS_STYLE = {
  ok: { label: "Extracción correcta", dot: "var(--positive)" },
  degraded: { label: "Extracción incompleta", dot: "var(--warning)" },
  failed: { label: "Extracción fallida", dot: "var(--negative)" },
} as const;

// El pulso de extracción: el estado del último ciclo, la cuenta atrás hasta la
// siguiente lectura y cada sección leída como un segmento (ok o con error).
export default function StatusSummary({
  data,
  pollSeconds,
  cycle,
}: {
  data: DiagnosticsResponse;
  pollSeconds: number;
  cycle: number;
}) {
  const { last_run: lastRun } = data.health;
  const stale =
    lastRun != null && secondsBetween(lastRun.finished_at, data.server_time) > STALE_AFTER_SECONDS;
  const sections = lastRun
    ? [
        ...lastRun.sections_ok.map((name) => ({ name, ok: true })),
        ...lastRun.sections_failed.map((name) => ({ name, ok: false })),
      ]
    : [];
  const openIssues = data.health.issues.filter((issue) => issue.resolved_at == null).length;
  const status = lastRun ? STATUS_STYLE[lastRun.status] : null;

  return (
    <section className="card flex flex-col p-5" aria-labelledby="pulse-title">
      <div className="flex items-center justify-between gap-3">
        <h2 id="pulse-title" className="card-title">
          Pulso de extracción
        </h2>
        <span className={data.is_active ? "pill-pos" : "pill-neg"}>
          {data.is_active ? "Base disponible" : "Base no disponible"}
        </span>
      </div>

      <div className="mt-5 flex items-center gap-5">
        <RefreshRing cycle={cycle} seconds={pollSeconds} />
        <div className="min-w-0">
          {lastRun && status ? (
            <>
              <p className="flex items-center gap-2 text-xl font-semibold leading-7">
                <span className="h-2 w-2 flex-none rounded-full" style={{ background: status.dot }} />
                {status.label}
              </p>
              <p className="mt-0.5 text-[13px] text-muted">
                Último ciclo {timeAgo(lastRun.finished_at, data.server_time)} · {formatDateTime(lastRun.finished_at)}
              </p>
            </>
          ) : !data.monitoring_enabled ? (
            <>
              <p className="text-xl font-semibold leading-7">Monitoreo sin iniciar</p>
              <p className="mt-0.5 text-[13px] text-muted">
                Los diagnósticos aparecerán después de que inicies el monitoreo.
              </p>
            </>
          ) : (
            <>
              <p className="text-xl font-semibold leading-7">Esperando la primera extracción</p>
              <p className="mt-0.5 text-[13px] text-muted">
                Los diagnósticos aparecerán cuando el pipeline lea esta base.
              </p>
            </>
          )}
        </div>
      </div>

      <div className="mt-5">
        {sections.length > 0 ? (
          <ul className="grid grid-cols-[repeat(auto-fit,minmax(132px,1fr))] gap-x-2 gap-y-3">
            {sections.map((section, i) => (
              <li key={section.name} title={section.ok ? "Leída" : "Con error"}>
                <div
                  className="grow-x h-1 rounded-full"
                  style={{ background: section.ok ? "var(--accent)" : "var(--negative)", ["--i" as string]: i }}
                />
                <code
                  className={`mt-2 block truncate font-mono text-[13px] ${section.ok ? "text-muted" : "text-[var(--pill-neg-ink)]"}`}
                >
                  {section.name}
                </code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-frame">Aún no hay secciones leídas.</p>
        )}
      </div>

      {stale && (
        <p className="notice-warn mt-5">
          El pipeline no ha extraído esta base en los últimos 5 minutos. Los datos que ves pueden estar
          desactualizados.
        </p>
      )}

      <div className="mt-auto pt-5">
        <dl className="flex flex-wrap gap-x-8 gap-y-2 border-t border-rule pt-4 text-[13px] text-muted">
          <div className="flex gap-2">
            <dt>Identificador</dt>
            <dd className="text-fg">
              <code className="font-mono">{data.database_identifier}</code>
            </dd>
          </div>
          <div className="flex gap-2">
            <dt>Motor</dt>
            <dd className="text-fg">
              {data.engine}
              {lastRun?.engine_version ? ` ${lastRun.engine_version}` : ""}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt>Problemas abiertos</dt>
            <dd className="text-fg">{openIssues}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
