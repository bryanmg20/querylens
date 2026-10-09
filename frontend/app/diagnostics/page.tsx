"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getAccessToken } from "@/lib/session";

// Página de diagnósticos. Por ahora solo muestra el título; el contenido (el
// polling a la API REST con el access token) se implementa después.
export default function DiagnosticsPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Sin access token no hay sesión: se vuelve al formulario de conexión.
    if (!getAccessToken()) {
      router.replace("/");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) return null;

  return (
    <main className="min-h-screen px-4 py-10 sm:py-16">
      <div className="mx-auto w-full max-w-4xl">
        <h1 className="text-2xl text-ink sm:text-3xl">Diagnósticos</h1>
      </div>
    </main>
  );
}
