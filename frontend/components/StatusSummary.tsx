import { formatDateTime, secondsBetween, timeAgo } from "@/lib/format";
import type { DiagnosticsResponse } from "@/types/database";

// Si el último ciclo terminó hace más que esto, el pipeline no está corriendo
// para esta base (el intervalo normal es de ~10 s).
const STALE_AFTER_SECONDS = 5 * 60;

const STATUS_STYLE = {
  ok: { label: "Extracción correcta", classes: "bg-signal-soft text-signal" },
  degraded: { label: "Extracción incompleta", classes: "bg-warn-soft text-warn" },
  failed: { label: "Extracción fallida", classes: "bg-alert-soft text-alert" },
} as const;

export default function StatusSummary({ data }: { data: DiagnosticsResponse }) {
  const { last_run: lastRun } = data.health;
  const stale =
    lastRun != null && secondsBetween(lastRun.finished_at, data.server_time) > STALE_AFTER_SECONDS;

  return (
    <section className="rounded border border-graphite-200 bg-surface px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg text-ink">{data.connection_name}</h2>
          <p className="mt-0.5 font-mono text-xs text-graphite-600">
            {data.database_identifier} · {data.engine}
            {lastRun?.engine_version ? ` ${lastRun.engine_version}` : ""}
          </p>
        </div>
        <span
          className={`rounded px-2 py-1 font-mono text-xs ${
            data.is_active ? "bg-signal-soft text-signal" : "bg-alert-soft text-alert"
          }`}
        >
          {data.is_active ? "disponible" : "no disponible"}
        </span>
      </div>

      <div className="mt-4 border-t border-graphite-200 pt-3 text-sm">
        {lastRun == null ? (
          <p className="text-graphite-600">
            Esperando la primera extracción. Los diagnósticos aparecerán cuando el pipeline lea esta base.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className={`rounded px-2 py-1 text-xs ${STATUS_STYLE[lastRun.status].classes}`}>
              {STATUS_STYLE[lastRun.status].label}
            </span>
            <span className="text-graphite-600">
              Último ciclo {timeAgo(lastRun.finished_at, data.server_time)}{" "}
              <span className="font-mono text-xs">({formatDateTime(lastRun.finished_at)})</span>
            </span>
            {lastRun.sections_failed.length > 0 && (
              <span className="font-mono text-xs text-warn">
                con error: {lastRun.sections_failed.join(", ")}
              </span>
            )}
          </div>
        )}

        {stale && (
          <p className="mt-3 rounded border-l-2 border-warn bg-warn-soft px-3 py-2 text-xs text-ink">
            El pipeline no ha extraído esta base en los últimos 5 minutos. Los datos que ves pueden estar
            desactualizados.
          </p>
        )}
      </div>
    </section>
  );
}
