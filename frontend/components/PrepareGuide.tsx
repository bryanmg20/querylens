"use client";

import { Fragment, useState } from "react";
import {
  GUIDE_ENGINES,
  SCRIPTS_PATH,
  guideSections,
  type Block,
  type GuideEngine,
} from "@/lib/prepararBase";
import CodeBlock from "./CodeBlock";

// La guía de preparar la base (PREPARAR_BASE.md) dentro de la interfaz: un selector de motor, el
// nombre de la base que se pone en los comandos, y las secciones del motor elegido. Cada sección
// tiene su ancla (#instalar, #planes-explain…) para enlazarla desde el formulario y la salud.
export default function PrepareGuide({
  initialEngine,
  initialBase,
}: {
  initialEngine: GuideEngine;
  initialBase: string;
}) {
  const [engine, setEngine] = useState<GuideEngine>(initialEngine);
  const [base, setBase] = useState(initialBase);
  const sections = guideSections(engine);

  function pick(next: GuideEngine) {
    setEngine(next);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("motor", next);
      window.history.replaceState(null, "", url);
    } catch {
      // sin historial no pasa nada: el selector sigue funcionando
    }
  }

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="space-y-5 lg:sticky lg:top-6">
        <div role="tablist" aria-label="Motor de la base" className="flex gap-1 rounded-full bg-raised p-1">
          {GUIDE_ENGINES.map((e) => (
            <button
              key={e.id}
              type="button"
              role="tab"
              id={`tab-${e.id}`}
              aria-selected={engine === e.id}
              aria-controls="guide-panel"
              onClick={() => pick(e.id)}
              className={`h-8 flex-1 rounded-full px-3 text-[13px] font-medium transition-colors duration-150 ${
                engine === e.id ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg"
              }`}
            >
              {e.label}
            </button>
          ))}
        </div>

        <label className="block text-[13px]">
          <span className="text-muted">Nombre de tu base</span>
          <input
            value={base}
            onChange={(e) => setBase(e.target.value)}
            placeholder="<base>"
            spellCheck={false}
            autoComplete="off"
            className="input mt-1 font-mono"
          />
          <span className="mt-1 block text-muted">Se pone en los comandos de abajo.</span>
        </label>

        <nav aria-label="Secciones de la guía" className="hidden text-[13px] lg:block">
          <ul className="space-y-1.5 border-l border-rule">
            {sections.map((s) => (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  className="-ml-px block border-l border-transparent pl-3 text-muted transition-colors duration-150 hover:border-accent hover:text-fg"
                >
                  {s.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div
        id="guide-panel"
        role="tabpanel"
        aria-labelledby={`tab-${engine}`}
        key={engine}
        className="swap-in min-w-0 space-y-6"
      >
        {sections.map((s) => (
          <section key={s.id} id={s.id} aria-labelledby={`h-${s.id}`} className="card scroll-mt-6 p-6">
            <h2 id={`h-${s.id}`} className="card-title">
              {s.title}
            </h2>
            <div className="mt-3 space-y-3 text-[14px] leading-6">
              <Blocks blocks={s.blocks} base={base} />
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

// `texto` entre acentos graves → <code>.
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split("`").map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className="rounded bg-ground px-1 py-0.5 font-mono text-[13px]">
            {part}
          </code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

function Blocks({ blocks, base }: { blocks: Block[]; base: string }) {
  const name = base.trim();
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.t) {
          case "p":
            return (
              <p key={i} className="max-w-[60rem]">
                <Inline text={b.text} />
              </p>
            );
          case "code":
            // El nombre de la base reemplaza <base>; sin nombre, queda el marcador.
            return <CodeBlock key={i} code={name ? b.code.replaceAll("<base>", name) : b.code} />;
          case "list": {
            const List = b.ordered ? "ol" : "ul";
            return (
              <List key={i} className={`max-w-[60rem] space-y-1.5 pl-5 ${b.ordered ? "list-decimal" : "list-disc"}`}>
                {b.items.map((item) => (
                  <li key={item}>
                    <Inline text={item} />
                  </li>
                ))}
              </List>
            );
          }
          case "table":
            return (
              <div key={i} className="overflow-x-auto rounded border border-rule">
                <table className="w-full text-left text-[13px]">
                  <thead className="bg-raised text-muted">
                    <tr>
                      {b.head.map((h, j) => (
                        <th key={j} className="px-3 py-2 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((row, r) => (
                      <tr key={r} className="border-t border-rule align-top">
                        {row.map((cell, c) => (
                          <td key={c} className={`px-3 py-2 ${c === 0 ? "font-medium" : ""}`}>
                            <Inline text={cell} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "note":
            return (
              <p
                key={i}
                className={`${b.tone === "warn" ? "notice-warn" : "rounded bg-raised px-3 py-2.5"} max-w-[60rem] leading-5`}
              >
                <Inline text={b.text} />
              </p>
            );
          case "download":
            return (
              <p key={i} className="flex flex-wrap gap-2">
                {b.files.map((f) => (
                  <a key={f.file} href={`${SCRIPTS_PATH}/${f.file}`} download className="btn-outline">
                    {f.label}
                  </a>
                ))}
              </p>
            );
          case "details":
            return (
              <details key={i} className="rounded border border-rule px-4 py-3">
                <summary className="cursor-pointer text-[14px] font-medium">{b.summary}</summary>
                <div className="mt-3 space-y-3">
                  <Blocks blocks={b.blocks} base={base} />
                </div>
              </details>
            );
        }
      })}
    </>
  );
}
