"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AntipatternDoc, VizConfig } from "@/lib/antipatterns";
import { useElementWidth, useProgress } from "@/lib/hooks";
import { niceTicks } from "@/lib/insights";

export type Mode = "bad" | "fix";

const INT = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const DEC = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 1 });
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
const clamp01 = (n: number) => Math.min(1, Math.max(0, n));
const seg = (p: number, from: number, to: number) => clamp01((p - from) / (to - from));

const READ = "color-mix(in srgb, var(--negative) 60%, transparent)";

// Interruptor Problemática / Corregida: la versión activa se pinta con su color de estado.
export function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  const fixed = mode === "fix";
  return (
    <div className="flex items-center gap-3 text-[13px]">
      <span className={`transition-colors duration-200 ${fixed ? "text-muted" : "text-[var(--pill-neg-ink)]"}`}>Problemática</span>
      <button
        type="button"
        role="switch"
        aria-checked={fixed}
        aria-label="Ver la versión corregida"
        onClick={() => onChange(fixed ? "bad" : "fix")}
        className="relative h-5 w-9 rounded-full bg-raised transition-colors duration-200 active:scale-[0.96]"
      >
        <span
          className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full transition-[transform,background-color] duration-200 ease-[var(--ease-out)]"
          style={{ transform: `translateX(${fixed ? 16 : 0}px)`, background: fixed ? "var(--positive)" : "var(--negative)" }}
        />
      </button>
      <span className={`transition-colors duration-200 ${fixed ? "text-[var(--pill-pos-ink)]" : "text-muted"}`}>Corregida</span>
    </div>
  );
}

// Marco común: interruptor Problemática / Corregida y botón Repetir. La animación arranca
// cuando el marco entra en pantalla y vuelve a empezar con cada cambio de modo.
function VizFrame({ children }: { children: (mode: Mode) => ReactNode }) {
  const [mode, setMode] = useState<Mode>("bad");
  const [run, setRun] = useState(0);
  const [seen, setSeen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { threshold: 0.1 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className="min-w-0 rounded-lg border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ModeSwitch mode={mode} onChange={setMode} />
        <button type="button" onClick={() => setRun((n) => n + 1)} className="btn-text h-8">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5" />
          </svg>
          Repetir
        </button>
      </div>
      <div key={`${mode}-${run}`} className="mt-5">
        {seen ? children(mode) : <div className="min-h-[320px]" />}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "neg" | "pos" }) {
  return (
    <div className="min-w-0 rounded bg-ground px-3 py-2.5">
      <p className="truncate text-[13px] text-muted">{label}</p>
      <p
        className="mt-0.5 truncate text-[18px] font-semibold"
        style={{ color: tone === "neg" ? "var(--pill-neg-ink)" : tone === "pos" ? "var(--pill-pos-ink)" : undefined }}
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- recorrido de tabla (AP-02, 03, 04)

const COLS = 25;
const BLOCKS = 200;
const MATCHES: Record<number, number[]> = {
  1: [120],
  2: [57, 143],
  3: [38, 101, 167],
  5: [88, 89, 90, 91, 92],
};

function ScanViz({ cfg, mode }: { cfg: Extract<VizConfig, { kind: "scan" }>; mode: Mode }) {
  const fixed = mode === "fix";
  const p = useProgress(0, fixed ? 1500 : 3000, fixed ? 350 : 250);
  const matches = MATCHES[cfg.matches] ?? MATCHES[3];
  const lit = fixed ? 0 : Math.floor(p * BLOCKS);
  const jump = fixed ? seg(p, 0.15, 0.4) : 0;
  const found = matches.filter((m) => (fixed ? jump > 0.5 : m < lit)).length;
  const read = fixed ? Math.round(cfg.readFix * easeOut(seg(p, 0.15, 0.6))) : Math.round(cfg.readBad * p);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <p className="text-muted">
          Tabla <code className="font-mono text-fg">{cfg.table}</code> · {BLOCKS} bloques = {INT.format(cfg.rows)} filas
        </p>
        <span className={fixed ? "pill-pos" : "pill-neg"}>
          {fixed ? "Salto directo con el índice" : "Lectura secuencial, bloque por bloque"}
        </span>
      </div>
      {cfg.note && (
        <p className="mt-2 text-[13px]" style={{ color: fixed ? "var(--pill-pos-ink)" : "var(--pill-warn-ink)" }}>
          {fixed ? cfg.note.fix : cfg.note.bad}
        </p>
      )}

      <div
        role="img"
        aria-label={fixed ? "Con índice se leen solo los bloques buscados" : "Sin índice se leen todos los bloques"}
        className="mt-4 grid gap-[3px]"
        style={{ gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: BLOCKS }, (_, i) => {
          const isMatch = matches.includes(i);
          const isFound = isMatch && (fixed ? jump > 0.5 : i < lit);
          const isRead = !fixed && !isMatch && i < lit;
          return (
            <span
              key={i}
              className="block aspect-square rounded-[2px] transition-[background-color,transform] duration-200 ease-out"
              style={{
                background: isFound ? "var(--accent)" : isRead ? READ : "var(--raised)",
                transform: isFound ? "scale(1.35)" : "scale(1)",
                zIndex: isFound ? 1 : 0,
              }}
            />
          );
        })}
      </div>

      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-muted">
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px]" style={{ background: READ }} />Fila leída</li>
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px] bg-accent" />Fila buscada, encontrada</li>
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px] bg-raised" />Fila no leída</li>
      </ul>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <Stat label="Filas leídas" value={INT.format(read)} tone={fixed ? "pos" : "neg"} />
        <Stat label="Filas devueltas" value={`${found} / ${cfg.matches}`} />
        <Stat label="Método de acceso" value={fixed ? "Index Scan" : "Seq Scan"} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- escrituras que cuestan (AP-05)

