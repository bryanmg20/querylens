// El detector escribe la recomendación como un párrafo con el SQL dentro (CREATE INDEX, ANALYZE,
// CREATE STATISTICS…). Aquí se saca ese SQL para mostrarlo aparte y poder copiarlo. El párrafo
// no se toca: se muestra completo y el SQL sale además, sin repetirse.
import type { Finding } from "@/types/database";

// Solo sentencias que no se confunden con prosa: "ANALYZE sobre la tabla" no es SQL, pero
// "ANALYZE public.orders;" sí. Cada patrón es una sentencia completa.
const STATEMENTS: RegExp[] = [
  /CREATE INDEX (?:CONCURRENTLY )?ON [\w."`]+ \([^)]*\)/g,
  /CREATE STATISTICS [^;]+?FROM [\w."`]+/g,
  /ALTER TABLE [\w."`]+ ALTER COLUMN [\w"`]+ SET STATISTICS \d+/g,
  /ANALYZE TABLE [\w."`]+ UPDATE HISTOGRAM ON [^;]+? WITH \d+ BUCKETS/g,
  /ANALYZE [\w."`]+(?=;)/g,
  /DROP INDEX [\w."`]+(?: ON [\w."`]+)?/g,
];

/** Las sentencias SQL de la recomendación, en el orden en que aparecen y sin repetir. */
export function recommendedSql(finding: Finding): string[] {
  const found: { at: number; sql: string }[] = [];
  for (const pattern of STATEMENTS) {
    for (const m of finding.recomendacion.matchAll(pattern)) found.push({ at: m.index ?? 0, sql: m[0].trim() });
  }
  // El detector también deja el índice sugerido en la evidencia (varios, separados por "; ").
  const suggested = (finding.evidencia as Record<string, unknown>).suggested_index;
  if (typeof suggested === "string") {
    for (const part of suggested.split(";")) if (part.trim()) found.push({ at: Number.MAX_SAFE_INTEGER, sql: part.trim() });
  }
  const seen = new Set<string>();
  return found
    .sort((a, b) => a.at - b.at)
    .map((f) => f.sql)
    .filter((sql) => {
      const key = sql.replace(/\s+/g, " ").toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((sql) => `${sql};`);
}
