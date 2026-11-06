"use client";

import { useState } from "react";
import { IconPause, IconPlay } from "./icons";

// Pausar / reanudar el monitoreo de la base de datos de la sesión. Pausar pide confirmación
// porque detiene la recolección de nuevos diagnósticos; la confirmación se despliega en la
// misma tarjeta.
export default function MonitoringControl({
  enabled,
  busy,
  error,
  onChange,
}: {
  enabled: boolean;
  busy: boolean;
  error: string | null;
  onChange: (enabled: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const asking = enabled && confirming;

  return (
    <section className="card p-5" aria-labelledby="monitoring-title">
      <h2 id="monitoring-title" className="card-title">
        Control de monitoreo
      </h2>

      <p className="mt-3 flex items-start gap-2.5 text-[13px]">
        <span className="relative mt-1.5 flex h-2 w-2 flex-none">
          <span className={`relative h-2 w-2 rounded-full ${enabled ? "live-dot bg-positive" : "border border-muted"}`} />
        </span>
        <span>
          <span className={`block font-medium ${enabled ? "" : "text-muted"}`}>
            {enabled ? "Monitoreo activo" : "Monitoreo en pausa"}
          </span>
          <span className="mt-0.5 block text-muted">
            {enabled
              ? "QueryLens está recolectando información de esta base de datos."
              : "No se recolectan nuevos diagnósticos. Los actuales se conservan."}
          </span>
        </span>
      </p>

      {!enabled && (
        <button type="button" disabled={busy} onClick={() => onChange(true)} className="btn-primary mt-4 w-full">
          <IconPlay size={14} />
          {busy ? "Reanudando…" : "Reanudar monitoreo"}
        </button>
      )}

      {enabled && (
        <button
          type="button"
          disabled={busy}
          aria-expanded={asking}
          onClick={() => setConfirming((v) => !v)}
          className="btn-outline mt-4 w-full"
        >
          <IconPause size={14} />
          Pausar monitoreo
        </button>
      )}

      <div className="reveal" data-open={asking}>
        <div className="overflow-hidden">
          <div className="mt-3 rounded bg-raised p-3">
            <p className="text-[13px] leading-5">
              Se dejará de recolectar información de esta base de datos. Los diagnósticos actuales se conservan y
              puedes reanudar cuando quieras.
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  onChange(false);
                }}
                className="btn-danger"
              >
                {busy ? "Pausando…" : "Sí, pausar"}
              </button>
              <button type="button" disabled={busy} onClick={() => setConfirming(false)} className="btn-outline">
                Cancelar
              </button>
            </div>
          </div>
        </div>
      </div>

      {error && <p className="notice-neg mt-3 text-[13px]">{error}</p>}
    </section>
  );
}
