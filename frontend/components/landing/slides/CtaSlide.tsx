import Link from "next/link";
import type { SlideProps } from "../HomeCarousel";

// Cierre: un campo de color plano con la acción, y el nombre de vuelta a todo el ancho.
export default function CtaSlide(_: SlideProps) {
  return (
    <>
      <section className="flex flex-1 items-center bg-accent text-[var(--on-accent)]">
        <div className="mx-auto grid w-full max-w-[1320px] gap-8 px-4 py-8 md:grid-cols-12 md:items-end md:px-8">
          <h2 className="text-[clamp(2.25rem,min(5.6vw,9vh),3.75rem)] font-semibold leading-[1.02] tracking-[-0.035em] md:col-span-8">
            <span className="sl-mask block">
              <span className="sl-line" style={{ ["--i" as string]: 0 }}>
                Conecta tu primera
              </span>
            </span>
            <span className="sl-mask block">
              <span className="sl-line" style={{ ["--i" as string]: 1 }}>
                base de datos
              </span>
            </span>
          </h2>
          <div className="sl md:col-span-4 md:justify-self-end" style={{ ["--i" as string]: 3 }}>
            <p className="max-w-[24rem] text-[18px] leading-[1.5]">
              Regístrala con sus credenciales y en unos segundos verás tus primeros diagnósticos.
            </p>
            <Link
              href="/conectar"
              className="btn mt-6 h-12 bg-[var(--on-accent)] px-7 text-[16px] font-semibold text-accent [@media(hover:hover)]:hover:opacity-90"
            >
              Conectar una base de datos
            </Link>
          </div>
        </div>
      </section>

      <footer className="bg-ground pb-4 pt-6">
        <div className="mx-auto w-full max-w-[1320px] overflow-hidden px-4 md:px-8">
          <p
            aria-hidden="true"
            className="sl select-none whitespace-nowrap text-[clamp(3rem,min(18vw,28vh),17rem)] font-semibold leading-[0.9] tracking-[-0.05em]"
            style={{ ["--i" as string]: 4 }}
          >
            QueryLens
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-between gap-4 text-[13px] text-muted">
            <p>Observabilidad de consultas para PostgreSQL y MySQL.</p>
            <nav aria-label="Pie" className="flex gap-6">
              <Link href="/conectar" className="transition-colors duration-150 hover:text-fg">
                Conectar base
              </Link>
              <Link href="/diagnostics" className="transition-colors duration-150 hover:text-fg">
                Diagnósticos
              </Link>
            </nav>
          </div>
        </div>
      </footer>
    </>
  );
}