function WritesViz({ cfg, mode }: { cfg: Extract<VizConfig, { kind: "writes" }>; mode: Mode }) {
  const fixed = mode === "fix";
  const p = useProgress(0, 3200, 250);
  const writes = Math.floor(easeOut(Math.min(1, p / 0.9)) * 120);
  const dropped = fixed && p > 0.5;
  const freed = fixed ? Math.round(cfg.sizeMb * easeOut(seg(p, 0.5, 0.85))) : 0;
  const bar = (v: number) => `${(v / 120) * 100}%`;

  return (
    <div>
      <p className="text-[13px] text-muted">Cada escritura (INSERT, UPDATE, DELETE) actualiza la tabla y todos sus índices.</p>

      <div className="mt-5 space-y-5">
        <div>
          <div className="flex items-baseline justify-between text-[13px]">
            <span>
              Tabla <code className="font-mono">pedidos</code>
            </span>
            <span className="text-muted">{writes} escrituras</span>
          </div>
          <div className="mt-2 h-2.5 rounded-full bg-raised">
            <div className="h-full rounded-full transition-[width] duration-100 ease-linear" style={{ width: bar(writes), background: "var(--text)", opacity: 0.8 }} />
          </div>
        </div>

        <div
          className="transition-[opacity,transform] duration-500 ease-[var(--ease-out)]"
          style={{ opacity: dropped ? 0.25 : 1, transform: dropped ? "translateX(12px)" : "none" }}
        >
          <div className="flex items-baseline justify-between text-[13px]">
            <span className={dropped ? "line-through" : ""}>
              Índice <code className="font-mono">{cfg.index}</code>
            </span>
            <span className="text-muted">{dropped ? "eliminado" : `${writes} actualizaciones`}</span>
          </div>
          <div className="mt-2 h-2.5 rounded-full bg-raised">
            <div
              className="h-full rounded-full transition-[width] duration-100 ease-linear"
              style={{ width: bar(dropped ? Math.min(writes, 60) : writes), background: "var(--warning)" }}
            />
          </div>
        </div>
      </div>

      {dropped && (
        <p className="pop mt-5 inline-block rounded bg-ground px-3 py-2 font-mono text-[13px] text-[var(--pill-pos-ink)]">
          DROP INDEX {cfg.index};
        </p>
      )}

      <div className="mt-5 grid grid-cols-3 gap-3">
        <Stat label="Lecturas del índice" value="0" />
        <Stat label="Escrituras al índice" value={dropped ? "—" : INT.format(writes)} tone={dropped ? undefined : "neg"} />
        <Stat label="Espacio liberado" value={fixed ? `${INT.format(freed)} MB` : "—"} tone={fixed ? "pos" : undefined} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- latencia frente a su histórico (AP-01)

function BaselineViz({ cfg, mode }: { cfg: Extract<VizConfig, { kind: "baseline" }>; mode: Mode }) {
  const fixed = mode === "fix";
  const series = fixed ? cfg.fix : cfg.bad;
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const p = useProgress(0, 2600, 250);

  const H = 230;
  const L = 36;
  const T = 12;
  const B = 28;
  const ticks = niceTicks(Math.max(...cfg.bad, cfg.median * 2) / 10).map((t) => t * 10);
  const top = ticks[ticks.length - 1];
  const x = (i: number) => L + (i / (series.length - 1)) * Math.max(1, width - L - 12);
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const threshold = cfg.median * 2;

  const t = p * (series.length - 1);
  const whole = Math.floor(t);
  const pts: [number, number][] = series.slice(0, whole + 1).map((v, i) => [x(i), y(v)]);
  if (whole < series.length - 1) {
    const frac = t - whole;
    pts.push([x(whole + frac), y(series[whole] + (series[whole + 1] - series[whole]) * frac)]);
  }
  const last = pts[pts.length - 1];
  const lastValue = series[Math.min(series.length - 1, Math.round(t))];
  const over = lastValue >= threshold;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <p className="text-muted">Latencia media por ventana de 60 s</p>
        <span className={fixed ? "pill-pos" : "pill-neg"}>{fixed ? "Vuelve a su nivel habitual" : "Se aleja de su histórico"}</span>
      </div>

      <div ref={ref} className="mt-3">
        {width > 0 && (
          <svg width={width} height={H} role="img" aria-label={`Latencia de la consulta en las últimas ${series.length} ventanas; la mediana previa es ${cfg.median} ms.`}>
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={L} x2={width} y1={y(tick)} y2={y(tick)} stroke={tick === 0 ? "var(--control)" : "var(--grid)"} strokeDasharray={tick === 0 ? undefined : "3 3"} />
                <text x={L - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">{tick}</text>
              </g>
            ))}
            <line x1={L} x2={width} y1={y(cfg.median)} y2={y(cfg.median)} stroke="var(--muted)" strokeDasharray="5 4" />
            <text x={width - 4} y={y(cfg.median) - 6} textAnchor="end" fontSize={11} fill="var(--muted)">mediana previa {DEC.format(cfg.median)} ms</text>
            <line x1={L} x2={width} y1={y(threshold)} y2={y(threshold)} stroke="var(--negative)" strokeDasharray="5 4" opacity={0.8} />
            <text x={width - 4} y={y(threshold) - 6} textAnchor="end" fontSize={11} fill="var(--pill-neg-ink)">2 × la mediana</text>

            <polyline points={pts.map(([px, py]) => `${px},${py}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" />
            {last && <circle cx={last[0]} cy={last[1]} r={5} fill={over ? "var(--negative)" : "var(--accent)"} />}
            {[0, Math.floor((series.length - 1) / 2), series.length - 1].map((i) => (
              <text key={i} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === series.length - 1 ? "end" : "middle"} fontSize={11} fill="var(--muted)">
                {i === series.length - 1 ? "ahora" : `−${series.length - 1 - i} min`}
              </text>
            ))}
          </svg>
        )}
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3">
        <Stat label="Ventana actual" value={`${DEC.format(lastValue)} ms`} tone={over ? "neg" : "pos"} />
        <Stat label="Mediana previa" value={`${DEC.format(cfg.median)} ms`} />
        <Stat label="Razón" value={`${DEC.format(lastValue / cfg.median)} ×`} tone={over ? "neg" : "pos"} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- estimación de filas (AP-08)

const LOG_MAX = 5;
const logWidth = (n: number) => `${(Math.log10(Math.max(n, 1)) / LOG_MAX) * 100}%`;

function EstimateViz({ cfg, mode }: { cfg: Extract<VizConfig, { kind: "estimate" }>; mode: Mode }) {
  const fixed = mode === "fix";
  const p = useProgress(0, 3000, 250);
  const grow = easeOut(seg(p, 0, 0.35));
  const logEst = Math.log10(cfg.estBad) + (fixed ? (Math.log10(cfg.estFix) - Math.log10(cfg.estBad)) * easeOut(seg(p, 0.4, 0.8)) : 0);
  const est = Math.round(Math.pow(10, logEst * grow));
  const actual = Math.round(Math.pow(10, Math.log10(cfg.actual) * grow));
  const ratio = Math.max(est / Math.max(actual, 1), actual / Math.max(est, 1));
  const plan = fixed && p > 0.8 ? cfg.planFix : cfg.planBad;
  const settled = p > 0.85;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <p className="text-muted">Filas estimadas contra filas reales (escala logarítmica)</p>
        {fixed && p > 0.35 && <code className="pop rounded bg-ground px-2 py-1 font-mono text-[var(--pill-pos-ink)]">ANALYZE pedidos;</code>}
      </div>

      <div className="mt-5 space-y-5">
        {[
          { label: "Estimadas por el optimizador", value: est, color: fixed && p > 0.8 ? "var(--positive)" : "var(--warning)" },
          { label: "Reales por ejecución", value: actual, color: "var(--accent)" },
        ].map((row) => (
          <div key={row.label}>
            <div className="flex items-baseline justify-between text-[13px]">
              <span>{row.label}</span>
              <span className="text-[18px] font-semibold">{INT.format(row.value)}</span>
            </div>
            <div className="mt-2 h-3 rounded-full bg-raised">
              <div className="h-full rounded-full" style={{ width: logWidth(row.value), background: row.color }} />
            </div>
          </div>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3">
        <Stat label="Diferencia" value={`${(ratio < 10 ? DEC : INT).format(ratio)} ×`} tone={settled ? (ratio >= 10 ? "neg" : "pos") : undefined} />
        <Stat label="Plan elegido" value={plan} tone={settled ? (fixed ? "pos" : "neg") : undefined} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- derrame a disco (AP-07)

function SpillViz({ cfg, mode }: { cfg: Extract<VizConfig, { kind: "spill" }>; mode: Mode }) {
  const fixed = mode === "fix";
  const mem = fixed ? cfg.memFix : cfg.memBad;
  const p = useProgress(0, 3200, 250);
  const ratio = cfg.dataMb / mem;
  const fill = Math.min(1, ratio * easeOut(Math.min(1, p / 0.7)));
  const overflow = Math.max(0, Math.ceil(cfg.dataMb - mem));
  const dropped = Math.floor(overflow * seg(p, 0.4, 0.95));
  const temp = Math.round(cfg.tempBlocks * (overflow ? seg(p, 0.4, 0.95) : 0));
  const time = fixed ? 0.31 : 2.4;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <p className="text-muted">Ordenar {cfg.dataMb} MB de filas</p>
        <span className={fixed ? "pill-pos" : "pill-neg"}>{fixed ? "Cabe en memoria" : "Se desborda a disco"}</span>
      </div>

      <div className="mt-5">
        <div className="flex items-baseline justify-between text-[13px]">
          <span>Memoria de trabajo</span>
          <span className="text-muted">{mem} MB</span>
        </div>
        <div className="relative mt-2 h-10 overflow-hidden rounded border border-control bg-ground">
          <div className="h-full" style={{ width: `${fill * 100}%`, background: fill >= 1 ? "var(--negative)" : "var(--accent)", opacity: 0.85 }} />
        </div>
      </div>

      <div className="mt-5">
        <div className="flex items-baseline justify-between text-[13px]">
          <span>Disco: archivos temporales</span>
          <span className="text-muted">{dropped} MB</span>
        </div>
        <div className="mt-2 flex min-h-[40px] flex-wrap gap-1 rounded border border-dashed border-control p-2">
          {Array.from({ length: dropped }, (_, i) => (
            <span key={i} className="pop block h-5 w-5 rounded-[3px]" style={{ background: READ }} />
          ))}
          {dropped === 0 && <span className="text-[13px] text-muted">{overflow === 0 ? "Sin escrituras temporales" : "Vacío por ahora"}</span>}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3">
        <Stat label="Bloques temporales escritos" value={INT.format(temp)} tone={fixed ? "pos" : "neg"} />
        <Stat label="Tiempo de la consulta" value={`${DEC.format(time * seg(p, 0, 0.9))} s`} tone={fixed ? "pos" : "neg"} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- consultas por fila (AP-06)

function NPlusOneViz({ cfg, mode }: { cfg: Extract<VizConfig, { kind: "nplus1" }>; mode: Mode }) {
  const fixed = mode === "fix";
  const p = useProgress(0, fixed ? 1600 : 3600, 250);
  // Problemática: una consulta hija por fila, una tras otra. Corregida: una sola trae todas.
  const done = fixed ? (seg(p, 0.2, 0.5) > 0 ? cfg.rows : 0) : Math.floor(seg(p, 0.1, 0.95) * cfg.rows);
  const queries = fixed ? (p > 0.2 ? 1 : 0) : (p > 0.05 ? 1 : 0) + done;
  const settled = p > 0.95;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <p className="text-muted">
          <code className="font-mono text-fg">{cfg.parent}</code> devuelve {cfg.rows} filas; cada una necesita sus{" "}
          <code className="font-mono text-fg">{cfg.child}</code>
        </p>
        <span className={fixed ? "pill-pos" : "pill-neg"}>{fixed ? "Una sola consulta con JOIN" : "Una consulta por cada fila"}</span>
      </div>

      <div
        role="img"
        aria-label={fixed ? "Una consulta trae los datos de todas las filas" : "Cada fila lanza su propia consulta"}
        className="mt-5 grid grid-cols-6 gap-2 sm:grid-cols-8 md:grid-cols-12"
      >
        {Array.from({ length: cfg.rows }, (_, i) => {
          const lit = i < done;
          return (
            <span
              key={i}
              className="flex aspect-square items-center justify-center rounded text-[11px] font-mono transition-[background-color,color,transform] duration-200 ease-out"
              style={{
                background: lit ? (fixed ? "var(--positive)" : READ) : "var(--raised)",
                color: lit ? "var(--ground)" : "var(--muted)",
                transform: lit && !fixed && i === done - 1 ? "scale(1.18)" : "scale(1)",
              }}
            >
              {i + 1}
            </span>
          );
        })}
      </div>

      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-muted">
        <li className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-[2px]" style={{ background: fixed ? "var(--positive)" : READ }} />
          {fixed ? "Fila resuelta por el JOIN" : "Fila con su consulta ya lanzada"}
        </li>
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px] bg-raised" />Fila pendiente</li>
      </ul>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <Stat label="Consultas lanzadas" value={INT.format(queries)} tone={settled ? (fixed ? "pos" : "neg") : undefined} />
        <Stat label="Filas del padre" value={cfg.rows} />
        <Stat label="Consultas hija" value={fixed ? (p > 0.2 ? "0" : "—") : INT.format(done)} tone={settled ? (fixed ? "pos" : "neg") : undefined} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- selector

export default function AntipatternViz({ doc }: { doc: AntipatternDoc }) {
  const cfg = doc.viz;
  return (
    <VizFrame>
      {(mode) => {
        switch (cfg.kind) {
          case "scan":
            return <ScanViz cfg={cfg} mode={mode} />;
          case "writes":
            return <WritesViz cfg={cfg} mode={mode} />;
          case "baseline":
            return <BaselineViz cfg={cfg} mode={mode} />;
          case "estimate":
            return <EstimateViz cfg={cfg} mode={mode} />;
          case "spill":
            return <SpillViz cfg={cfg} mode={mode} />;
          case "nplus1":
            return <NPlusOneViz cfg={cfg} mode={mode} />;
        }
      }}
    </VizFrame>
  );
}
