import type { Metadata } from "next";
import PrepareGuide from "@/components/PrepareGuide";
import { isGuideEngine } from "@/lib/prepararBase";

export const metadata: Metadata = { title: "Preparar la base - QueryLens" };

// ?motor=postgresql|mysql elige el motor; ?base=nombre rellena el nombre en los comandos.
// El formulario de registro y el panel de salud enlazan aquí.
export default function PrepareBasePage({ searchParams }: { searchParams: { motor?: string; base?: string } }) {
  const engine = isGuideEngine(searchParams.motor) ? searchParams.motor : "postgresql";
  return (
    <main className="mx-auto w-full max-w-[1320px] px-4 pb-24 pt-8 md:px-8 md:pb-12">
      <header>
        <p className="text-[13px] text-muted">
          <span className="text-fg">QueryLens</span>
          <span aria-hidden="true"> / </span>
          Preparar la base
        </p>
        <h1 className="mt-2 text-[26px] font-semibold leading-8 tracking-[-0.01em]">Preparar la base para QueryLens</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Un script por motor activa lo que QueryLens necesita (auto_explain en PostgreSQL, el esquema de rendimiento en
          MySQL) y crea el usuario de solo lectura <code className="font-mono">querylens_monitor</code>. Lo corre una
          vez el administrador de la base, sin salir de esta guía.
        </p>
      </header>
      <div className="mt-8">
        <PrepareGuide initialEngine={engine} initialBase={searchParams.base?.slice(0, 128) ?? ""} />
      </div>
    </main>
  );
}
