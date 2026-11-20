import { buildActivity, topPatterns } from "@/lib/insights";
import type { ActivityPoint, Finding } from "@/types/database";
import ActivityChart from "./charts/ActivityChart";
import BarList from "./charts/BarList";

// Dos preguntas seguidas: cuándo se detectaron y qué antipatrones se repiten más.
export function ActivityPanel({
  points,
  serverTime,
  chartHeight,
}: {
  points: ActivityPoint[];
  serverTime: string;
  chartHeight?: number;
}) {
  const activity = buildActivity(points, serverTime);

  return (
    <section className="card p-5" aria-labelledby="activity-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="activity-title" className="card-title">
          Detecciones en el tiempo
        </h2>
        <span className="text-[13px] text-muted">{activity.windowLabel}</span>
      </div>
      <div className="mt-4">
        <ActivityChart buckets={activity.buckets} noun={activity.unit} height={chartHeight} />
      </div>
    </section>
  );
}

export function PatternsPanel({ findings }: { findings: Finding[] }) {
  const rows = topPatterns(findings, 5);

  return (
    <section className="card p-6" aria-labelledby="patterns-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="patterns-title" className="card-title">
          Antipatrones frecuentes
        </h2>
        <span className="text-[13px] text-muted">{rows.length === 1 ? "1 tipo" : `${rows.length} tipos`}</span>
      </div>
      <div className="mt-4">
        <BarList rows={rows} />
      </div>
    </section>
  );
}
