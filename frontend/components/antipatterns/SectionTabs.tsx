"use client";

// Pestañas de las dos secciones de Diagnósticos (antipatrones y abrazos mortales). Elegir una
// muestra solo los patrones de esa sección y oculta los de la otra.
export default function SectionTabs({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { key: string; label: string; count?: number }[];
  value: string;
  onChange: (key: string) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-6">
      {tabs.map((tab) => {
        const selected = tab.key === value;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.key)}
            className={`relative h-9 text-[14px] transition-colors duration-150 ${selected ? "font-medium text-fg" : "text-muted hover:text-fg"}`}
          >
            {tab.label}
            {tab.count != null && <span className="ml-1.5 text-muted">{tab.count}</span>}
            <span
              aria-hidden="true"
              className={`absolute inset-x-0 bottom-0 h-px origin-left bg-accent transition-transform duration-200 ease-[var(--ease-out)] ${
                selected ? "scale-x-100" : "scale-x-0"
              }`}
            />
          </button>
        );
      })}
    </div>
  );
}
