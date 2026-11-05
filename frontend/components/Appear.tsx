"use client";

import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "@/lib/hooks";

// Aparición al entrar en pantalla: sube 14px y se vuelve opaco una sola vez. Lo que ya está a la
// vista al cargar nunca se oculta y, sin JavaScript, todo se ve.
export default function Appear({
  children,
  delay = 0,
  className = "",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState<"idle" | "armed" | "shown">("idle");

  useEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) return;
    if (el.getBoundingClientRect().top < window.innerHeight * 0.9) return;
    setPhase("armed");
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setPhase("shown");
          observer.disconnect();
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -5% 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`${className} ${
        phase === "idle"
          ? ""
          : `transition-[opacity,transform] duration-500 ease-[var(--ease-out)] ${
              phase === "armed" ? "translate-y-3.5 opacity-0" : "translate-y-0 opacity-100"
            }`
      }`}
      style={phase === "idle" ? undefined : { transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
}
