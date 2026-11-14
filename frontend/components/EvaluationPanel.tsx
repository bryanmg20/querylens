import { getDoc } from "@/lib/antipatterns";
import { formatDateTime, timeAgo } from "@/lib/format";
import type { EvaluationState, PathologyEvaluation } from "@/types/database";

const STATE_STYLE: Record<EvaluationState, { label: string; pill: string }> = {
  evaluada: { label: "Evaluada", pill: "pill-pos" },
  parcial: { label: "En parte", pill: "pill-warn" },
  no_evaluada: { label: "No evaluada", pill: "pill-neg" },
};

// Qué patologías se pudieron evaluar en la última ventana y por qué no las demás. Sin esto, una lista
// vacía de hallazgos se leería como «no hay problemas» cuando a veces es «no se pudo mirar».
export default function EvaluationPanel({
  evaluation,
  engine,
  serverTime,
}: {
  evaluation: PathologyEvaluation[];
  engine: string;
  serverTime: string;
}) {
  const applicable = evaluation.filter((e) => e.aplica);
  const evaluated = applicable.filter((e) => e.estado === "evaluada").length;
  const updated = evaluation.reduce<string | null>(
    (latest, e) => (latest == null || e.actualizado_en > latest ? e.actualizado_en : latest),
    null
  );

  return (
    <section className="card p-5" aria-labelledby="evaluation-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="evaluation-title" className="card-title">
          Estado de evaluación
        </h2>
        {applicable.length > 0 && (
          <span className="text-[13px] text-muted">
            {evaluated} de {applicable.length} evaluadas
          </span>
        )}
      </div>

      {evaluation.length === 0 ? (
        <p className="empty-frame mt-4">El detector todavía no procesó ninguna extracción de esta base.</p>
      ) : (
        <>
          <p className="mt-2 text-[13px] leading-5 text-muted">
            Sin hallazgos no es lo mismo que no evaluada: una patología que no se pudo evaluar puede estar ocurriendo.
            {updated && (
              <span title={formatDateTime(updated)}> Última ventana {timeAgo(updated, serverTime)}.</span>
            )}
          </p>
          <ul className="mt-4 space-y-2">
            {evaluation.map((item) => (
              <EvaluationRow key={item.codigo} item={item} engine={engine} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function EvaluationRow({ item, engine }: { item: PathologyEvaluation; engine: string }) {
  const doc = getDoc(item.antipatron);
  const title = doc?.title ?? item.antipatron;
  const sessionNotEvaluated = (item.ventanas_sesion.no_evaluada ?? 0) + (item.ventanas_sesion.parcial ?? 0);
  const sessionTotal = sessionNotEvaluated + (item.ventanas_sesion.evaluada ?? 0);

  if (!item.aplica) {
    return (
      <li className="flex items-center justify-between gap-3 text-[13px] text-muted">
        <span className="min-w-0 truncate" title={`${item.codigo} ${title}`}>
          <span className="font-mono">{item.codigo}</span> {title}
        </span>
        <span className="pill-neu" title="Depende de los bloqueos de InnoDB: solo aplica en MySQL.">
          No aplica en {engine === "postgresql" ? "PostgreSQL" : engine}
        </span>
      </li>
    );
  }

  const style = STATE_STYLE[item.estado] ?? STATE_STYLE.no_evaluada;
  const summary = (
    <span className="flex w-full items-center justify-between gap-3">
      <span className="min-w-0 truncate" title={`${item.codigo} ${title}`}>
        <span className="font-mono text-muted">{item.codigo}</span> {title}
      </span>
      <span className={style.pill}>{style.label}</span>
    </span>
  );

  if (item.estado === "evaluada") {
    return (
      <li className="text-[13px]" title={sessionNotEvaluated > 0 ? `${sessionNotEvaluated} de ${sessionTotal} ventanas de la sesión sin evaluar del todo` : undefined}>
        {summary}
      </li>
    );
  }

  return (
    <li className="text-[13px]">
      <details>
        <summary className="cursor-pointer list-none">{summary}</summary>
        <ul className="mt-2 space-y-2 border-l border-rule pl-3">
          {item.motivos.map((reason) => (
            <li key={`${reason.codigo}@${reason.seccion}`}>
              <p className="font-mono text-[12px] text-muted">
                {reason.codigo}
                {reason.seccion && ` · ${reason.seccion}`}
              </p>
              <p className="mt-0.5 leading-5">{reason.mensaje}</p>
              <p className="mt-1 whitespace-pre-wrap rounded bg-ground px-2 py-1.5 font-mono text-[12px] leading-5">
                {reason.accion}
              </p>
            </li>
          ))}
          {sessionTotal > 0 && (
            <li className="text-muted">
              En esta sesión: {sessionNotEvaluated} de {sessionTotal} ventanas sin evaluar del todo.
            </li>
          )}
        </ul>
      </details>
    </li>
  );
}
