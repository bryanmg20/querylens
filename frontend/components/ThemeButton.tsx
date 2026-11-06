"use client";

import { useTheme } from "@/lib/theme";
import { IconMoon, IconSun } from "./icons";

// Interruptor de tema para barras sin riel (la presentación): un botón redondo cuyo icono gira
// y se desvanece al cambiar.
export default function ThemeButton() {
  const [theme, toggle] = useTheme();
  const label = theme === "light" ? "Cambiar a modo oscuro" : "Cambiar a modo claro";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className="relative flex h-10 w-10 items-center justify-center rounded-full border border-control text-fg transition-[background-color,transform] duration-150 ease-out active:scale-[0.94] [@media(hover:hover)]:hover:bg-raised"
    >
      <span
        className="absolute transition-[opacity,transform] duration-300 ease-[var(--ease-out)]"
        style={{ opacity: theme === "light" ? 1 : 0, transform: theme === "light" ? "none" : "rotate(-60deg) scale(0.8)" }}
      >
        <IconSun />
      </span>
      <span
        className="absolute transition-[opacity,transform] duration-300 ease-[var(--ease-out)]"
        style={{ opacity: theme === "dark" ? 1 : 0, transform: theme === "dark" ? "none" : "rotate(60deg) scale(0.8)" }}
      >
        <IconMoon />
      </span>
    </button>
  );
}
