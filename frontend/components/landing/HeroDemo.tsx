"use client";

import { useEffect, useRef, useState } from "react";
import { getDoc } from "@/lib/antipatterns";
import { FEED_POOL, type DemoSeverity } from "@/lib/landing-data";
import { useCountUp } from "@/lib/hooks";
import { SEVERITY_META } from "@/lib/insights";

const ROW = 56;
const VISIBLE = 4;
const TICK_MS = 3400;
const BASE: Record<DemoSeverity, number> = { high: 2, medium: 3, low: 1 };
const BAR_START = [1, 0, 2, 1, 0, 1, 3, 1, 2, 0, 1, 2];
const BAR_MAX = 8;

interface FeedItem {
  uid: number;
  severity: DemoSeverity;
  name: string;
  subject: string;
}

// Fragmento vivo del producto con datos de ejemplo: cada pocos segundos "llega"
// un hallazgo, sube el contador de su severidad y crece la última barra.
export default function HeroDemo({ running = true }: { running?: boolean }) {
  const [mounted, setMounted] = useState(false);
  const [items, setItems] = useState<FeedItem[]>(() =>
    [3, 2, 1, 0].map((i) => ({ uid: i, ...FEED_POOL[i] }))
  );
  const [counts, setCounts] = useState(BASE);
  const [bars, setBars] = useState(BAR_START);
  const next = useRef(4);

  useEffect(() => {
    setMounted(true);
  }, []);

  // El feed solo avanza mientras la diapositiva está a la vista.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      const uid = next.current++;
      const entry = FEED_POOL[uid % FEED_POOL.length];
      setItems((prev) => [{ uid, ...entry }, ...prev].slice(0, VISIBLE + 1));

      // Tras una vuelta completa del repertorio, el ejemplo vuelve a su punto de partida.
      if ((uid - 3) % FEED_POOL.length === 0) {
        setCounts({ ...BASE, [entry.severity]: BASE[entry.severity] + 1 });
        setBars([...BAR_START.slice(0, 11), BAR_START[11] + 1]);
      } else {
        setCounts((c) => ({ ...c, [entry.severity]: c[entry.severity] + 1 }));
        setBars((b) => [...b.slice(0, 11), b[11] + 1]);
      }
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [running]);

  const total = counts.high + counts.medium + counts.low;
  const shownTotal = useCountUp(total, 500);

  return (
    <div data-ui className="rounded-lg border border-line bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <p className="flex items-center gap-2.5 text-[16px] font-medium">
          <span className="relative flex h-2 w-2">
            <span className="live-dot relative h-2 w-2 rounded-full bg-positive" />
          </span>
          ventas_prod
          <span className="pill-neu">postgresql 16</span>
        </p>
        <span className="text-[13px] text-muted">Datos de ejemplo</span>
      </div>

      <div className="mt-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <p className="flex items-baseline gap-2">
          <span className="text-[60px] font-semibold leading-none tracking-[-0.03em]">{shownTotal}</span>
          <span className="text-[13px] text-muted">hallazgos</span>
        </p>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 pb-1 text-[13px] text-muted">
          {(["high", "medium", "low"] as const).map((key) => (
            <li key={key} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: SEVERITY_META[key].mark }} />
              {SEVERITY_META[key].label} <span className="font-medium text-fg">{counts[key]}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-4 flex h-2.5 w-full gap-0.5 overflow-hidden rounded-sm bg-raised" aria-hidden="true">
        {(["high", "medium", "low"] as const).map((key) => (
          <div
            key={key}
            className="h-full transition-[flex-grow] duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]"
            style={{ flexGrow: counts[key], flexBasis: 0, background: SEVERITY_META[key].mark }}
          />
        ))}
      </div>

      <div className="mt-6">
        <p className="text-[13px] text-muted">Detecciones por hora</p>
        <div className="mt-3 flex h-[72px] items-end gap-1.5" role="img" aria-label="Detecciones por hora, últimas 12 horas">
          {bars.map((value, i) => (
            <div key={i} className="flex h-full flex-1 items-end">
              <div
                className="h-full w-full origin-bottom bg-accent transition-transform duration-[600ms] ease-[cubic-bezier(0.23,1,0.32,1)]"
                style={{
                  transform: `scaleY(${mounted ? Math.min(value, BAR_MAX) / BAR_MAX : 0})`,
                  transitionDelay: mounted ? `${i * 30}ms` : "0ms",
                }}
              />
            </div>
          ))}
        </div>
      </div>

      <ul className="relative mt-6" style={{ height: ROW * VISIBLE }} aria-label="Últimos hallazgos de ejemplo">
        {items.map((item, index) => {
          const meta = SEVERITY_META[item.severity];
          return (
            <li
              key={item.uid}
              className="feed-row absolute inset-x-0 top-0 flex items-center gap-4 border-t border-rule"
              style={{
                height: ROW,
                transform: `translateY(${index * ROW}px)`,
                opacity: index >= VISIBLE ? 0 : 1,
              }}
            >
              <span className="flex w-14 flex-none">
                <span className={meta.pill}>{meta.label}</span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{getDoc(item.name)?.title ?? item.name}</span>
                <span className="block truncate text-[13px] text-muted">{item.subject}</span>
              </span>
              {index === 0 && <span className="pill-pos flex-none">Nuevo</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
