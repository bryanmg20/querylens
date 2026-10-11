import { formatDateTime, timeAgo } from "@/lib/format";
import type { HealthIssue } from "@/types/database";

const SEVERITY_STYLE = {
  blocking: { label: "Bloqueante", border: "border-l-alert", badge: "bg-alert-soft text-alert" },
  degraded: { label: "Degradado", border: "border-l-warn", badge: "bg-warn-soft text-warn" },
  info: { label: "Información", border: "border-l-graphite-300", badge: "bg-paper text-graphite-600" },
} as const;

// Problemas de extracción de la base del cliente. message y remediation ya
// vienen redactados en español desde el pipeline: se muestran tal cual.
export default function HealthPanel({
  issues,
  serverTime,
}: {
  issues: HealthIssue[];
  serverTime: string;
}) {
  const open = issues.filter((issue) => issue.resolved_at == null);
  const resolved = issues.filter((issue) => issue.resolved_at != null);

  return (
    <section>
      <h2 className="mb-3 text-lg text-ink">Salud de la extracción</h2>

      {open.length === 0 ? (
        <p className="rounded border border-graphite-200 bg-surface px-4 py-3 text-sm text-graphite-600">
          Sin problemas abiertos.
        </p>
      ) : (
        <ul className="space-y-3">
          {open.map((issue) => (
            <IssueCard key={issue.id} issue={issue} serverTime={serverTime} />
          ))}
        </ul>
      )}

      {resolved.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-graphite-600">
            Resueltos recientemente ({resolved.length})
          </summary>
          <ul className="mt-3 space-y-3 opacity-70">
            {resolved.map((issue) => (
              <IssueCard key={issue.id} issue={issue} serverTime={serverTime} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function IssueCard({ issue, serverTime }: { issue: HealthIssue; serverTime: string }) {
  const style = SEVERITY_STYLE[issue.severity] ?? SEVERITY_STYLE.info;

  return (
    <li className={`rounded border border-graphite-200 border-l-4 ${style.border} bg-surface px-4 py-3`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs ${style.badge}`}>{style.label}</span>
        <span className="font-mono text-xs text-graphite-600">{issue.code}</span>
        {issue.section && <span className="font-mono text-xs text-graphite-600">· {issue.section}</span>}
      </div>

      <p className="mt-2 text-sm text-ink">{issue.message}</p>
      {issue.remediation && (
        <p className="mt-2 whitespace-pre-wrap rounded bg-paper px-3 py-2 font-mono text-xs text-ink">
          {issue.remediation}
        </p>
      )}

      <p className="mt-2 text-xs text-graphite-600">
        Visto {issue.occurrences} {issue.occurrences === 1 ? "vez" : "veces"} · última{" "}
        {timeAgo(issue.last_seen, serverTime)}
        {issue.resolved_at && ` · resuelto el ${formatDateTime(issue.resolved_at)}`}
      </p>
    </li>
  );
}
