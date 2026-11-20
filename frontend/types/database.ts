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

// Un problema detectado (una fila aunque se haya detectado miles de veces), con lo que pasó en la
// sesión de detección actual. Evidencia, explicación y recomendación son las de su última detección.
export interface Finding {
  id: number;
  antipatron: string;
  // Código de la patología (AP-01…AP-08, CT-01…CT-05; "CT" en un abrazo sin clasificar).
  codigo: string | null;
  // La más alta que tuvo en la sesión.
  severidad: string;
  // La de la última detección.
  severidad_actual: string;
  query_id: string | null;
  table_name: string | null;
  // Base donde ocurrió (en MySQL, el schema).
  database_name: string | null;
  evidencia: Record<string, unknown>;
  explicacion: string;
  recomendacion: string;
  // Lo que puede pasar al aplicar la recomendación (no la gravedad del problema).
  riesgo: string | null;
  // Costo del problema en milisegundos: tiempo de base de datos de su sentencia o espera acumulada
  // en contención. impacto_ms es el de la última ventana; impacto_sesion_ms, la suma de la sesión,
  // que es el orden de la lista. null si no se pudo medir (abrazos mortales).
  impacto_ms: number | null;
  impacto_sesion_ms: number | null;
  // Última detección.
  detectado_en: string;
  // Primera detección, en todo el histórico.
  primera_deteccion: string;
  veces_sesion: number;
  veces_total: number;
  // Detecciones de la sesión por severidad (solo las que tuvo).
  por_severidad: Record<string, number>;
}

// Por qué una patología no se evaluó (o se evaluó en parte) y qué hacer. origen: "recoleccion" (un
// problema del estado de la recolección) o "detector" (una condición de la propia regla).
export interface EvaluationReason {
  codigo: string;
  origen: "recoleccion" | "detector";
  severidad: "blocking" | "degraded" | "info" | null;
  seccion: string;
  mensaje: string;
  accion: string;
}

export type EvaluationState = "evaluada" | "parcial" | "no_evaluada";

// Estado de evaluación de una patología: distingue «sin hallazgos» de «no evaluada».
export interface PathologyEvaluation {
  codigo: string;
  antipatron: string;
  // En la última ventana procesada.
  estado: EvaluationState;
  motivos: EvaluationReason[];
  detalle: Record<string, unknown>;
  actualizado_en: string;
  // Ventanas de la sesión en cada estado.
  ventanas_sesion: Partial<Record<EvaluationState, number>>;
  // false en CT-03 y CT-04 sobre PostgreSQL.
  aplica: boolean;
}

// Detecciones de una hora de las últimas 24 h (inicio de la hora).
export interface ActivityPoint {
  hora: string;
  detecciones: number;
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
  // Inicio de la sesión de detección: diagnostics trae solo lo detectado desde entonces.
  detection_session_started_at: string;
  // Ordenados por impacto de la sesión.
  diagnostics: Finding[];
  // Una por patología del catálogo; vacía si el detector todavía no procesó ningún snapshot.
  evaluation: PathologyEvaluation[];
  // Solo las horas que tuvieron detecciones.
  activity: ActivityPoint[];
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
  detection_session_started_at: string;
}
