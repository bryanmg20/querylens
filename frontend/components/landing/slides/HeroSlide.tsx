"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import type { SlideProps } from "../HomeCarousel";
import HeroDemo from "../HeroDemo";

const MAX_TILT = 3;

// Titular a dos líneas que sube desde una máscara, la acción principal y el fragmento
// vivo del producto, que se inclina un poco hacia el cursor con inercia (decoración).
export default function HeroSlide({ active, go }: SlideProps) {
  const stage = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = stage.current;
    const target = card.current;
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!el || !target || !fine || calm || !active) return;

    let goalX = 0;
    let goalY = 0;
    let x = 0;
    let y = 0;
    let frame = 0;

    const tick = () => {
      // Interpolación con inercia: el movimiento sigue al cursor sin saltar a él.
      x += (goalX - x) * 0.08;
      y += (goalY - y) * 0.08;
      target.style.transform = `perspective(1400px) rotateX(${(-y * MAX_TILT).toFixed(3)}deg) rotateY(${(x * MAX_TILT).toFixed(3)}deg)`;
      frame = Math.abs(goalX - x) > 0.001 || Math.abs(goalY - y) > 0.001 ? requestAnimationFrame(tick) : 0;
    };
    const kick = () => {
      if (!frame) frame = requestAnimationFrame(tick);
    };
    const onMove = (event: PointerEvent) => {
      const rect = el.getBoundingClientRect();
      goalX = ((event.clientX - rect.left) / rect.width - 0.5) * 2;
      goalY = ((event.clientY - rect.top) / rect.height - 0.5) * 2;
      kick();
    };
    const onLeave = () => {
      goalX = 0;
      goalY = 0;
      kick();
    };

    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      cancelAnimationFrame(frame);
    };
  }, [active]);

  return (
    <div ref={stage} className="grid w-full flex-1 content-center items-center gap-10 lg:grid-cols-12">
      <div className="min-w-0 lg:col-span-7">
        <h1 className="text-[clamp(2rem,min(5vw,8.5vh),4.5rem)] font-semibold leading-[1.02] tracking-[-0.035em]">
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 0 }}>
              Encuentra qué frena
            </span>
          </span>
          <span className="sl-mask block">
            <span className="sl-line" style={{ ["--i" as string]: 1 }}>
              tu base de datos
            </span>
          </span>
        </h1>

        <p className="sl mt-7 max-w-[34rem] text-[18px] leading-[1.55]" style={{ ["--i" as string]: 3 }}>
          QueryLens se conecta a tu PostgreSQL o MySQL, detecta antipatrones en las consultas y te da la evidencia y la
          recomendación para corregirlos.
        </p>

        <div className="sl mt-9 flex flex-wrap items-center gap-x-8 gap-y-4" style={{ ["--i" as string]: 4 }}>
          <Link
            href="/conectar"
            className="btn h-12 bg-accent px-7 text-[16px] font-semibold text-[var(--on-accent)] hover:brightness-110"
          >
            Conectar una base de datos
          </Link>
          <button
            type="button"
            onClick={() => go(1)}
            className="text-[16px] text-muted transition-colors duration-150 hover:text-fg"
          >
            Ver cómo funciona
          </button>
        </div>
      </div>

      <div className="sl min-w-0 lg:col-span-5" style={{ ["--i" as string]: 5 }}>
        <div ref={card} className="will-change-transform">
          <HeroDemo running={active} />
        </div>
      </div>
    </div>
  );
}
