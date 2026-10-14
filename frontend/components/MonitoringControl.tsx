"use client";

import { useState } from "react";

// Pausar / reanudar el monitoreo de la base de datos de la sesión. Pausar pide
// confirmación porque detiene la recolección de nuevos diagnósticos.
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

  return (
    <section className="rounded border border-graphite-200 bg-surface px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg text-ink">Monitoreo</h2>
          <p className="mt-0.5 text-sm text-graphite-600">
            {enabled
              ? "QueryLens está monitoreando esta base de datos."
              : "El monitoreo está pausado: no se recolectan nuevos diagnósticos."}
          </p>
        </div>

        {!enabled && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onChange(true)}
            className="rounded bg-ink px-4 py-2 text-sm text-paper transition hover:bg-graphite-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Reanudando…" : "Reanudar monitoreo"}
          </button>
        )}

        {enabled && !confirming && (
          <button
            type="button"
            disabled={busy}
            onClick={() => setConfirming(true)}
            className="rounded border border-alert px-4 py-2 text-sm text-alert transition hover:bg-alert-soft disabled:cursor-not-allowed disabled:opacity-50"
          >
            Pausar monitoreo
          </button>
        )}
      </div>

      {enabled && confirming && (
        <div className="mt-3 rounded border-l-2 border-warn bg-warn-soft px-3 py-2.5">
          <p className="text-sm text-ink">
            Se dejará de recolectar información de esta base de datos. Los diagnósticos actuales se
            conservan y puedes reanudar cuando quieras.
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onChange(false);
              }}
              className="rounded bg-alert px-3 py-1.5 text-sm text-paper transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Pausando…" : "Sí, pausar"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
              className="rounded border border-graphite-300 px-3 py-1.5 text-sm text-ink transition hover:bg-paper disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancelar
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-3 rounded border-l-2 border-alert bg-alert-soft px-3 py-2 text-sm text-ink">
          {error}
        </p>
      )}
    </section>
  );
}
