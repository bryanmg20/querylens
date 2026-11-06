// Cálculos sobre los hallazgos que alimentan las gráficas. Todo sale de los
// datos de la API: no se inventa ningún valor.
import type { Finding } from "@/types/database";

export type SeverityKey = "high" | "medium" | "low" | "other";

export const SEVERITY_KEYS: SeverityKey[] = ["high", "medium", "low", "other"];

export const SEVERITY_META: Record<SeverityKey, { label: string; pill: string; mark: string }> = {
  high: { label: "Alta", pill: "pill-neg", mark: "var(--negative)" },
  medium: { label: "Media", pill: "pill-warn", mark: "var(--warning)" },
  low: { label: "Baja", pill: "pill-neu", mark: "var(--neutral-mark)" },
  other: { label: "Otra", pill: "pill-neu", mark: "var(--control)" },
};

export function severityKey(raw: string): SeverityKey {
  return raw === "high" || raw === "medium" || raw === "low" ? raw : "other";
}

export function countBySeverity(findings: Finding[]): Record<SeverityKey, number> {
  const counts: Record<SeverityKey, number> = { high: 0, medium: 0, low: 0, other: 0 };
  for (const finding of findings) counts[severityKey(finding.severidad)] += 1;
  return counts;
}

export function topPatterns(findings: Finding[], limit: number): { label: string; value: number }[] {
  const counts = new Map<string, number>();
  for (const finding of findings) counts.set(finding.antipatron, (counts.get(finding.antipatron) ?? 0) + 1);
  return [...counts.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
    .slice(0, limit);
}

export interface ActivityBucket {
  label: string;
  count: number;
}

export interface Activity {
  buckets: ActivityBucket[];
  windowLabel: string;
  unit: "hora" | "día";
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const MAX_DAYS = 30;

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

// Detecciones por hora (si todo cayó en las últimas 24 h) o por día (hasta 30),
// contadas hacia atrás desde la hora del servidor.
export function buildActivity(findings: Finding[], nowIso: string): Activity {
  const now = new Date(nowIso).getTime();
  const times = findings.map((f) => new Date(f.detectado_en).getTime()).filter(Number.isFinite);
  const oldest = times.length ? Math.min(...times) : now;
  const hourly = now - oldest <= DAY;

  if (hourly) {
    const lastStart = Math.floor(now / HOUR) * HOUR;
    const starts = Array.from({ length: 24 }, (_, i) => lastStart - (23 - i) * HOUR);
    const counts = new Array(24).fill(0);
    for (const t of times) {
      const index = Math.floor((t - starts[0]) / HOUR);
      if (index >= 0 && index < 24) counts[index] += 1;
    }
    return {
      unit: "hora",
      windowLabel: "Últimas 24 horas",
      buckets: starts.map((start, i) => ({
        label: new Date(start).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hour12: false }),
        count: counts[i],
      })),
    };
  }

  const days = Math.min(MAX_DAYS, Math.ceil((now - oldest) / DAY) + 1);
  const today = startOfDay(now);
  const starts = Array.from({ length: days }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (days - 1 - i));
    return d.getTime();
  });
  const counts = new Array(days).fill(0);
  for (const t of times) {
    const index = Math.round((startOfDay(t) - starts[0]) / DAY);
    if (index >= 0 && index < days) counts[index] += 1;
  }
  return {
    unit: "día",
    windowLabel: `Últimos ${days} días`,
    buckets: starts.map((start, i) => ({
      label: new Date(start).toLocaleDateString("es-CO", { day: "numeric", month: "short" }).replace(".", ""),
      count: counts[i],
    })),
  };
}

// Marcas redondas del eje Y que terminan por encima del máximo (nunca pegado al borde).
export function niceTicks(max: number): number[] {
  const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
  const base = Math.max(max, 2);
  const step = steps.find((s) => base / s <= 4) ?? 1000;
  let top = Math.ceil(base / step) * step;
  if (top <= base) top += step;
  const ticks: number[] = [];
  for (let v = 0; v <= top; v += step) ticks.push(v);
  return ticks;
}
