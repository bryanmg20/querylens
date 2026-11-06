export type DatabaseEngine = "postgresql" | "mysql";

export const DEFAULT_PORTS: Record<DatabaseEngine, number> = {
  postgresql: 5432,
  mysql: 3306,
};

export interface ConnectionFormValues {
  connectionName: string;
  engine: DatabaseEngine;
  host: string;
  port: number;
  username: string;
  password: string;
  databaseName: string;
}

export const DEFAULT_FORM_VALUES: ConnectionFormValues = {
  connectionName: "",
  engine: "postgresql",
  host: "",
  port: DEFAULT_PORTS.postgresql,
  username: "",
  password: "",
  databaseName: "",
};

export interface TestConnectionResponse {
  success: boolean;
  message: string;
  latency_ms?: number | null;
}

export interface DatabaseRegisteredResponse {
  id: string;
  database_identifier: string;
  connection_name: string;
  engine: string;
  host: string;
  port: number;
  database_name: string;
  is_active: boolean;
  monitoring_enabled: boolean;
  created_at: string;
}

export interface DatabaseSessionResponse extends DatabaseRegisteredResponse {
  access_token: string;
  token_type: string;
}

export interface Finding {
  id: number;
  antipatron: string;
  severidad: string;
  query_id: string | null;
  table_name: string | null;
  evidencia: Record<string, unknown>;
  explicacion: string;
  recomendacion: string;
  detectado_en: string;
}

export interface LastRun {
  status: "ok" | "degraded" | "failed";
  started_at: string;
  finished_at: string;
  sections_ok: string[];
  sections_failed: string[];
  msg_id: number | null;
  engine_version: string | null;
}

export interface HealthIssue {
  id: number;
  code: string;
  category: string;
  severity: "blocking" | "degraded" | "info";
  section: string;
  message: string;
  remediation: string;
  params: Record<string, unknown>;
  first_seen: string;
  last_seen: string;
  occurrences: number;
  resolved_at: string | null;
}

export interface Health {
  // null cuando el pipeline todavía no corrió contra esta base.
  last_run: LastRun | null;
  issues: HealthIssue[];
}

export interface DiagnosticsResponse {
  database_identifier: string;
  connection_name: string;
  engine: string;
  is_active: boolean;
  monitoring_enabled: boolean;
  diagnostics: Finding[];
  health: Health;
  server_time: string;
}

export interface RegisterDatabaseResult {
  data: DatabaseSessionResponse;
  // true cuando el backend respondió 200 (la conexión ya existía y se
  // actualizó) en vez de 201 (fila nueva).
  alreadyRegistered: boolean;
}

export interface ApiErrorResponse {
  detail?: string | { msg: string }[];
}

export interface MonitoringResponse {
  database_identifier: string;
  monitoring_enabled: boolean;
}
