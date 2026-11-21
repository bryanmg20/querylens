"use client";

import { useEffect, useRef, useState } from "react";

// Código con un botón para copiarlo. El aviso "Copiado" se anuncia a lectores de pantalla y vuelve
// solo; si el navegador no deja usar el portapapeles, el botón avisa en vez de fallar callado.
export default function CodeBlock({ code, label = "Copiar" }: { code: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1800);
  }

  return (
    <div className="relative mt-2 rounded bg-ground">
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words px-3 py-2.5 pr-24 font-mono text-[13px] leading-5">
        {code}
      </pre>
      <button
        type="button"
        onClick={copy}
        className="absolute right-2 top-2 rounded border border-control px-2 py-0.5 text-[13px] text-muted transition-colors duration-150 hover:border-[var(--control-hover)] hover:text-fg active:scale-[0.97]"
      >
        {state === "copied" ? "Copiado" : state === "failed" ? "No se pudo" : label}
      </button>
      <span className="sr-only" role="status">
        {state === "copied" ? "Copiado al portapapeles" : ""}
      </span>
    </div>
  );
}
