"use client";

import { useElementWidth } from "@/lib/hooks";
import { niceTicks, type ActivityBucket } from "@/lib/insights";

const DEFAULT_HEIGHT = 176;
const LANE_LEFT = 30;
const PAD_TOP = 8;
const PAD_BOTTOM = 24;
const LABEL_SPACING = 72;

// Columnas cuadradas de una sola serie: cuántos hallazgos se detectaron por período.
// Sin eje Y dibujado, 3-5 líneas tenues, marcas redondas por encima del máximo y
// etiquetas X en un paso regular que termina en el último período.
export default function ActivityChart({
  buckets,
  noun,
  height = DEFAULT_HEIGHT,
}: {
  buckets: ActivityBucket[];
  noun: "hora" | "día";
  height?: number;
}) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const max = Math.max(0, ...buckets.map((b) => b.count));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];

  const plotW = Math.max(0, width - LANE_LEFT);
  const plotH = height - PAD_TOP - PAD_BOTTOM;
  const slot = buckets.length ? plotW / buckets.length : 0;
  const barW = Math.min(28, Math.max(3, slot * 0.62));
  const every = Math.max(1, Math.ceil(LABEL_SPACING / Math.max(slot, 1)));
  const y = (value: number) => PAD_TOP + plotH - (value / top) * plotH;
  const total = buckets.reduce((sum, b) => sum + b.count, 0);

  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <svg
          className="chart block"
          width={width}
          height={height}
          role="img"
          aria-label={`${total} hallazgos detectados, agrupados por ${noun}; el máximo es ${max} por ${noun}.`}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={LANE_LEFT}
                x2={width}
                y1={y(tick)}
                y2={y(tick)}
                stroke={tick === 0 ? "var(--control)" : "var(--grid)"}
                strokeWidth={1}
                strokeDasharray={tick === 0 ? undefined : "3 3"}
              />
              <text x={LANE_LEFT - 10} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">
                {tick}
              </text>
            </g>
          ))}

          {buckets.map((bucket, i) => {
            if (bucket.count === 0) return null;
            const h = (bucket.count / top) * plotH;
            const x = LANE_LEFT + slot * i + (slot - barW) / 2;
            return (
              <g key={i} className="bar">
                <rect
                  className="grow-y"
                  style={{ ["--i" as string]: i }}
                  x={x}
                  y={y(bucket.count)}
                  width={barW}
                  height={h}
                  fill="var(--accent)"
                />
                <title>{`${bucket.count} ${bucket.count === 1 ? "hallazgo" : "hallazgos"} · ${bucket.label}`}</title>
              </g>
            );
          })}

          {buckets.map((bucket, i) => {
            if ((buckets.length - 1 - i) % every !== 0) return null;
            const cx = LANE_LEFT + slot * i + slot / 2;
            if (cx - 22 < LANE_LEFT - 6) return null;
            return (
              <text key={i} x={cx} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--muted)">
                {bucket.label}
              </text>
            );
          })}
        </svg>
      )}
    </div>
  );
}
