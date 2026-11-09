"use client";

import { useEffect, useState } from "react";
import { SAMPLE_DIAGNOSTICS } from "@/lib/landing-data";
import { ActivityPanel, PatternsPanel } from "../../ActivityPanel";
import SeverityPanel from "../../SeverityPanel";
import StatusSummary from "../../StatusSummary";
import type { SlideProps } from "../HomeCarousel";

const POLL_SECONDS = 30;

// La pantalla Diagnósticos de verdad, con los mismos componentes que usa la aplicación
// pero alimentados con datos de ejemplo. Se monta solo en el navegador: sus horas se
// formatean con la zona del visitante.
export default function DashboardSlide({ active }: SlideProps) {
  const [mounted, setMounted] = useState(false);
  const [cycle, setCycle] = useState(0);
  const data = SAMPLE_DIAGNOSTICS;

  useEffect(() => {
    setMounted(true);
  }, []);

  // La cuenta atrás del anillo vuelve a empezar cada vez que se llega a la diapositiva.
  useEffect(() => {
    if (active) setCycle((n) => n + 1);
  }, [active]);

  return (
    <div className="grid gap-8 lg:grid-cols-12 lg:items-center lg:gap-10">
      <div className="lg:col-span-4">
        <h2 className="text-[clamp(2rem,min(4.4vw,7.5vh),3.75rem)] font-semibold leading-[1.04] tracking-[-0.03em]">
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 0 }}>
              Todo en una
            </span>
          </span>
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 1 }}>
              sola pantalla
            </span>
          </span>
        </h2>
        <p className="sl mt-6 max-w-[26rem] text-[18px] leading-[1.55] text-muted" style={{ ["--i" as string]: 3 }}>
          Así se ve Diagnósticos con datos de ejemplo: el pulso de la extracción, la severidad y las detecciones en el
          tiempo.
        </p>
      </div>

      <div className="sl min-w-0 lg:col-span-8" style={{ ["--i" as string]: 3 }}>
        <div className="min-h-[600px] space-y-6">
          {mounted && (
            <>
              <div data-ui className="grid grid-cols-1 gap-6 sm:grid-cols-5">
                <div className="min-w-0 sm:col-span-3 [&>section]:h-full">
                  <StatusSummary data={data} pollSeconds={POLL_SECONDS} cycle={cycle} />
                </div>
                <div className="min-w-0 sm:col-span-2 [&>section]:h-full">
                  <SeverityPanel findings={data.diagnostics} serverTime={data.server_time} />
                </div>
              </div>
              <div data-ui className="grid grid-cols-1 gap-6 sm:grid-cols-5">
                <div className="min-w-0 sm:col-span-3 [&>section]:h-full">
                  <ActivityPanel points={data.activity} serverTime={data.server_time} chartHeight={232} />
                </div>
                <div className="min-w-0 sm:col-span-2 [&>section]:h-full">
                  <PatternsPanel findings={data.diagnostics} />
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
