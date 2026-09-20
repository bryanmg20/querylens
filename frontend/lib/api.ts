import type {
  ApiErrorResponse,
  ConnectionFormValues,
  DatabaseRegisteredResponse,
  TestConnectionResponse,
} from "@/types/database";

const AUTH_SERVICE_URL =
  process.env.NEXT_PUBLIC_AUTH_SERVICE_URL ?? "http://localhost:8000";

function toApiPayload(values: ConnectionFormValues) {
  return {
    connection_name: values.connectionName,
    engine: values.engine,
    host: values.host,
    port: values.port,
    username: values.username,
    password: values.password,
    database_name: values.databaseName,
  };
}

async function parseErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorResponse;
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg).join(", ");
    }
  } catch {
    // ignore parse errors, fall back to the generic message below
  }
  return `Error inesperado (${response.status})`;
}

export async function testConnection(
  values: ConnectionFormValues
): Promise<TestConnectionResponse> {
  const response = await fetch(`${AUTH_SERVICE_URL}/databases/test-connection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toApiPayload(values)),
  });

  if (!response.ok) {
    return { success: false, message: await parseErrorMessage(response) };
  }

  return response.json();
}

export async function registerDatabase(
  values: ConnectionFormValues
): Promise<DatabaseRegisteredResponse> {
  const response = await fetch(`${AUTH_SERVICE_URL}/databases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toApiPayload(values)),
  });

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  return response.json();
}
