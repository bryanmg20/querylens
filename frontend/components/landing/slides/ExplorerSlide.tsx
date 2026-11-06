"use client";

import { useRef, useState } from "react";
import { ANTIPATTERNS } from "@/lib/landing-data";
import type { SlideProps } from "../HomeCarousel";

// Lo interactivo central: eliges un antipatrón y ves su regla, la consulta problemática, la
// corrección y la evidencia. Con teclado el cambio es instantáneo (acciones repetidas no se
// animan); con puntero, un fundido corto.
export default function ExplorerSlide(_: SlideProps) {
  const [index, setIndex] = useState(0);
  const [pulse, setPulse] = useState(0);
  const byPointer = useRef(true);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = ANTIPATTERNS[index];

  function select(next: number, pointer: boolean) {
    byPointer.current = pointer;
    setIndex(next);
    setPulse((n) => n + 1);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    const last = ANTIPATTERNS.length - 1;
    let next = index;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else return;
    event.preventDefault();
    select(next, false);
    refs.current[next]?.focus();
  }

  return (
    <>
      <h2 className="text-[clamp(2rem,min(4.4vw,7.5vh),3.75rem)] font-semibold leading-[1.04] tracking-[-0.03em]">
        <span className="sl-mask block">
          <span className="sl-line" style={{ ["--i" as string]: 0 }}>
            Siete antipatrones, siete arreglos
          </span>
        </span>
      </h2>

      <div className="sl mt-8 grid gap-6 lg:grid-cols-12" style={{ ["--i" as string]: 2 }}>
        <div
          role="tablist"
          aria-label="Antipatrones"
          aria-orientation="vertical"
          onKeyDown={onKeyDown}
          className="grid grid-cols-2 gap-2 lg:col-span-4 lg:flex lg:flex-col lg:gap-1"
        >
          {ANTIPATTERNS.map((item, i) => {
            const selected = i === index;
            return (
              <button
                key={item.id}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${item.id}`}
                aria-selected={selected}
                aria-controls="antipattern-panel"
                tabIndex={selected ? 0 : -1}
                onClick={() => select(i, true)}
                className={`rounded px-4 py-2.5 text-left transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.98] lg:flex-auto ${
                  selected ? "bg-raised text-fg" : "text-muted [@media(hover:hover)]:hover:bg-raised [@media(hover:hover)]:hover:text-fg"
                }`}
              >
                <span className="block text-[16px] font-medium">{item.title}</span>
                <code className="mt-0.5 hidden font-mono text-[13px] text-muted lg:block">{item.id}</code>
              </button>
            );
          })}
        </div>

        <div
          role="tabpanel"
          id="antipattern-panel"
          aria-labelledby={`tab-${current.id}`}
          className="min-w-0 rounded-lg bg-ground p-6 lg:col-span-8 lg:p-7"
        >
          <div key={pulse} className={byPointer.current ? "swap-in" : undefined}>
            <h3 className="text-2xl font-semibold leading-[1.1] tracking-[-0.02em]">{current.title}</h3>
            <p className="mt-3 max-w-[40rem] text-[18px] leading-[1.5]">{current.summary}</p>

            <p className="mt-4 max-w-[40rem] text-[16px] leading-[1.5] text-muted">
              <span className="text-fg">Cómo se detecta: </span>
              {current.rule}
            </p>

            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              <div className="min-w-0">
                <p className="mb-2 text-[13px] text-muted">Consulta con el problema</p>
                <pre
                  data-ui
                  data-no-swipe
                  className="min-h-[88px] overflow-x-auto rounded bg-surface px-4 py-3.5 font-mono text-[13px] leading-5 text-[var(--pill-neg-ink)]"
                >
                  {current.bad}
                </pre>
              </div>
              <div className="min-w-0">
                <p className="mb-2 text-[13px] text-muted">Recomendación</p>
                <pre
                  data-ui
                  data-no-swipe
                  className="min-h-[88px] overflow-x-auto rounded bg-surface px-4 py-3.5 font-mono text-[13px] leading-5 text-[var(--pill-pos-ink)]"
                >
                  {current.fix}
                </pre>
              </div>
            </div>

            <dl className="mt-6 flex flex-wrap gap-x-10 gap-y-3 text-[13px]">
              {current.evidence.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-muted">{label}</dt>
                  <dd className="mt-0.5 text-[16px] font-medium">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-5 text-[13px] text-muted">Ejemplo ilustrativo: nombres y cifras no son de ninguna base real.</p>
          </div>
        </div>
      </div>
    </>
  );
}
