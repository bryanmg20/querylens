"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clearAccessToken, getAccessToken } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import { IconDatabase, IconLogout, IconMoon, IconPulse, IconSun, LensMark } from "./icons";

const ITEMS = [
  { href: "/diagnostics", label: "Diagnósticos", Icon: IconPulse },
  { href: "/conectar", label: "Conectar base", Icon: IconDatabase },
] as const;

// Riel de iconos en escritorio, barra inferior con etiquetas en teléfono.
// La selección es una línea de 1px en el acento, sobre el borde más cercano.
export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [hasSession, setHasSession] = useState(false);
  const [theme, toggleTheme] = useTheme();

  useEffect(() => {
    setHasSession(getAccessToken() != null);
  }, [pathname]);

  function handleDisconnect() {
    clearAccessToken();
    setHasSession(false);
    router.push("/conectar");
  }

  // La página de presentación va a pantalla completa, sin el riel de la aplicación.
  if (pathname === "/") return <>{children}</>;

  return (
    <div className="min-h-screen md:pl-[var(--rail)]">
      <nav
        aria-label="Principal"
        className="fixed inset-x-0 bottom-0 z-30 flex h-16 items-stretch border-t border-line bg-ground md:inset-y-0 md:right-auto md:h-auto md:w-[var(--rail)] md:flex-col md:items-center md:border-r md:border-t-0 md:py-4"
      >
        <Link
          href="/"
          aria-label="QueryLens"
          className="mb-4 hidden h-10 w-10 items-center justify-center rounded transition-transform duration-150 hover:scale-105 md:flex"
        >
          <LensMark />
        </Link>

        {ITEMS.map(({ href, label, Icon }) => {
          const active = pathname === href;
          return (
            <RailItem key={href} label={label} active={active} href={href}>
              <Icon />
            </RailItem>
          );
        })}

        <div className="contents md:mt-auto md:flex md:flex-col md:items-center md:gap-1">
          <RailItem label={theme === "light" ? "Modo oscuro" : "Modo claro"} onClick={toggleTheme}>
            {theme === "light" ? <IconMoon /> : theme === "dark" ? <IconSun /> : <span className="h-[18px] w-[18px]" />}
          </RailItem>
          {hasSession && (
            <RailItem label="Desconectar" onClick={handleDisconnect}>
              <IconLogout />
            </RailItem>
          )}
        </div>
      </nav>

      {children}
    </div>
  );
}

function RailItem({
  label,
  active = false,
  href,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  const className = `group relative flex flex-1 flex-col items-center justify-center gap-1 text-[11px] transition-colors duration-150 md:h-10 md:w-10 md:flex-none md:rounded md:hover:bg-raised ${
    active ? "text-fg" : "text-muted hover:text-fg"
  }`;

  const inner = (
    <>
      {children}
      <span className="md:sr-only">{label}</span>
      {active && (
        <span
          aria-hidden="true"
          className="absolute left-1/2 top-0 h-px w-6 -translate-x-1/2 bg-accent md:-right-2 md:left-auto md:top-1 md:h-8 md:w-px md:translate-x-0"
        />
      )}
      <span
        role="presentation"
        className="pointer-events-none absolute left-full z-40 ml-3 hidden whitespace-nowrap rounded bg-raised px-2.5 py-1.5 text-xs text-fg invisible opacity-0 shadow-[var(--shadow-float)] transition-[opacity,visibility] duration-150 group-hover:visible group-hover:opacity-100 group-focus-visible:visible group-focus-visible:opacity-100 md:block"
      >
        {label}
      </span>
    </>
  );

  if (href) {
    return (
      <Link href={href} aria-label={label} aria-current={active ? "page" : undefined} className={className}>
        {inner}
      </Link>
    );
  }
  return (
    <button type="button" aria-label={label} onClick={onClick} className={`${className} w-full`}>
      {inner}
    </button>
  );
}
