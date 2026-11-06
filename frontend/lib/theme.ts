"use client";

import { useCallback, useEffect, useState } from "react";
import { prefersReducedMotion } from "./hooks";

export type Theme = "light" | "dark";

const KEY = "querylens_theme";
const EVENT = "querylens-theme";

// Se ejecuta en <head> antes de pintar, para que la página no parpadee en el tema equivocado:
// usa la elección guardada o, si no hay, la preferencia del sistema.
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("${KEY}");if(t!=="light"&&t!=="dark"){t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="dark"}})();`;

function current(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

// El tema activo y una función para alternarlo. Todas las instancias del hook se mantienen
// sincronizadas; antes de montar devuelve null para no pintar un icono equivocado.
export function useTheme(): [Theme | null, () => void] {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(current());
    const sync = () => setTheme(current());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);

  const toggle = useCallback(() => {
    const next: Theme = current() === "dark" ? "light" : "dark";
    const root = document.documentElement;
    if (!prefersReducedMotion()) {
      root.classList.add("theme-anim");
      window.setTimeout(() => root.classList.remove("theme-anim"), 320);
    }
    root.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // sin localStorage el tema vale solo para esta visita
    }
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return [theme, toggle];
}
