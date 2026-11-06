import { IconCheck } from "./icons";

export type TraceState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "success"; latencyMs: number | null; message: string }
  | { status: "error"; message: string };

const STATUS_TEXT: Record<TraceState["status"], string> = {
  idle: "Sin probar todavía.",
  testing: "Probando conexión…",
  success: "",
  error: "",
};

// La señal de la prueba de conexión: una línea que se queda quieta, viaja mientras
// se prueba, llega al otro extremo si responde o se corta si falla.
export default function SignalTrace({ state, bare = false }: { state: TraceState; bare?: boolean }) {
  const message =
    state.status === "success" || state.status === "error" ? state.message : STATUS_TEXT[state.status];
  const lineColor =
    state.status === "success" ? "var(--accent)" : state.status === "testing" ? "var(--muted)" : "var(--control)";

  return (
    <section className={bare ? "" : "card p-6"} aria-labelledby="trace-title">
      <div className="flex items-center justify-between gap-3">
        <h2 id="trace-title" className="card-title">
          Señal
        </h2>
        {state.status === "success" && state.latencyMs != null && (
          <span className="pill-pos">{state.latencyMs} ms</span>
        )}
        {state.status === "success" && state.latencyMs == null && (
          <span className="pill-pos">
            <IconCheck size={12} />
            Conectada
          </span>
        )}
        {state.status === "error" && <span className="pill-neg">Sin señal</span>}
      </div>

      <div className="mt-5">
        {state.status === "error" ? (
          <div className="flex h-6 items-center gap-2">
            <span className="h-px flex-1" style={{ background: "var(--negative)" }} />
            <span className="text-[13px] text-negative">×</span>
            <span className="h-px flex-1" style={{ background: "var(--negative)", opacity: 0.35 }} />
          </div>
        ) : (
          <div className="relative h-6">
            <span
              className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 transition-colors duration-500"
              style={{ background: lineColor }}
            />
            <TraceDot status={state.status} />
          </div>
        )}
      </div>

      <p
        aria-live="polite"
        className={`mt-3 min-h-5 text-[13px] ${state.status === "error" ? "text-[var(--pill-neg-ink)]" : "text-muted"}`}
      >
        {message}
      </p>
    </section>
  );
}

function TraceDot({ status }: { status: TraceState["status"] }) {
  // El punto vive en un carril que descuenta su propio ancho para no salirse de la línea.
  return (
    <span className="absolute inset-y-0 left-0 right-2 block">
      <span
        className={`absolute left-0 top-1/2 -mt-1 block h-2 w-2 rounded-full transition-[left,background-color] duration-700 ease-out ${
          status === "testing" ? "trace-dot bg-fg" : status === "success" ? "bg-accent" : "bg-control"
        }`}
        style={status === "success" ? { left: "100%" } : undefined}
      >
        {status === "success" && <span className="ping-once absolute inset-0 rounded-full bg-accent" />}
      </span>
    </span>
  );
}
