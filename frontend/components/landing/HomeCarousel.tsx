"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { LensMark } from "../icons";
import ThemeButton from "../ThemeButton";
import Slide from "./Slide";
import CtaSlide from "./slides/CtaSlide";
import DashboardSlide from "./slides/DashboardSlide";
import ExplorerSlide from "./slides/ExplorerSlide";
import FlowSlide from "./slides/FlowSlide";
import HealthSlide from "./slides/HealthSlide";
import HeroSlide from "./slides/HeroSlide";

export interface SlideProps {
  active: boolean;
  go: (index: number) => void;
}

const SLIDES: { id: string; label: string; bg: string; bleed?: boolean; View: (props: SlideProps) => JSX.Element }[] = [
  { id: "inicio", label: "Inicio", bg: "bg-ground", View: HeroSlide },
  { id: "como-funciona", label: "Cómo funciona", bg: "bg-surface", View: FlowSlide },
  { id: "tablero", label: "Tablero", bg: "bg-ground", View: DashboardSlide },
  { id: "antipatrones", label: "Antipatrones", bg: "bg-surface", View: ExplorerSlide },
  { id: "salud", label: "Salud", bg: "bg-ground", View: HealthSlide },
  { id: "conectar", label: "Conectar", bg: "bg-ground", View: CtaSlide, bleed: true },
];

const LAST = SLIDES.length - 1;
const LOCK_MS = 850; // lo que dura el desplazamiento
const WHEEL_QUIET_MS = 140; // silencio que marca el final de un gesto de rueda o trackpad
const SWIPE_PX = 56;

