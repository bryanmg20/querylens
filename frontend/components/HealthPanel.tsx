import Link from "next/link";
import { formatDateTime, timeAgo } from "@/lib/format";
import { guideHref, sectionForHealthCode } from "@/lib/prepararBase";
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
  engine,
}: {
  issues: HealthIssue[];
  serverTime: string;
  engine?: string;
}) {
  const open = issues.filter((issue) => issue.resolved_at == null);
  const resolved = issues.filter((issue) => issue.resolved_at != null);

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg text-ink">Salud de la extracción</h2>
        <Link href={guideHref({ engine })} className="text-[13px] text-muted underline-offset-2 hover:text-fg hover:underline">
          Guía para preparar la base
        </Link>
      </div>

      {open.length === 0 ? (
        <p className="swap-in rounded border border-graphite-200 bg-surface px-4 py-3 text-sm text-graphite-600">
          Sin problemas abiertos.
        </p>
      ) : (
        <ul className="space-y-3">
          {open.map((issue) => (
            <IssueCard key={issue.id} issue={issue} serverTime={serverTime} engine={engine} />
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

function IssueCard({ issue, serverTime, engine }: { issue: HealthIssue; serverTime: string; engine?: string }) {
  const style = SEVERITY_STYLE[issue.severity] ?? SEVERITY_STYLE.info;
  // Algunos problemas se resuelven con un paso de la guía de preparar la base.
  const guideSection = sectionForHealthCode(issue.code);

  return (
    <li className={`swap-in rounded border border-graphite-200 border-l-4 ${style.border} bg-surface px-4 py-3`}>
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

      {guideSection && (
        <p className="mt-2 text-sm">
          <Link href={guideHref({ engine, section: guideSection })} className="font-medium text-accent underline-offset-2 hover:underline">
            Ver cómo resolverlo en la guía →
          </Link>
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
