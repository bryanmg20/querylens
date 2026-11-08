"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { EvidenceGraph, GEdge, GNode, NodeKind, Tone } from "@/lib/evidenceGraph";
import { prefersReducedMotion, useElementWidth } from "@/lib/hooks";

const TONE: Record<Tone, string> = {
  neutral: "var(--neutral-mark)",
  neg: "var(--negative)",
  pos: "var(--positive)",
  warn: "var(--warning)",
  accent: "var(--accent)",
};

const KIND_LABEL: Record<NodeKind, string> = {
  query: "Consulta",
  table: "Tabla",
  column: "Columna",
  index: "Índice",
  predicate: "Filtro",
  metric: "Medida",
  session: "Sesión",
};
const IDENT: NodeKind[] = ["query", "table", "column", "index", "predicate", "session"];

const NODE_H = 64;
const NODE_H_SERIES = 92;
const GAP_Y = 14;
const ROW_GAP = 56;
const VERTICAL_BELOW = 640;
const BACK_DROP = 56;
const BACK_SIDE = 36;

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const nodeHeight = (n: GNode) => (n.series && n.series.length > 1 ? NODE_H_SERIES : NODE_H);

// Coloca los nodos por capas: de izquierda a derecha en pantallas anchas y de arriba abajo en
// las estrechas. Devuelve la caja de cada nodo y el alto total.
function layout(nodes: GNode[], edges: GEdge[], fullWidth: number) {
  // Una flecha que vuelve a una capa anterior (el cierre de un ciclo) necesita espacio propio.
  const layerOf = new Map(nodes.map((n) => [n.id, n.layer]));
  const hasBack = edges.some((e) => (layerOf.get(e.to) ?? 0) <= (layerOf.get(e.from) ?? 0));
  const width = fullWidth;
  const layers = [...new Set(nodes.map((n) => n.layer))].sort((a, b) => a - b);
  const rows = layers.map((l) => nodes.filter((n) => n.layer === l));
  // Con muchas capas (una cola larga de bloqueos) las columnas no caben: se apilan.
  const vertical = width < VERTICAL_BELOW || width < rows.length * 140 + (rows.length - 1) * 64;
  const boxes = new Map<string, Box>();
  let height = 0;
  let gapX = 0;

  if (!vertical) {
    // El hueco entre columnas es donde van las etiquetas de las relaciones: se reserva antes que el ancho de los nodos.
    const nodeW = Math.max(140, Math.min(184, (width - (rows.length - 1) * 84) / rows.length));
    gapX = rows.length > 1 ? (width - rows.length * nodeW) / (rows.length - 1) : 0;
    const colH = rows.map((r) => r.reduce((s, n) => s + nodeHeight(n), 0) + (r.length - 1) * GAP_Y);
    height = Math.max(...colH);
    const blockH = height;
    if (hasBack) height += BACK_DROP + 12;
    rows.forEach((col, ci) => {
      let y = (blockH - colH[ci]) / 2;
      for (const n of col) {
        const h = nodeHeight(n);
        boxes.set(n.id, { x: ci * (nodeW + gapX), y, w: nodeW, h });
        y += h + GAP_Y;
      }
    });
  } else {
    const perRow = width < 480 ? 2 : 3;
    const usable = hasBack ? width - BACK_SIDE - 4 : width;
    const nodeW = Math.min(220, (usable - (perRow - 1) * 12) / perRow);
    const lines = rows.flatMap((r) => {
      const out: GNode[][] = [];
      for (let i = 0; i < r.length; i += perRow) out.push(r.slice(i, i + perRow));
      return out;
    });
    let y = 0;
    for (const row of lines) {
      const rowW = row.length * nodeW + (row.length - 1) * 12;
      const rowH = Math.max(...row.map(nodeHeight));
      row.forEach((n, i) => boxes.set(n.id, { x: (usable - rowW) / 2 + i * (nodeW + 12), y, w: nodeW, h: nodeHeight(n) }));
      y += rowH + ROW_GAP;
    }
    height = y - ROW_GAP;
  }
  return { boxes, height, vertical, layers, gapX };
}

// Punto de una curva cúbica en t: la etiqueta va a la mitad, o cerca del destino cuando la arista
// salta una capa (así no queda escondida detrás del nodo intermedio).
function bezier(p: [number, number][], t: number): [number, number] {
  const u = 1 - t;
  return [
    u * u * u * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t * t * t * p[3][0],
    u * u * u * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t * t * t * p[3][1],
  ];
}

