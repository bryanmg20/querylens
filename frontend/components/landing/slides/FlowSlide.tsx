"use client";

import { useEffect, useState } from "react";
import { STAGES } from "@/lib/landing-data";
import type { SlideProps } from "../HomeCarousel";

const STEP_MS = 2600;

// El recorrido de los datos: una pista que se llena etapa a etapa mientras la diapositiva
// está a la vista. Pasar el cursor o enfocar una etapa la fija.
export default function FlowSlide({ active }: SlideProps) {
  const [stage, setStage] = useState(0);
  const [pinned, setPinned] = useState(false);

  useEffect(() => {
    if (active) setStage(0);
  }, [active]);

  useEffect(() => {
    if (!active || pinned) return;
    const timer = setInterval(() => setStage((n) => (n + 1) % STAGES.length), STEP_MS);
    return () => clearInterval(timer);
  }, [active, pinned]);

  return (
    <>
      <div className="grid gap-6 md:grid-cols-12 md:items-end">
        <h2
          className="text-[clamp(2rem,min(4.4vw,7.5vh),3.75rem)] font-semibold leading-[1.04] tracking-[-0.03em] md:col-span-7"
        >
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 0 }}>
              Sin instalar nada
            </span>
          </span>
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 1 }}>
              en tu servidor
            </span>
          </span>
        </h2>
        <p className="sl max-w-[30rem] text-[18px] leading-[1.55] text-muted md:col-span-5" style={{ ["--i" as string]: 3 }}>
          QueryLens observa desde fuera. Lee lo que tu motor ya sabe de sus consultas y lo convierte en diagnósticos que
          cualquier desarrollador puede leer.
        </p>
      </div>

      <div className="sl mt-12 md:mt-16" style={{ ["--i" as string]: 4 }}>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised" aria-hidden="true">
          <div
            className="h-full origin-left rounded-full bg-accent transition-transform duration-[800ms] ease-[var(--ease-out)]"
            style={{ transform: `scaleX(${(stage + 1) / STAGES.length})` }}
          />
        </div>

        <ol className="mt-8 grid gap-8 md:grid-cols-2 lg:grid-cols-4 lg:gap-8">
          {STAGES.map((item, i) => {
            const on = i <= stage;
            return (
              <li
                key={item.title}
                tabIndex={0}
                onPointerEnter={() => {
                  setPinned(true);
                  setStage(i);
                }}
                onPointerLeave={() => setPinned(false)}
                onFocus={() => setStage(i)}
                className="rounded outline-offset-8"
              >
                <h3
                  className={`text-2xl font-semibold tracking-[-0.02em] transition-colors duration-300 ${
                    on ? "text-fg" : "text-muted"
                  }`}
                >
                  {item.title}
                </h3>
                <p className="mt-3 text-[16px] leading-[1.55] text-muted">{item.text}</p>
                <pre
                  data-ui
                  data-no-swipe
                  className={`mt-5 overflow-x-auto whitespace-pre rounded bg-ground px-4 py-3.5 font-mono text-[13px] leading-5 transition-[color,transform] duration-300 ease-[var(--ease-out)] ${
                    i === stage ? "-translate-y-1 text-fg" : "text-muted"
                  }`}
                >
                  {item.sample}
                </pre>
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
}
