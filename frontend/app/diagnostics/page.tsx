"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ActivityPanel } from "@/components/ActivityPanel";
import DiagnosticsSkeleton from "@/components/DiagnosticsSkeleton";
import FindingsPanel, { NO_FILTERS, filterFindings, type Filters } from "@/components/FindingsPanel";
import HealthPanel from "@/components/HealthPanel";
import KpiStrip from "@/components/KpiStrip";
import MonitoringControl from "@/components/MonitoringControl";
import PrintReport from "@/components/PrintReport";
import ReadingGuide from "@/components/ReadingGuide";
import StatusSummary from "@/components/StatusSummary";
import { ApiError, fetchDiagnostics, setMonitoring } from "@/lib/api";
import { formatDateTime, formatShortDateTime } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { getDoc } from "@/lib/antipatterns";
import { SEVERITY_META, countBySeverity } from "@/lib/insights";
import { clearAccessToken, getAccessToken } from "@/lib/session";
import type { DiagnosticsResponse } from "@/types/database";

// Cada cuánto se vuelve a pedir el estado a la API REST.
const POLL_INTERVAL_MS = 30_000;
const GUIDE_KEY = "querylens_guide_closed";

// La frase bajo el título: el hecho de hoy que más pide atención.
function headline(data: DiagnosticsResponse): { text: string; tone: string } {
  const blocking = data.health.issues.filter((i) => i.resolved_at == null && i.severity === "blocking").length;
  const high = countBySeverity(data.diagnostics).high;
  const total = data.diagnostics.length;

  if (blocking > 0) {
    return {
      text: `${blocking} ${blocking === 1 ? "problema bloquea" : "problemas bloquean"} la extracción de esta base.`,
      tone: "var(--negative)",
    };
  }
  if (!data.monitoring_enabled) {
    return data.health.last_run == null
      ? { text: "El monitoreo no ha iniciado: inícialo para empezar a recolectar diagnósticos.", tone: "var(--warning)" }
      : { text: "El monitoreo está en pausa: no se recolectan nuevos diagnósticos.", tone: "var(--warning)" };
  }
  if (data.health.last_run == null) {
    return { text: "Esperando la primera extracción.", tone: "var(--muted)" };
  }
  if (high > 0) {
    return {
      text: `${high} de ${total} ${total === 1 ? "hallazgo es" : "hallazgos son"} de severidad alta.`,
      tone: "var(--negative)",
    };
  }
  if (total > 0) {
    return {
      text: `${total} ${total === 1 ? "hallazgo" : "hallazgos"}, ninguno de severidad alta.`,
      tone: "var(--warning)",
    };
  }
  return { text: "No se han detectado antipatrones en esta sesión.", tone: "var(--positive)" };
}

// Los filtros activos en palabras, para el encabezado del PDF. null si no hay ninguno.
function describeFilters(filters: Filters): string | null {
  const parts: string[] = [];
  if (filters.severity !== "all") parts.push(`severidad ${SEVERITY_META[filters.severity].label.toLowerCase()}`);
  if (filters.pattern !== "all") parts.push(getDoc(filters.pattern)?.title.toLowerCase() ?? filters.pattern);
  if (filters.query.trim()) parts.push(`búsqueda «${filters.query.trim()}»`);
  return parts.length ? parts.join(", ") : null;
}

