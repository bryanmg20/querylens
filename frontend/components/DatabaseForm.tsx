"use client";

import { useMemo, useState } from "react";
import { registerDatabase, testConnection } from "@/lib/api";
import {
  DEFAULT_FORM_VALUES,
  DEFAULT_PORTS,
  type ConnectionFormValues,
  type DatabaseEngine,
} from "@/types/database";
import SignalTrace, { type TraceState } from "./SignalTrace";

type RegisterFeedback =
  | { type: "idle" }
  | { type: "success"; identifier: string }
  | { type: "error"; message: string };

function buildDsnPreview(values: ConnectionFormValues): string {
  const scheme = values.engine === "mysql" ? "mysql" : "postgresql";
  const user = values.username || "usuario";
  const host = values.host || "host";
  const port = values.port || DEFAULT_PORTS[values.engine];
  const db = values.databaseName || "basededatos";
  return `${scheme}://${user}@${host}:${port}/${db}`;
}

export default function DatabaseForm() {
  const [values, setValues] = useState<ConnectionFormValues>(DEFAULT_FORM_VALUES);
  const [registering, setRegistering] = useState(false);
  const [traceState, setTraceState] = useState<TraceState>({ status: "idle" });
  const [registerFeedback, setRegisterFeedback] = useState<RegisterFeedback>({ type: "idle" });

  const testing = traceState.status === "testing";
  const dsnPreview = useMemo(() => buildDsnPreview(values), [values]);

  function update<K extends keyof ConnectionFormValues>(key: K, value: ConnectionFormValues[K]) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function handleEngineChange(engine: DatabaseEngine) {
    setValues((prev) => {
      const wasDefaultPort = prev.port === DEFAULT_PORTS[prev.engine];
      return { ...prev, engine, port: wasDefaultPort ? DEFAULT_PORTS[engine] : prev.port };
    });
  }

  async function handleTestConnection() {
    setTraceState({ status: "testing" });
    try {
      const result = await testConnection(values);
      setTraceState(
        result.success
          ? { status: "success", latencyMs: result.latency_ms ?? null, message: result.message }
          : { status: "error", message: result.message }
      );
    } catch (error) {
      setTraceState({
        status: "error",
        message: error instanceof Error ? error.message : "No fue posible probar la conexión",
      });
    }
  }

  async function handleRegister(event: React.FormEvent) {
    event.preventDefault();
    setRegistering(true);
    setRegisterFeedback({ type: "idle" });
    try {
      const result = await registerDatabase(values);
      setRegisterFeedback({ type: "success", identifier: result.database_identifier });
    } catch (error) {
      setRegisterFeedback({
        type: "error",
        message: error instanceof Error ? error.message : "No fue posible registrar la base de datos",
      });
    } finally {
      setRegistering(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_296px]">
      <form onSubmit={handleRegister} className="min-w-0 space-y-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Nombre de la conexión">
            <input
              required
              type="text"
              value={values.connectionName}
              onChange={(e) => update("connectionName", e.target.value)}
              placeholder="Producción - Ventas"
              className="input"
            />
          </Field>

          <Field label="Motor">
            <select
              value={values.engine}
              onChange={(e) => handleEngineChange(e.target.value as DatabaseEngine)}
              className="input"
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
            </select>
          </Field>
        </div>

        <div className="grid grid-cols-1 gap-4 border-t panel-line pt-6 sm:grid-cols-2">
          <Field label="Host">
            <input
              required
              type="text"
              value={values.host}
              onChange={(e) => update("host", e.target.value)}
              className="input-mono"
            />
            {(values.host === "localhost" || values.host === "127.0.0.1") && (
              <p className="mt-1 text-xs text-graphite-600">
                Si la base de datos corre en este mismo servidor (fuera de un
                contenedor), usa <code className="font-mono">host.docker.internal</code>{" "}
                en vez de <code className="font-mono">{values.host}</code>.
              </p>
            )}
          </Field>

          <Field label="Puerto">
            <input
              required
              type="number"
              value={values.port}
              onChange={(e) => update("port", Number(e.target.value))}
              className="input-mono"
            />
          </Field>

          <Field label="Usuario">
            <input
              required
              type="text"
              value={values.username}
              onChange={(e) => update("username", e.target.value)}
              className="input-mono"
            />
          </Field>

          <Field label="Contraseña">
            <input
              required
              type="password"
              value={values.password}
              onChange={(e) => update("password", e.target.value)}
              className="input-mono"
            />
          </Field>

          <Field label="Nombre de la base de datos" className="sm:col-span-2">
            <input
              required
              type="text"
              value={values.databaseName}
              onChange={(e) => update("databaseName", e.target.value)}
              className="input-mono"
            />
          </Field>
        </div>

        {registerFeedback.type === "success" && (
          <div className="rounded border-l-2 border-signal bg-signal-soft px-3 py-2.5">
            <p className="text-xs text-graphite-600">Registrada. Identificador asignado:</p>
            <p className="mt-0.5 font-mono text-sm text-ink">{registerFeedback.identifier}</p>
          </div>
        )}
        {registerFeedback.type === "error" && (
          <div className="rounded border-l-2 border-alert bg-alert-soft px-3 py-2.5 text-sm text-ink">
            {registerFeedback.message}
          </div>
        )}

        <div className="flex flex-col gap-3 sm:flex-row">
          <button
            type="button"
            onClick={handleTestConnection}
            disabled={testing}
            className="flex-1 rounded border border-signal px-4 py-2 text-sm text-signal transition hover:bg-signal-soft disabled:cursor-not-allowed disabled:opacity-50"
          >
            {testing ? "Probando…" : "Probar conexión"}
          </button>
          <button
            type="submit"
            disabled={registering}
            className="flex-1 rounded bg-ink px-4 py-2 text-sm text-paper transition hover:bg-graphite-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {registering ? "Registrando…" : "Registrar base de datos"}
          </button>
        </div>
      </form>

      <aside className="h-fit space-y-4 lg:sticky lg:top-10">
        <div className="rounded border border-graphite-600 bg-ink px-4 py-4">
          <p className="truncate text-xs text-graphite-300">
            {values.connectionName || "Sin nombre todavía"}
          </p>
          <p className="mt-1 break-all font-mono text-sm text-paper">{dsnPreview}</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Badge>{values.engine}</Badge>
          </div>
        </div>

        <SignalTrace state={traceState} />
      </aside>
    </div>
  );
}

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`block ${className}`}>
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-sm border border-graphite-600 px-1.5 py-0.5 font-mono text-[11px] text-graphite-300">
      {children}
    </span>
  );
}