function edgeGeometry(a: Box, b: Box, vertical: boolean, t = 0.5) {
  let pts: [number, number][];
  if (!vertical && b.x <= a.x) {
    // Flecha que vuelve hacia atrás (cierra un ciclo): rodea los nodos por debajo.
    const x1 = a.x + a.w / 2;
    const y1 = a.y + a.h;
    const x2 = b.x + b.w / 2;
    const y2 = b.y + b.h;
    pts = [[x1, y1], [x1, y1 + BACK_DROP], [x2, y2 + BACK_DROP], [x2, y2]];
  } else if (vertical && b.y <= a.y) {
    // En vertical rodea los nodos por la derecha.
    const x1 = a.x + a.w;
    const y1 = a.y + a.h / 2;
    const x2 = b.x + b.w;
    const y2 = b.y + b.h / 2;
    pts = [[x1, y1], [x1 + BACK_SIDE, y1], [x2 + BACK_SIDE, y2], [x2, y2]];
  } else if (!vertical) {
    const x1 = a.x + a.w;
    const y1 = a.y + a.h / 2;
    const x2 = b.x;
    const y2 = b.y + b.h / 2;
    const dx = Math.max(28, (x2 - x1) / 2);
    pts = [[x1, y1], [x1 + dx, y1], [x2 - dx, y2], [x2, y2]];
  } else {
    const x1 = a.x + a.w / 2;
    const y1 = a.y + a.h;
    const x2 = b.x + b.w / 2;
    const y2 = b.y;
    const dy = Math.max(24, (y2 - y1) / 2);
    pts = [[x1, y1], [x1, y1 + dy], [x2, y2 - dy], [x2, y2]];
  }
  const [mx, my] = bezier(pts, t);
  return { d: `M${pts[0][0]},${pts[0][1]} C${pts[1][0]},${pts[1][1]} ${pts[2][0]},${pts[2][1]} ${pts[3][0]},${pts[3][1]}`, mx, my };
}

// El JSON de evidencia de un hallazgo convertido en un mapa: qué consulta toca qué, por qué
// columna filtra y qué índice existe o falta. Las relaciones se dibujan al aparecer; al pasar el
// cursor (o enfocar) por un nodo se resaltan sus conexiones.
export default function EvidenceGraphView({ graph }: { graph: EvidenceGraph }) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const uid = useId().replace(/:/g, "");
  const [shown, setShown] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const seen = useRef(false);
  const calm = typeof window !== "undefined" && prefersReducedMotion();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (calm) {
      setShown(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !seen.current) {
          seen.current = true;
          setShown(true);
          observer.disconnect();
        }
      },
      { threshold: 0.15 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, calm]);

  function replay() {
    setShown(false);
    requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)));
  }

  const placed = useMemo(() => (width > 0 ? layout(graph.nodes, graph.edges, width) : null), [graph.nodes, graph.edges, width]);
  const connected = useMemo(() => {
    if (!hover) return null;
    const set = new Set<string>([hover]);
    for (const e of graph.edges) {
      if (e.from === hover) set.add(e.to);
      if (e.to === hover) set.add(e.from);
    }
    return set;
  }, [hover, graph.edges]);

  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const layerIndex = (n: GNode) => (placed ? placed.layers.indexOf(n.layer) : 0);
  const step = calm ? 0 : 140;

  return (
    <div>
      <div ref={ref} className="relative w-full" style={{ height: placed ? placed.height : 220 }} role="group" aria-label="Grafo de la evidencia">
        {placed && (
          <>
            <svg className="pointer-events-none absolute inset-0" width={width} height={placed.height} aria-hidden="true">
              <defs>
                {(Object.keys(TONE) as Tone[]).map((tone) => (
                  <marker key={tone} id={`${uid}-${tone}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M1 1.5 9 5 1 8.5" fill="none" stroke={TONE[tone]} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </marker>
                ))}
              </defs>
              {graph.edges.map((edge, i) => {
                const a = placed.boxes.get(edge.from);
                const b = placed.boxes.get(edge.to);
                const from = byId.get(edge.from);
                if (!a || !b || !from) return null;
                const { d } = edgeGeometry(a, b, placed.vertical);
                const dim = connected && !(connected.has(edge.from) && connected.has(edge.to));
                const delay = layerIndex(from) * step + 260;
                return (
                  <path
                    key={`${edge.from}-${edge.to}-${i}`}
                    d={d}
                    fill="none"
                    stroke={TONE[edge.tone]}
                    strokeWidth={1.6}
                    strokeLinecap="round"
                    markerEnd={`url(#${uid}-${edge.tone})`}
                    pathLength={edge.dashed ? undefined : 1}
                    strokeDasharray={edge.dashed ? "5 4" : 1}
                    strokeDashoffset={edge.dashed ? 0 : shown ? 0 : 1}
                    style={{
                      opacity: shown ? (dim ? 0.2 : 1) : 0,
                      transition: calm
                        ? "none"
                        : `stroke-dashoffset 700ms var(--ease-out) ${delay}ms, opacity ${shown ? 400 : 150}ms ease ${shown && !dim ? delay : 0}ms`,
                    }}
                  />
                );
              })}
            </svg>

            {graph.edges.map((edge, i) => {
              if (!edge.label) return null;
              const a = placed.boxes.get(edge.from);
              const b = placed.boxes.get(edge.to);
              const from = byId.get(edge.from);
              if (!a || !b || !from) return null;
              const to = byId.get(edge.to);
              const skips = to ? layerIndex(to) - layerIndex(from) > 1 : false;
              const { mx, my } = edgeGeometry(a, b, placed.vertical, skips ? 0.82 : 0.5);
              const dim = connected && !(connected.has(edge.from) && connected.has(edge.to));
              return (
                <span
                  key={`l-${i}`}
                  className={`pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded bg-surface px-1.5 text-[12px] leading-4 ${placed.vertical ? "whitespace-nowrap" : "text-center"}`}
                  style={{
                    left: mx,
                    top: my,
                    maxWidth: placed.vertical ? undefined : Math.max(56, placed.gapX - 8),
                    color: edge.tone === "neutral" ? "var(--muted)" : TONE[edge.tone],
                    opacity: shown ? (dim ? 0.2 : 1) : 0,
                    transition: calm ? "none" : `opacity 400ms ease ${shown ? layerIndex(from) * step + 520 : 0}ms`,
                  }}
                  title={edge.label}
                >
                  {placed.vertical && edge.label.length > 22 ? `${edge.label.slice(0, 21)}…` : edge.label}
                </span>
              );
            })}

            {graph.nodes.map((node) => {
              const box = placed.boxes.get(node.id)!;
              const dim = connected && !connected.has(node.id);
              return (
                <NodeCard
                  key={node.id}
                  node={node}
                  box={box}
                  shown={shown}
                  dim={!!dim}
                  delay={layerIndex(node) * step}
                  calm={calm}
                  onHover={setHover}
                />
              );
            })}
          </>
        )}
      </div>

      <ul className="sr-only">
        {graph.edges.map((e, i) => (
          <li key={i}>
            {byId.get(e.from)?.title} → {byId.get(e.to)?.title}
            {e.label ? `: ${e.label}` : ""}
          </li>
        ))}
      </ul>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted">
        <p>Cada nodo sale del JSON de evidencia de este hallazgo. Las líneas punteadas son lo que falta o no se usa.</p>
        <button type="button" onClick={replay} className="btn-text h-8">
          Repetir
        </button>
      </div>
    </div>
  );
}