export default function DiagnosticsPage() {
  const router = useRouter();
  const [data, setData] = useState<DiagnosticsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cycle, setCycle] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [monitoringBusy, setMonitoringBusy] = useState(false);
  const [monitoringError, setMonitoringError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [guideOpen, setGuideOpen] = useState(false);
  const refresh = useRef<() => void>(() => {});
  const now = useNow(1000);

  useEffect(() => {
    try {
      setGuideOpen(localStorage.getItem(GUIDE_KEY) !== "1");
    } catch {
      setGuideOpen(true);
    }
  }, []);

  function setGuide(open: boolean) {
    setGuideOpen(open);
    try {
      localStorage.setItem(GUIDE_KEY, open ? "0" : "1");
    } catch {
      // sin localStorage la guía simplemente vuelve a abrirse en la próxima visita
    }
  }

  useEffect(() => {
    const token = getAccessToken();
    // Sin access token no hay sesión: se vuelve al formulario de conexión.
    if (!token) {
      router.replace("/conectar");
      return;
    }

    let cancelled = false;

    async function poll(accessToken: string, manual = false) {
      if (manual) setRefreshing(true);
      try {
        const result = await fetchDiagnostics(accessToken);
        if (cancelled) return;
        setData(result);
        setError(null);
        setCycle((n) => n + 1);
        setUpdatedAt(Date.now());
      } catch (err) {
        if (cancelled) return;
        // 401: el token venció o no es válido; hay que volver a iniciar sesión.
        if (err instanceof ApiError && err.status === 401) {
          clearAccessToken();
          router.replace("/conectar");
          return;
        }
        setError(err instanceof Error ? err.message : "No fue posible consultar los diagnósticos");
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    }

    refresh.current = () => poll(token, true);
    poll(token);
    const timer = setInterval(() => poll(token), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [router]);

  async function handleMonitoringChange(enabled: boolean, newSession: boolean) {
    const token = getAccessToken();
    if (!token) {
      router.replace("/conectar");
      return;
    }

    setMonitoringBusy(true);
    setMonitoringError(null);
    const sessionBefore = data ? new Date(data.detection_session_started_at).getTime() : null;
    try {
      const result = await setMonitoring(token, enabled, newSession);
      // Se refleja de inmediato; el siguiente polling confirma el estado. Con una sesión nueva la
      // lista vuelve a empezar vacía.
      const sessionChanged =
        sessionBefore !== null && new Date(result.detection_session_started_at).getTime() !== sessionBefore;
      setData((current) =>
        current
          ? {
              ...current,
              monitoring_enabled: result.monitoring_enabled,
              detection_session_started_at: result.detection_session_started_at,
              diagnostics: sessionChanged ? [] : current.diagnostics,
            }
          : current
      );
      if (sessionChanged) {
        setFilters(NO_FILTERS);
        refresh.current();
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearAccessToken();
        router.replace("/conectar");
        return;
      }
      setMonitoringError(
        err instanceof Error ? err.message : "No fue posible cambiar el estado del monitoreo"
      );
    } finally {
      setMonitoringBusy(false);
    }
  }

  const summary = data ? headline(data) : null;
  const seconds = updatedAt ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;

  return (
    <main className="mx-auto w-full max-w-[1320px] px-4 pb-24 pt-8 md:px-8 md:pb-12 print:p-0">
      {data && <PrintReport data={data} findings={filterFindings(data.diagnostics, filters)} filterNote={describeFilters(filters)} />}
      <div className="print:hidden">
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <p className="text-[13px] text-muted">
            <span className="text-fg">QueryLens</span>
            <span aria-hidden="true"> / </span>
            {data ? data.connection_name : "Diagnósticos"}
          </p>
          <h1 className="mt-2 text-[24px] font-semibold leading-8 tracking-[-0.02em] md:text-[32px] md:leading-10">Diagnósticos</h1>
          <p
            className="mt-1 max-w-2xl text-[14px] leading-5 text-muted"
            title={data ? `Inicio de la sesión de detección: ${formatDateTime(data.detection_session_started_at)}` : undefined}
          >
            {data
              ? `Desde el ${formatShortDateTime(data.detection_session_started_at)}, lo que QueryLens encontró en ${data.connection_name}.`
              : "Consultando…"}
          </p>
          {summary && (
            <p className="mt-2 flex items-center gap-2 text-[14px]">
              <span className="h-2 w-2 flex-none rounded-full" style={{ background: summary.tone }} />
              {summary.text}
            </p>
          )}
        </div>

        {data && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <button
              type="button"
              aria-expanded={guideOpen}
              aria-controls="reading-guide"
              onClick={() => setGuide(!guideOpen)}
              className="btn-text"
            >
              ¿Cómo leer esta página?
            </button>
            {seconds != null && <span className="text-[13px] text-muted">Actualizado hace {seconds} s</span>}
            <button type="button" onClick={() => refresh.current()} disabled={refreshing} className="btn-outline">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
                className={refreshing ? "animate-spin" : ""}
              >
                <path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" />
              </svg>
              Actualizar
            </button>
          </div>
        )}
      </header>

      {error && (
        <p className="notice-neg mt-6" role="alert">
          {error}
          {data && " Se muestran los últimos datos recibidos."}
        </p>
      )}

      {!data && !error && (
        <div className="mt-8">
          <DiagnosticsSkeleton />
        </div>
      )}

      {data && (
        <>
          <div className="mt-6">
            <KpiStrip
              data={data}
              severity={filters.severity}
              onSeverity={(severity) => setFilters((f) => ({ ...f, severity }))}
            />
          </div>

          <ReadingGuide open={guideOpen} onClose={() => setGuide(false)} />

          <div className="mt-6 grid items-start gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
            <div className="space-y-6">
              <StatusSummary data={data} pollSeconds={POLL_INTERVAL_MS / 1000} cycle={cycle} />
              <MonitoringControl
                enabled={data.monitoring_enabled}
                started={data.health.last_run != null}
                busy={monitoringBusy}
                error={monitoringError}
                sessionStartedAt={data.detection_session_started_at}
                serverTime={data.server_time}
                onChange={handleMonitoringChange}
              />
              <HealthPanel issues={data.health.issues} serverTime={data.server_time} />
              {data.activity.length > 0 && <ActivityPanel points={data.activity} serverTime={data.server_time} />}
            </div>

            <div className="order-first min-w-0 lg:order-none">
              <FindingsPanel
                findings={data.diagnostics}
                waiting={data.health.last_run == null}
                serverTime={data.server_time}
                filters={filters}
                onFilters={setFilters}
              />
            </div>
          </div>
        </>
      )}
      </div>
    </main>
  );
}
