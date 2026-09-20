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

export default function SignalTrace({ state }: { state: TraceState }) {
  const lineClass =
    state.status === "success"
      ? "bg-signal"
      : state.status === "testing"
        ? "bg-signal/50"
        : "bg-graphite-300";

  const dotClass =
    state.status === "success"
      ? "left-[calc(100%-7px)] bg-signal"
      : state.status === "testing"
        ? "left-0 bg-signal trace-dot"
        : "left-0 bg-graphite-300";

  const message =
    state.status === "success" || state.status === "error" ? state.message : STATUS_TEXT[state.status];

  return (
    <div className="rounded border border-graphite-200 bg-surface px-4 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm text-graphite-600">Señal</span>
        {state.status === "success" && state.latencyMs != null && (
          <span className="font-mono text-xs text-signal">{state.latencyMs} ms</span>
        )}
      </div>

      {state.status === "error" ? (
        <div className="flex h-6 items-center gap-2">
          <span className="h-px flex-1 bg-alert/40" />
          <span className="font-mono text-xs text-alert">×</span>
          <span className="h-px flex-1 bg-alert/40" />
        </div>
      ) : (
        <div className="relative h-6">
          <span className={`absolute left-0 top-1/2 h-px w-full -translate-y-1/2 ${lineClass}`} />
          <span
            className={`absolute top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full transition-[left] duration-500 ${dotClass}`}
          />
        </div>
      )}

      <p
        className={`mt-2 min-h-[1rem] text-xs ${
          state.status === "error" ? "text-alert" : "text-graphite-600"
        }`}
      >
        {message}
      </p>
    </div>
  );
}
