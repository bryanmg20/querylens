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
  created_at: string;
}

export interface DatabaseSessionResponse extends DatabaseRegisteredResponse {
  access_token: string;
  token_type: string;
}

export interface DiagnosticsResponse {
  database_identifier: string;
  connection_name: string;
  engine: string;
  is_active: boolean;
  diagnostics: Record<string, unknown>[];
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
