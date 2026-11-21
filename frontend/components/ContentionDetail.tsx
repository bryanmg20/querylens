import { scopeLabel, type Contention, type LockInfo } from "@/lib/contention";

// Las sesiones de un hallazgo de contención: quién es cada una, qué sentencia corre y qué bloqueo
// espera o mantiene. En los abrazos mortales el ciclo; en CT-05 la sesión raíz y las bloqueadas.
export default function ContentionDetail({ contention }: { contention: Contention }) {
  const { kind, parties, targetsSource } = contention;
  return (
    <section className="mt-3" aria-label={kind === "cycle" ? "Transacciones del ciclo" : "Sesiones de la cascada"}>
      <h4 className="text-[13px] font-semibold">
        {kind === "cycle" ? "Transacciones del ciclo" : "Sesión raíz y sesiones bloqueadas"}
      </h4>
      <ul className="mt-2 grid gap-2 md:grid-cols-2">
        {parties.map((p) => (
          <li key={p.pid} className="min-w-0 rounded bg-ground px-3 py-2.5 text-[13px] leading-5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono font-semibold">pid {p.pid}</span>
              {p.tags.map((t) => (
                <span key={t} className="pill-neu">
                  {t}
                </span>
              ))}
              {p.blockedBy.length > 0 && (
                <span className="text-muted">
                  bloqueada por {p.blockedBy.map((b) => `pid ${b}`).join(", ")}
                </span>
              )}
            </div>
            {p.facts.length > 0 && <p className="text-muted">{p.facts.join(" · ")}</p>}
            {p.statement && (
              <pre className="mt-1.5 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono text-muted">
                {p.statement}
              </pre>
            )}
            {p.waits && <Lock label="Espera" lock={p.waits} />}
            {p.holds?.map((h, i) => <Lock key={i} label="Mantiene" lock={h} />)}
          </li>
        ))}
      </ul>
      {targetsSource && (
        <p className="mt-2 text-[13px] text-muted">
          {targetsSource === "ctid"
            ? "La fila de cada espera sale de su dirección física (ctid) en el log de esperas, no de los valores de la sentencia."
            : "La fila de cada espera sale de los valores de la sentencia que cubren un índice único."}
        </p>
      )}
    </section>
  );
}

function Lock({ label, lock }: { label: string; lock: LockInfo }) {
  const scope = scopeLabel(lock.scope);
  return (
    <p className="mt-1">
      <span className="text-muted">{label} </span>
      {lock.mode && <code className="font-mono font-semibold">{lock.mode}</code>}
      {scope && <span> sobre {scope}</span>}
      {lock.resource && (
        <>
          {" "}
          en <code className="font-mono">{lock.resource}</code>
        </>
      )}
      {lock.targets.length > 0 && (
        <span className="text-muted" title={lock.targets.join(", ")}>
          {" "}
          ({lock.targets.slice(0, 2).join(", ")}
          {lock.targets.length > 2 && ` y ${lock.targets.length - 2} más`})
        </span>
      )}
    </p>
  );
}
