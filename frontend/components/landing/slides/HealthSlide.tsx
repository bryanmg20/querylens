"use client";

import { useState } from "react";
import { IconPause, IconPlay } from "../../icons";
import type { SlideProps } from "../HomeCarousel";

const SECTIONS = [
  { name: "Estadísticas de consultas", ok: false },
  { name: "Planes de ejecución", ok: true },
  { name: "Tablas e índices", ok: true },
  { name: "Bloqueos", ok: true },
];

// La otra mitad del producto: cuando la extracción falla, el problema llega con su causa y su
// remedio. El botón de pausa del ejemplo funciona de verdad.
export default function HealthSlide(_: SlideProps) {
  const [on, setOn] = useState(true);

  return (
    <div className="grid gap-10 lg:grid-cols-12 lg:items-center lg:gap-10">
      <div className="lg:col-span-6">
        <h2 className="text-[clamp(2rem,min(4.4vw,7.5vh),3.75rem)] font-semibold leading-[1.04] tracking-[-0.03em]">
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 0 }}>
              Si algo falla,
            </span>
          </span>
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 1 }}>
              te dice qué hacer
            </span>
          </span>
        </h2>
        <p className="sl mt-7 max-w-[30rem] text-[18px] leading-[1.55] text-muted" style={{ ["--i" as string]: 3 }}>
          Cuando una sección no se puede leer, QueryLens abre un problema con el código, el mensaje y los pasos para
          resolverlo. Y puedes pausar el monitoreo cuando quieras, sin perder los diagnósticos.
        </p>
      </div>

      <div className="sl lg:col-span-6" style={{ ["--i" as string]: 3 }}>
        <div data-ui className="rounded-lg border border-line bg-surface p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="flex items-center gap-2.5 text-[16px]">
              <span className="relative flex h-2 w-2">
                <span
                  className={`relative h-2 w-2 rounded-full transition-colors duration-200 ${
                    on ? "live-dot bg-positive" : "border border-muted"
                  }`}
                />
              </span>
              <span className={on ? "text-fg" : "text-muted"}>{on ? "Monitoreo activo" : "Monitoreo en pausa"}</span>
            </p>
            <button type="button" onClick={() => setOn((v) => !v)} className={on ? "btn-outline" : "btn-primary"}>
              {on ? <IconPause size={14} /> : <IconPlay size={14} />}
              {on ? "Pausar monitoreo" : "Reanudar monitoreo"}
            </button>
          </div>

          <ul className="mt-6 grid grid-cols-2 gap-x-3 gap-y-4 sm:grid-cols-4">
            {SECTIONS.map((section, i) => (
              <li key={section.name}>
                <div
                  className="h-1 rounded-full transition-opacity duration-300"
                  style={{
                    background: section.ok ? "var(--accent)" : "var(--negative)",
                    opacity: on ? 1 : 0.35,
                    transitionDelay: `${i * 40}ms`,
                  }}
                />
                <p className={`mt-2 text-[13px] ${section.ok ? "text-muted" : "text-[var(--pill-neg-ink)]"}`}>
                  {section.name}
                </p>
              </li>
            ))}
          </ul>

          <div className="mt-6 rounded bg-ground p-5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="pill-warn">Degradado</span>
              <code className="font-mono text-[13px]">pg_stat_statements_missing</code>
            </div>
            <p className="mt-3 text-[16px] leading-[1.5]">
              La extensión pg_stat_statements no está disponible: no se pueden leer las estadísticas de consultas.
            </p>
            <p className="mb-2 mt-4 text-[13px] text-muted">Cómo resolverlo</p>
            <pre
              data-no-swipe
              className="overflow-x-auto rounded bg-surface px-4 py-3.5 font-mono text-[13px] leading-5"
            >
              {"CREATE EXTENSION IF NOT EXISTS\n  pg_stat_statements;"}
            </pre>
          </div>
          <p className="mt-4 text-[13px] text-muted">Ejemplo ilustrativo.</p>
        </div>
      </div>
    </div>
  );
}
