"use client";

import Link from "next/link";
import { useState } from "react";
import { ANTIPATTERN_ORDER, getDoc, type AntipatternDoc } from "@/lib/antipatterns";
import Appear from "../Appear";
import { IconCheck } from "../icons";
import AntipatternViz, { ModeSwitch, type Mode } from "./Viz";

const DEC = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 1 });

// La explicación completa de un antipatrón: qué es (con su animación), por qué sucede, cómo
// evitarlo y cuándo no es un problema. Solo explica el patrón: los hallazgos de la base viven en
// Diagnósticos y no se repiten aquí.
export default function AntipatternDetail({ id }: { id: string }) {
  const doc = getDoc(id);

  if (!doc) {
    return (
      <Shell title="Antipatrón desconocido">
        <p className="empty-frame mt-6">QueryLens no tiene una explicación para «{id}».</p>
        <Link href="/diagnostics" className="btn-outline mt-4 inline-flex">
          Volver a Diagnósticos
        </Link>
      </Shell>
    );
  }

  const position = ANTIPATTERN_ORDER.indexOf(doc.id as (typeof ANTIPATTERN_ORDER)[number]);
  const prev = ANTIPATTERN_ORDER[(position + ANTIPATTERN_ORDER.length - 1) % ANTIPATTERN_ORDER.length];
  const next = ANTIPATTERN_ORDER[(position + 1) % ANTIPATTERN_ORDER.length];

  return (
    <Shell title={doc.title}>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="Antipatrones" className="flex flex-wrap gap-2">
          {ANTIPATTERN_ORDER.map((other) => {
            const o = getDoc(other)!;
            const selected = other === doc.id;
            return (
              <Link
                key={other}
                href={`/diagnostics/${other}`}
                aria-current={selected ? "page" : undefined}
                className={`inline-flex h-8 items-center gap-1.5 rounded-full px-3.5 text-[13px] transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
                  selected ? "bg-raised text-fg" : "text-muted [@media(hover:hover)]:hover:bg-surface [@media(hover:hover)]:hover:text-fg"
                }`}
              >
                <span className="text-muted">{o.code}</span>
                {o.title.toLowerCase()}
              </Link>
            );
          })}
        </nav>
        <div className="flex items-center gap-2 text-[13px] text-muted">
          <span>
            {position + 1} / {ANTIPATTERN_ORDER.length}
          </span>
          <Link href={`/diagnostics/${prev}`} aria-label="Antipatrón anterior" className="btn-outline h-8 w-8 px-0">
            ←
          </Link>
          <Link href={`/diagnostics/${next}`} aria-label="Antipatrón siguiente" className="btn-outline h-8 w-8 px-0">
            →
          </Link>
        </div>
      </div>

      <div key={doc.id}>
        <header className="rise mt-10">
          <p className="flex items-center gap-2.5 text-[13px] text-muted">
            <span className="pill-neu">{doc.code}</span>
            {doc.family}
          </p>
          <h1 className="mt-3 text-[24px] font-semibold leading-8 tracking-[-0.02em] md:text-[32px] md:leading-10">{doc.title}</h1>
          <p className="mt-2 text-[18px] leading-[1.5] text-muted">{doc.tagline}</p>
        </header>

        <Section title="¿Qué es?">
          <div className="grid gap-8 lg:grid-cols-12 lg:gap-10">
            <div className="lg:col-span-5">
              {doc.what.map((paragraph) => (
                <p key={paragraph} className="mb-4 max-w-[34rem] text-[16px] leading-[1.6] last:mb-0">
                  {paragraph}
                </p>
              ))}
              <p className="mt-6 max-w-[34rem] rounded bg-surface p-4 text-[13px] leading-5 text-muted">
                <span className="font-semibold text-fg">Cómo lo detecta QueryLens: </span>
                {doc.rule}
              </p>
            </div>
            <div className="min-w-0 lg:col-span-7">
              <AntipatternViz doc={doc} />
              <p className="mt-3 text-[13px] text-muted">Animación ilustrativa: tablas y cifras son un ejemplo, no tu base de datos.</p>
            </div>
          </div>
        </Section>

        <Section title="¿Por qué sucede generalmente?">
          <ul className="grid overflow-hidden rounded-lg border border-line bg-surface sm:grid-cols-2 lg:grid-cols-4">
            {doc.causes.map((cause, i) => (
              <li key={cause.title} className="border-line p-5 [&:not(:first-child)]:border-t sm:[&:nth-child(2)]:border-t-0 sm:[&:nth-child(even)]:border-l lg:[&:not(:first-child)]:border-t-0 lg:[&:not(:first-child)]:border-l">
                <Appear delay={i * 70}>
                  <h3 className="text-[16px] font-semibold leading-[1.3]">{cause.title}</h3>
                  <p className="mt-2 text-[13px] leading-5 text-muted">{cause.text}</p>
                </Appear>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="¿Cómo evitarlo?">
          <HowToAvoid doc={doc} />
        </Section>

        <Section title="Ojo: no siempre es un antipatrón">
          <p className="notice-warn max-w-3xl text-[14px] leading-6">{doc.caveat}</p>
        </Section>
      </div>
    </Shell>
  );
}

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-[1320px] px-4 pb-24 pt-8 md:px-8 md:pb-16">
      <p className="text-[13px] text-muted">
        <span className="text-fg">QueryLens</span>
        <span aria-hidden="true"> / </span>
        <Link href="/diagnostics" className="transition-colors duration-150 hover:text-fg">
          Diagnósticos
        </Link>
        <span aria-hidden="true"> / </span>
        {title}
      </p>
      {children}
    </main>
  );
}

function Section({ title, aside, children }: { title: string; aside?: string; children: React.ReactNode }) {
  return (
    <section className="mt-14" aria-label={title}>
      <Appear>
        <div className="mb-5 flex items-baseline justify-between gap-3">
          <h2 className="text-[20px] font-semibold tracking-[-0.01em]">{title}</h2>
          {aside && <span className="text-[13px] text-muted">{aside}</span>}
        </div>
      </Appear>
      <Appear delay={60}>{children}</Appear>
    </section>
  );
}

// El antes y el después: la consulta cambia con un fundido corto y las barras de tiempo se
// vuelven a dibujar; al lado, lo que hay que hacer.
function HowToAvoid({ doc }: { doc: AntipatternDoc }) {
  const [mode, setMode] = useState<Mode>("bad");
  const fixed = mode === "fix";
  const { compare } = doc;
  const max = Math.max(compare.bad, compare.fix);
  const faster = compare.bad / compare.fix;

  return (
    <div className="grid items-start gap-6 lg:grid-cols-12">
      <div className="min-w-0 rounded-lg border border-line bg-surface p-5 lg:col-span-7">
        <ModeSwitch mode={mode} onChange={setMode} />

        <div key={mode} className="swap-in mt-5">
          <p className="mb-2 text-[13px] text-muted">{fixed ? "Consulta corregida" : "Consulta problemática"}</p>
          <pre
            className="min-h-[88px] overflow-x-auto rounded bg-ground px-4 py-3.5 font-mono text-[13px] leading-5"
            style={{ color: fixed ? "var(--pill-pos-ink)" : "var(--pill-neg-ink)" }}
          >
            {fixed ? doc.fix : doc.bad}
          </pre>
        </div>

        <div className="mt-6">
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <p className="font-medium">{compare.metric}</p>
            <p className="text-muted">valores de ejemplo</p>
          </div>
          <div key={mode} className="mt-3 space-y-3">
            {[
              { label: "Problemática", value: compare.bad, color: "var(--negative)", active: !fixed },
              { label: "Corregida", value: compare.fix, color: "var(--positive)", active: fixed },
            ].map((row, i) => (
              <div key={row.label} className="transition-opacity duration-200" style={{ opacity: row.active ? 1 : 0.45 }}>
                <div className="flex items-baseline justify-between text-[13px]">
                  <span>{row.label}</span>
                  <span className="font-semibold">
                    {DEC.format(row.value)} {compare.unit}
                  </span>
                </div>
                <div className="mt-1.5 h-2.5 rounded-full bg-raised">
                  <div
                    className="grow-x h-full rounded-full"
                    style={{ width: `${Math.max(1.5, (row.value / max) * 100)}%`, background: row.color, ["--i" as string]: i }}
                  />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[13px] text-muted">
            {faster >= 1
              ? `La versión corregida es ${DEC.format(faster)} veces más rápida en este ejemplo.`
              : `La versión corregida baja el tiempo a ${DEC.format(1 / faster)} veces.`}
          </p>
        </div>
      </div>

      <div className="min-w-0 rounded-lg border border-line bg-surface p-5 lg:col-span-5">
        <h3 className="text-[13px] font-semibold">En grandes rasgos</h3>
        <ul className="mt-4 space-y-3.5">
          {doc.checklist.map((item, i) => (
            <li key={item}>
              <Appear delay={i * 80} className="flex gap-3 text-[14px] leading-5">
                <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-[var(--pill-pos-bg)] text-[var(--pill-pos-ink)]">
                  <IconCheck size={12} />
                </span>
                <span>{item}</span>
              </Appear>
            </li>
          ))}
        </ul>
        <div className="mt-6 rounded bg-ground p-4">
          <h4 className="text-[13px] font-semibold">Antes de cambiar nada</h4>
          <p className="mt-1.5 text-[13px] leading-5 text-muted">
            Mide el estado actual con EXPLAIN (ANALYZE, BUFFERS) y prueba el cambio primero en un entorno que no sea
            producción. Después vuelve a medir y compara el tiempo y las filas leídas.
          </p>
        </div>
      </div>
    </div>
  );
}
