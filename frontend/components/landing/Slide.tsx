import type { ReactNode } from "react";

const PARALLAX = 22;

// Una diapositiva del carrusel. El marco se desplaza 100% por diapositiva y el contenido
// 22% menos: así llega con un poco de retraso y se va más despacio, y da profundidad.
// Las que no están a la vista quedan inertes (sin foco ni lectores de pantalla).
export default function Slide({
  index,
  current,
  total,
  label,
  className,
  ready,
  bleed = false,
  children,
}: {
  index: number;
  current: number;
  total: number;
  label: string;
  className: string;
  ready: boolean;
  bleed?: boolean;
  children: ReactNode;
}) {
  const offset = index - current;
  const isCurrent = offset === 0;
  const inert = isCurrent ? {} : ({ inert: "" } as object);

  return (
    <section
      role="group"
      aria-roledescription="diapositiva"
      aria-label={`${label}, ${index + 1} de ${total}`}
      data-current={isCurrent}
      data-active={isCurrent && ready}
      className={`slide absolute inset-0 overflow-y-auto overscroll-contain ${className}`}
      style={{ transform: `translate3d(${offset * 100}%,0,0)`, opacity: isCurrent ? 1 : 0.3 }}
      {...inert}
    >
      <div
        className={`slide-inner flex min-h-full w-full flex-col ${
          bleed ? "" : "mx-auto max-w-[1320px] justify-center px-4 py-8 md:px-8 md:py-10"
        }`}
        style={{ transform: `translate3d(${-offset * PARALLAX}%,0,0)` }}
      >
        {children}
      </div>
    </section>
  );
}