// La página de inicio como un carrusel a pantalla completa. Se navega con la rueda, el
// teclado, un gesto horizontal, las flechas o los segmentos; la URL guarda la diapositiva.
export default function HomeCarousel() {
  const [index, setIndex] = useState(0);
  const [ready, setReady] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const indexRef = useRef(0);
  const lockedUntil = useRef(0);
  const lastWheel = useRef(0);

  const go = useCallback((next: number) => {
    const target = Math.max(0, Math.min(LAST, next));
    if (target === indexRef.current) return;
    indexRef.current = target;
    lockedUntil.current = performance.now() + LOCK_MS;
    setIndex(target);
    history.replaceState(null, "", `#${SLIDES[target].id}`);
  }, []);

  // Diapositiva inicial desde la URL, sin animar el salto; después se activan las transiciones.
  useEffect(() => {
    const fromHash = SLIDES.findIndex((s) => `#${s.id}` === window.location.hash);
    if (fromHash > 0) {
      indexRef.current = fromHash;
      setIndex(fromHash);
    }
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setReady(true)));
    const onHash = () => {
      const i = SLIDES.findIndex((s) => `#${s.id}` === window.location.hash);
      if (i >= 0) go(i);
    };
    window.addEventListener("hashchange", onHash);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("hashchange", onHash);
    };
  }, [go]);

  // Teclado. Lo que ya manejó otro control (las pestañas del explorador) no se repite aquí.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const el = event.target as HTMLElement | null;
      if (el?.closest("input, textarea, select, [role=tablist]")) return;
      const key = event.key;
      if (key === "ArrowRight" || key === "PageDown") go(indexRef.current + 1);
      else if (key === "ArrowLeft" || key === "PageUp") go(indexRef.current - 1);
      else if (key === "Home") go(0);
      else if (key === "End") go(LAST);
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  // Rueda y trackpad. Solo cuenta el primer evento de cada gesto (la inercia del trackpad
  // sigue disparando eventos) y una diapositiva más alta que la pantalla se recorre antes.
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      const now = performance.now();
      const continuing = now - lastWheel.current < WHEEL_QUIET_MS;
      lastWheel.current = now;

      const horizontal = Math.abs(event.deltaX) > Math.abs(event.deltaY);
      const delta = horizontal ? event.deltaX : event.deltaY;
      if (Math.abs(delta) < 12) return;

      if (!horizontal) {
        const slide = (event.target as HTMLElement).closest<HTMLElement>("section.slide");
        if (slide) {
          const canDown = slide.scrollTop + slide.clientHeight < slide.scrollHeight - 2;
          const canUp = slide.scrollTop > 0;
          if ((delta > 0 && canDown) || (delta < 0 && canUp)) return;
        }
      }
      event.preventDefault();
      if (continuing || now < lockedUntil.current) return;
      go(indexRef.current + (delta > 0 ? 1 : -1));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [go]);

  // Gesto horizontal en pantallas táctiles: basta un arrastre corto o rápido.
  const swipe = useRef<{ x: number; y: number; t: number } | null>(null);
  function onPointerDown(event: React.PointerEvent) {
    if (event.pointerType === "mouse" || (event.target as HTMLElement).closest("[data-no-swipe]")) return;
    swipe.current = { x: event.clientX, y: event.clientY, t: performance.now() };
  }
  function onPointerUp(event: React.PointerEvent) {
    const start = swipe.current;
    swipe.current = null;
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    const velocity = Math.abs(dx) / Math.max(1, performance.now() - start.t);
    if (Math.abs(dx) > Math.abs(dy) * 1.5 && (Math.abs(dx) > SWIPE_PX || velocity > 0.4)) go(index + (dx < 0 ? 1 : -1));
  }

  return (
    <div data-ready={ready} className="fixed inset-0 grid grid-rows-[64px_minmax(0,1fr)_72px] bg-ground">
      <noscript>
        <style>{`.slide{position:relative!important;transform:none!important;opacity:1!important;visibility:visible!important}.slide-inner{transform:none!important}.sl,.sl-line{opacity:1!important;transform:none!important}`}</style>
      </noscript>

      <header className="z-20 bg-ground">
        <nav
          aria-label="Principal"
          className="mx-auto flex h-16 w-full max-w-[1320px] items-center justify-between px-4 md:px-8"
        >
          <button
            type="button"
            onClick={() => go(0)}
            className="flex items-center gap-2.5 text-base font-semibold tracking-[-0.01em] transition-transform duration-150 ease-out active:scale-[0.97]"
          >
            <LensMark size={28} />
            QueryLens
          </button>

          <div className="flex items-center gap-8">
            <ul className="hidden items-center gap-7 lg:flex">
              {SLIDES.slice(1, LAST).map((slide, i) => {
                const selected = index === i + 1;
                return (
                  <li key={slide.id}>
                    <button
                      type="button"
                      onClick={() => go(i + 1)}
                      aria-current={selected ? "true" : undefined}
                      className={`relative h-10 text-[13px] transition-colors duration-150 ${
                        selected ? "text-fg" : "text-muted hover:text-fg"
                      }`}
                    >
                      {slide.label}
                      <span
                        aria-hidden="true"
                        className={`absolute inset-x-0 bottom-1.5 h-px origin-left bg-accent transition-transform duration-300 ease-[var(--ease-out)] ${
                          selected ? "scale-x-100" : "scale-x-0"
                        }`}
                      />
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="flex items-center gap-3">
              <ThemeButton />
              <Link href="/conectar" className="btn-primary">
                Conectar base
              </Link>
            </div>
          </div>
        </nav>
      </header>

      <div
        ref={stage}
        role="region"
        aria-roledescription="carrusel"
        aria-label="Presentación de QueryLens"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        className="relative overflow-hidden"
      >
        {SLIDES.map((slide, i) => (
          <Slide
            key={slide.id}
            index={i}
            current={index}
            total={SLIDES.length}
            label={slide.label}
            ready={ready}
            bleed={slide.bleed}
            className={slide.bg}
          >
            <slide.View active={i === index && ready} go={go} />
          </Slide>
        ))}
      </div>

      <footer className="z-20 bg-ground">
        <div className="mx-auto flex h-[72px] w-full max-w-[1320px] items-center justify-between gap-4 px-4 md:px-8">
          <p className="hidden min-w-0 flex-1 truncate text-[13px] text-muted md:block">
            {index < LAST ? (
              <>
                Siguiente: <span className="text-fg">{SLIDES[index + 1].label}</span>
              </>
            ) : (
              "Fin de la presentación"
            )}
          </p>

          <div className="flex flex-1 items-center justify-center gap-1.5 md:flex-none" role="group" aria-label="Diapositivas">
            {SLIDES.map((slide, i) => (
              <button
                key={slide.id}
                type="button"
                onClick={() => go(i)}
                aria-label={`Ir a ${slide.label}`}
                aria-current={i === index ? "true" : undefined}
                className="group flex h-10 w-9 items-center justify-center sm:w-11"
              >
                <span className="relative block h-1 w-full overflow-hidden rounded-full bg-raised">
                  <span
                    className="absolute inset-0 origin-left rounded-full transition-[transform,background-color] duration-[700ms] ease-[var(--ease-out)]"
                    style={{
                      transform: `scaleX(${i <= index ? 1 : 0})`,
                      background: i === index ? "var(--accent)" : "var(--neutral-mark)",
                    }}
                  />
                </span>
              </button>
            ))}
          </div>

          <div className="flex flex-1 justify-end gap-2">
            <ArrowButton label="Diapositiva anterior" disabled={index === 0} onClick={() => go(index - 1)} dir="prev" />
            <ArrowButton label="Diapositiva siguiente" disabled={index === LAST} onClick={() => go(index + 1)} dir="next" />
          </div>
        </div>
      </footer>

      <p className="sr-only" aria-live="polite">
        {`${SLIDES[index].label}, diapositiva ${index + 1} de ${SLIDES.length}`}
      </p>
    </div>
  );
}

function ArrowButton({
  label,
  disabled,
  onClick,
  dir,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  dir: "prev" | "next";
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-11 w-11 items-center justify-center rounded-full border border-control text-fg transition-[background-color,transform,color] duration-150 ease-out active:scale-[0.94] disabled:cursor-not-allowed disabled:border-line disabled:text-muted [@media(hover:hover)]:enabled:hover:bg-raised"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {dir === "prev" ? <path d="M19 12H5m6-6-6 6 6 6" /> : <path d="M5 12h14m-6-6 6 6-6 6" />}
      </svg>
    </button>
  );
}
