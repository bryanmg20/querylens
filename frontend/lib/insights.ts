// Cálculos sobre los hallazgos que alimentan las gráficas. Todo sale de los
// datos de la API: no se inventa ningún valor. Cada hallazgo es un problema
// (no una detección): los conteos por severidad y por patrón cuentan problemas.
import { getDoc } from "@/lib/antipatterns";
import type { ActivityPoint, Finding } from "@/types/database";

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
  // Etiqueta: el nombre en español del catálogo; el identificador interno solo si no está.
  return [...counts.entries()]
    .map(([id, value]) => ({ label: getDoc(id)?.title ?? id, value }))
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

// Detecciones por hora de las últimas 24 h, contadas hacia atrás desde la hora del servidor. La API
// ya las trae sumadas por hora (todas las detecciones, no solo las de los hallazgos de la lista);
// las horas sin detecciones no vienen y quedan en 0.
export function buildActivity(points: ActivityPoint[], nowIso: string): Activity {
  const now = new Date(nowIso).getTime();
  const lastStart = Math.floor(now / HOUR) * HOUR;
  const starts = Array.from({ length: 24 }, (_, i) => lastStart - (23 - i) * HOUR);
  const counts = new Array(24).fill(0);
  for (const point of points) {
    const t = new Date(point.hora).getTime();
    if (!Number.isFinite(t)) continue;
    const index = Math.floor((t - starts[0]) / HOUR);
    if (index >= 0 && index < 24) counts[index] += point.detecciones;
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

// Detecciones de la sesión por severidad en palabras ("alta 200, media 880"), de la más alta a la
// más baja. null si tuvo una sola severidad: ya la dice la etiqueta.
export function severityBreakdown(finding: Finding): string | null {
  const entries = Object.entries(finding.por_severidad ?? {}).filter(([, n]) => n > 0);
  if (entries.length < 2) return null;
  const order = (key: string) => SEVERITY_KEYS.indexOf(severityKey(key));
  return entries
    .sort(([a], [b]) => order(a) - order(b))
    .map(([key, n]) => `${SEVERITY_META[severityKey(key)].label.toLowerCase()} ${formatCount(n)}`)
    .join(", ");
}

export function formatCount(n: number): string {
  return n.toLocaleString("es-CO");
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
