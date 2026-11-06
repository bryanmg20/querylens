"use client";

import { SEVERITY_KEYS, SEVERITY_META, countBySeverity } from "@/lib/insights";
import { useCountUp } from "@/lib/hooks";
import { timeAgo } from "@/lib/format";
import type { Finding } from "@/types/database";

// Cuántos hallazgos hay y qué tan graves: una barra al 100% con costuras de 2px
// y la leyenda convertida en tabla con las cantidades.
export default function SeverityPanel({ findings, serverTime }: { findings: Finding[]; serverTime: string }) {
  const latest = findings.reduce<Finding | null>(
    (best, f) => (best == null || f.detectado_en > best.detectado_en ? f : best),
    null
  );
  const counts = countBySeverity(findings);
  const total = findings.length;
  const shown = useCountUp(total);
  const rows = SEVERITY_KEYS.filter((key) => key !== "other" || counts.other > 0);

  return (
    <section className="card flex flex-col p-6" aria-labelledby="severity-title">
      <h2 id="severity-title" className="card-title">
        Severidad
      </h2>

      <p className="mt-4 flex items-baseline gap-2">
        <span className="text-4xl font-semibold leading-none tracking-[-0.02em]">{shown}</span>
        <span className="text-[13px] text-muted">{total === 1 ? "hallazgo" : "hallazgos"}</span>
      </p>

      <div
        className="mt-4 flex h-3 w-full gap-0.5 overflow-hidden rounded-sm bg-raised"
        role="img"
        aria-label={rows.map((key) => `${SEVERITY_META[key].label}: ${counts[key]}`).join(", ")}
      >
        {rows
          .filter((key) => counts[key] > 0)
          .map((key, i) => (
            <div
              key={key}
              className="grow-x h-full"
              style={{ flexGrow: counts[key], flexBasis: 0, background: SEVERITY_META[key].mark, ["--i" as string]: i }}
            />
          ))}
      </div>

      <table className="mt-3 w-full text-[13px]">
        <tbody>
          {rows.map((key) => (
            <tr key={key} className="h-7">
              <td className="w-4">
                <span className="block h-2 w-2 rounded-full" style={{ background: SEVERITY_META[key].mark }} />
              </td>
              <td className="text-muted">{SEVERITY_META[key].label}</td>
              <td className="text-right font-medium">{counts[key]}</td>
              <td className="w-14 text-right text-muted">
                {total > 0 ? `${Math.round((counts[key] / total) * 100)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {latest && (
        <div className="mt-auto pt-4">
          <p className="border-t border-rule pt-4 text-[13px] text-muted">
            Último hallazgo {timeAgo(latest.detectado_en, serverTime)}:{" "}
            <code className="font-mono text-fg">{latest.antipatron}</code>
          </p>
        </div>
      )}
    </section>
  );
}
