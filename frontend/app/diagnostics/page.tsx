"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import FindingsPanel from "@/components/FindingsPanel";
import HealthPanel from "@/components/HealthPanel";
import StatusSummary from "@/components/StatusSummary";
import { ApiError, fetchDiagnostics } from "@/lib/api";
import { clearAccessToken, getAccessToken } from "@/lib/session";
import type { DiagnosticsResponse } from "@/types/database";

// Cada cuánto se vuelve a pedir el estado a la API REST.
const POLL_INTERVAL_MS = 30_000;

export default function DiagnosticsPage() {
  const router = useRouter();
  const [data, setData] = useState<DiagnosticsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = getAccessToken();
    // Sin access token no hay sesión: se vuelve al formulario de conexión.
    if (!token) {
      router.replace("/");
      return;
    }

    let cancelled = false;

    async function poll(accessToken: string) {
      try {
        const result = await fetchDiagnostics(accessToken);
        if (cancelled) return;
        setData(result);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        // 401: el token venció o no es válido; hay que volver a iniciar sesión.
        if (err instanceof ApiError && err.status === 401) {
          clearAccessToken();
          router.replace("/");
          return;
        }
        setError(err instanceof Error ? err.message : "No fue posible consultar los diagnósticos");
      }
    }

    poll(token);
    const timer = setInterval(() => poll(token), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [router]);

  return (
    <main className="min-h-screen px-4 py-10 sm:py-16">
      <div className="mx-auto w-full max-w-4xl space-y-8">
        <header>
          <h1 className="text-2xl text-ink sm:text-3xl">Diagnósticos</h1>
          <p className="mt-2 text-sm text-graphite-600">Se actualiza automáticamente cada 30 segundos.</p>
        </header>

        {error && (
          <div className="rounded border-l-2 border-alert bg-alert-soft px-3 py-2.5 text-sm text-ink">
            {error}
            {data && " Se muestran los últimos datos recibidos."}
          </div>
        )}

        {!data && !error && <p className="text-sm text-graphite-600">Consultando…</p>}

        {data && (
          <>
            <StatusSummary data={data} />
            <HealthPanel issues={data.health.issues} serverTime={data.server_time} />
            <FindingsPanel findings={data.diagnostics} waiting={data.health.last_run == null} />
          </>
        )}
      </div>
    </main>
  );
}
