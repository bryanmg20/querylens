// Lista ordenada donde cada fila lleva su barra fina: la más larga en tinta, el resto en gris.
export default function BarList({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));

  return (
    <ul className="space-y-3.5">
      {rows.map((row, i) => (
        <li key={row.label}>
          <div className="flex items-baseline justify-between gap-3">
            <code className="min-w-0 truncate font-mono text-[13px] text-fg" title={row.label}>
              {row.label}
            </code>
            <span className="flex-none text-[13px] font-medium text-fg">{row.value}</span>
          </div>
          <div className="mt-1.5 h-[3px] w-full rounded-full bg-rule">
            <div
              className="grow-x h-full rounded-full"
              style={{
                width: `${(row.value / max) * 100}%`,
                background: i === 0 ? "var(--text)" : "var(--neutral-mark)",
                ["--i" as string]: i,
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
