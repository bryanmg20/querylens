"use client";

import { useState } from "react";
import { formatDateTime, formatShortDateTime, timeAgo } from "@/lib/format";
import { IconPause, IconPlay } from "./icons";

// Iniciar / pausar / reanudar el monitoreo de la base de datos de la sesión. Registrar una base
// lo deja apagado: `started` distingue una base que nunca se ha extraído (iniciar) de una que ya
// tuvo ciclos (reanudar). Pausar pide confirmación porque detiene la recolección de nuevos
// diagnósticos; la confirmación se despliega en la misma tarjeta.
//
// La sesión de detección (lo que la lista cuenta como reciente) solo cambia cuando el monitoreo
// arranca: iniciar siempre empieza una, y al reanudar se elige seguir la misma o empezar otra. Con
// el monitoreo encendido el detector todavía procesa snapshots de antes del corte.
//
// Reanudar con sesión nueva deja la lista vacía: si la sesión actual tiene hallazgos, antes se
// ofrece guardarlos en PDF (onExportAll imprime todos, sin los filtros de la lista), para que no se
// pierdan de vista por olvido.
export default function MonitoringControl({
  enabled,
  started,
  busy,
  error,
  sessionStartedAt,
  serverTime,
  findingsCount,
  onChange,
  onExportAll,
}: {
  enabled: boolean;
  started: boolean;
  busy: boolean;
  error: string | null;
  sessionStartedAt: string;
  serverTime: string;
  findingsCount: number;
  onChange: (enabled: boolean, newSession: boolean) => void;
  onExportAll: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [confirmingNew, setConfirmingNew] = useState(false);
  // Si ya abrió el diálogo de impresión desde la confirmación de sesión nueva.
  const [exported, setExported] = useState(false);
  // Qué botón de reanudar se pulsó, para decir "Reanudando…" solo en ese.
  const [resuming, setResuming] = useState<"same" | "new" | null>(null);
  const asking = enabled && confirming;
  const askingNew = !enabled && started && confirmingNew;

  const resume = (newSession: boolean) => {
    setConfirmingNew(false);
    setResuming(newSession ? "new" : "same");
    onChange(true, newSession);
  };

  const askNewSession = () => {
    if (findingsCount === 0) {
      resume(true);
      return;
    }
    setExported(false);
    setConfirmingNew((v) => !v);
  };

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
            {enabled ? "Monitoreo activo" : started ? "Monitoreo en pausa" : "Monitoreo sin iniciar"}
          </span>
          <span className="mt-0.5 block text-muted">
            {enabled
              ? "QueryLens está recolectando información de esta base de datos."
              : started
                ? "No se recolectan nuevos diagnósticos. Los actuales se conservan."
                : "QueryLens no recolecta información de esta base de datos hasta que lo inicies."}
          </span>
        </span>
      </p>

      {started && (
        <p
          className="mt-3 text-[13px]"
          title={`Desde el ${formatDateTime(sessionStartedAt)}. La lista cuenta lo detectado desde entonces.`}
        >
          <span className="block text-muted">Sesión de detección</span>
          <span className="block">
            Desde el {formatShortDateTime(sessionStartedAt)}{" "}
            <span className="whitespace-nowrap text-muted">· {timeAgo(sessionStartedAt, serverTime)}</span>
          </span>
        </p>
      )}

      {!enabled && !started && (
        <button type="button" disabled={busy} onClick={() => resume(true)} className="btn-primary mt-4 w-full">
          <IconPlay size={14} />
          {busy ? "Iniciando…" : "Iniciar monitoreo"}
        </button>
      )}

      {!enabled && started && (
        <>
          <div className="mt-4 grid gap-2">
            <button type="button" disabled={busy} onClick={() => resume(false)} className="btn-primary w-full">
              <IconPlay size={14} />
              {busy && resuming === "same" ? "Reanudando…" : "Reanudar (misma sesión)"}
            </button>
            <button
              type="button"
              disabled={busy}
              aria-expanded={findingsCount > 0 ? askingNew : undefined}
              onClick={askNewSession}
              className="btn-outline w-full"
            >
              {busy && resuming === "new" ? "Reanudando…" : "Reanudar con sesión nueva"}
            </button>
          </div>
          <div className="reveal" data-open={askingNew}>
            <div className="overflow-hidden">
              <div className="mt-3 rounded bg-raised p-3">
                <p className="text-[13px] leading-5">
                  {findingsCount === 1 ? "Esta sesión tiene 1 hallazgo." : `Esta sesión tiene ${findingsCount} hallazgos.`}{" "}
                  Con sesión nueva dejarán de verse en esta lista (no se borran: siguen en el total de cada hallazgo y
                  en el reporte de la CLI). ¿Quieres guardarlos en PDF antes?
                </p>
                {exported && (
                  <p className="mt-2 text-[13px] leading-5 text-muted">
                    Si guardaste el PDF, ya puedes reanudar con sesión nueva.
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      onExportAll();
                      setExported(true);
                    }}
                    className={exported ? "btn-outline" : "btn-primary"}
                  >
                    Guardar PDF
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => resume(true)}
                    className={exported ? "btn-primary" : "btn-outline"}
                  >
                    {busy && resuming === "new" ? "Reanudando…" : exported ? "Reanudar con sesión nueva" : "Reanudar sin guardar"}
                  </button>
                  <button type="button" disabled={busy} onClick={() => setConfirmingNew(false)} className="btn-text">
                    Cancelar
                  </button>
                </div>
              </div>
            </div>
          </div>
          <p className="mt-2 text-[13px] leading-5 text-muted">
            Con sesión nueva, la lista empieza vacía y muestra solo lo que se detecte desde ahora; lo anterior queda en
            el total de cada hallazgo, no se borra. Úsala después de corregir algo, para ver si vuelve a aparecer.
          </p>
        </>
      )}

      {enabled && (
        <>
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
          <p className="mt-2 text-[13px] leading-5 text-muted">
            Para empezar una sesión de detección nueva, pausa el monitoreo y reanúdalo con sesión nueva.
          </p>
        </>
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
                  onChange(false, false);
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