function NodeCard({
  node,
  box,
  shown,
  dim,
  delay,
  calm,
  onHover,
}: {
  node: GNode;
  box: Box;
  shown: boolean;
  dim: boolean;
  delay: number;
  calm: boolean;
  onHover: (id: string | null) => void;
}) {
  const color = TONE[node.tone];
  const ident = IDENT.includes(node.kind);
  const series = node.series && node.series.length > 1 ? node.series : null;

  return (
    <div
      tabIndex={0}
      onPointerEnter={() => onHover(node.id)}
      onPointerLeave={() => onHover(null)}
      onFocus={() => onHover(node.id)}
      onBlur={() => onHover(null)}
      className="absolute flex min-w-0 flex-col justify-center rounded bg-ground px-3 py-2 outline-offset-2"
      style={{
        left: box.x,
        top: box.y,
        width: box.w,
        height: box.h,
        border: node.dashed ? `1px dashed ${color}` : "1px solid var(--control)",
        borderLeft: node.dashed ? `1px dashed ${color}` : `3px solid ${color}`,
        opacity: shown ? (dim ? 0.3 : 1) : 0,
        transform: shown ? "none" : "translate3d(0, 8px, 0) scale(0.97)",
        transition: calm
          ? "none"
          : `opacity ${shown ? 450 : 150}ms var(--ease-out) ${shown ? delay : 0}ms, transform ${shown ? 500 : 150}ms var(--ease-out) ${shown ? delay : 0}ms`,
      }}
    >
      <p className="text-[12px] leading-4 text-muted">{KIND_LABEL[node.kind]}</p>
      {ident ? (
        <code className="truncate font-mono text-[13px] font-medium leading-5" title={node.title}>
          {node.title}
        </code>
      ) : (
        <p className="truncate text-[13px] font-semibold leading-5" style={{ color: node.tone === "neg" ? "var(--pill-neg-ink)" : undefined }} title={node.title}>
          {node.title}
        </p>
      )}
      {node.sub && (
        <p className="truncate text-[12px] leading-4 text-muted" title={node.sub}>
          {node.sub}
        </p>
      )}
      {series && <Sparkline values={series} median={node.median} width={box.w - 24} />}
    </div>
  );
}

// Línea pequeña de las ventanas previas, con su mediana en trazo discontinuo.
function Sparkline({ values, median, width }: { values: number[]; median?: number; width: number }) {
  const H = 24;
  const max = Math.max(...values, median ?? 0) * 1.1 || 1;
  const x = (i: number) => (i / (values.length - 1)) * Math.max(1, width);
  const y = (v: number) => H - (v / max) * H;
  return (
    <svg width={width} height={H} className="mt-1 block" aria-hidden="true">
      {median != null && <line x1={0} x2={width} y1={y(median)} y2={y(median)} stroke="var(--muted)" strokeDasharray="3 3" />}
      <polyline points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
