"use client";

import { useEffect, useState } from "react";

const RADIUS = 26;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

// Anillo fino con la cuenta atrás hasta el siguiente sondeo. `cycle` cambia con
// cada respuesta de la API y reinicia la cuenta.
export default function RefreshRing({ cycle, seconds }: { cycle: number; seconds: number }) {
  const [left, setLeft] = useState(seconds);

  useEffect(() => {
    setLeft(seconds);
    const timer = setInterval(() => setLeft((value) => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [cycle, seconds]);

  return (
    <div className="relative h-16 w-16 flex-none" role="timer" aria-label={`Próxima lectura en ${left} segundos`}>
      <svg width={64} height={64} viewBox="0 0 64 64" className="-rotate-90" aria-hidden="true">
        <circle cx={32} cy={32} r={RADIUS} fill="none" stroke="var(--grid)" strokeWidth={3} />
        <circle
          className="ring-progress"
          cx={32}
          cy={32}
          r={RADIUS}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={3}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - left / seconds)}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="text-base font-semibold">{left}</span>
        <span className="mt-0.5 text-[11px] text-muted">seg</span>
      </div>
    </div>
  );
}
