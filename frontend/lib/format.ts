// Formato de fechas para la página de diagnósticos.

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("es-CO", { dateStyle: "short", timeStyle: "medium" });
}

export function secondsBetween(fromIso: string, toIso: string): number {
  return (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 1000;
}

// "hace 12 s", "hace 5 min", "hace 3 h", "hace 2 d" respecto a `nowIso` (la
// hora del servidor, para no depender del reloj del navegador).
export function timeAgo(iso: string, nowIso: string): string {
  const seconds = Math.max(0, Math.round(secondsBetween(iso, nowIso)));
  if (seconds < 60) return `hace ${seconds} s`;
  if (seconds < 3600) return `hace ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `hace ${Math.floor(seconds / 3600)} h`;
  return `hace ${Math.floor(seconds / 86400)} d`;
}
