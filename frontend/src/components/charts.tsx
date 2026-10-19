import { Table2, BarChart3 } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/* Charts follow one spec: thin marks, 2px surface gaps/rings, hairline recessive grid, a legend for
   two or more series, a hover + keyboard tooltip, and a table view as the accessible twin. */

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    setWidth(ref.current.clientWidth);
    const observer = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const scaled = value / power;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * power;
}

function integerTicks(max: number): number[] {
  const top = niceMax(Math.max(1, max));
  const step = Math.max(1, Math.round(top / 4));
  const ticks: number[] = [];
  for (let t = 0; t <= top; t += step) ticks.push(t);
  return ticks;
}

export interface Series {
  key: string;
  label: string;
  color: string; // a CSS colour token, e.g. var(--series-1)
  icon?: ReactNode;
}

function Legend({ series, shape }: { series: Series[]; shape: "rect" | "line" }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          {shape === "rect" ? (
            <span className="inline-block size-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
          ) : (
            <span className="inline-block h-0.5 w-3.5 rounded" style={{ background: s.color }} aria-hidden />
          )}
          {s.icon}
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function ViewToggle({ table, onToggle }: { table: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-ink-2 hover:bg-surface-2"
      aria-pressed={table}
    >
      {table ? <BarChart3 className="size-3.5" aria-hidden /> : <Table2 className="size-3.5" aria-hidden />}
      {table ? "Chart" : "Table"}
    </button>
  );
}

function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x + 12, 4), Math.max(4, width - 180));
  return (
    <div
      className="pointer-events-none absolute z-10 w-44 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg"
      style={{ left, top: Math.max(0, y) }}
      role="tooltip"
    >
      {children}
    </div>
  );
}

function TooltipRow({ color, value, label }: { color: string; value: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block h-0.5 w-3 shrink-0 rounded" style={{ background: color }} aria-hidden />
      <span className="font-semibold text-ink tabular-nums">{value}</span>
      <span className="truncate text-muted">{label}</span>
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  const radius = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + radius} Q${x},${y} ${x + radius},${y} H${x + w - radius} Q${x + w},${y} ${x + w},${y + radius} V${y + h} Z`;
}

/* ------------------------------------------------------------------------------------------- */

export interface ColumnDatum {
  key: string;
  label: string;
  values: Record<string, number>;
}

/** Stacked columns (part-to-whole per category), e.g. ratings per day. */
export function StackedColumns({ data, series, height = 220, caption }: {
  data: ColumnDatum[];
  series: Series[];
  height?: number;
  caption: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const margin = { top: 12, right: 8, bottom: 26, left: 32 };
  const plotW = Math.max(0, width - margin.left - margin.right);
  const plotH = height - margin.top - margin.bottom;
  const totals = data.map((d) => series.reduce((sum, s) => sum + (d.values[s.key] ?? 0), 0));
  const ticks = integerTicks(Math.max(0, ...totals));
  const yMax = ticks[ticks.length - 1] || 1;
  const band = data.length ? plotW / data.length : 0;
  const barW = Math.max(4, Math.min(24, band * 0.6));
  const y = (v: number) => margin.top + plotH - (v / yMax) * plotH;
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(plotW / 64))));

  return (
    <figure className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Legend series={series} shape="rect" />
        <ViewToggle table={table} onToggle={() => setTable((t) => !t)} />
      </div>
      {table ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-1.5 pr-3 font-medium">Date</th>
                {series.map((s) => (
                  <th key={s.key} className="py-1.5 pr-3 text-right font-medium">{s.label}</th>
                ))}
                <th className="py-1.5 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d, i) => (
                <tr key={d.key} className="border-b border-line last:border-0">
                  <td className="py-1.5 pr-3">{d.label}</td>
                  {series.map((s) => (
                    <td key={s.key} className="py-1.5 pr-3 text-right tabular-nums">{d.values[s.key] ?? 0}</td>
                  ))}
                  <td className="py-1.5 text-right tabular-nums">{totals[i]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={ref} className="relative" style={{ height }}>
          {width > 0 && (
            <svg width={width} height={height} role="img" aria-label={caption}>
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={margin.left} x2={width - margin.right} y1={y(t)} y2={y(t)}
                    stroke={t === 0 ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth={1} />
                  <text x={margin.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11}
                    fill="var(--chart-label)" className="tabular-nums">{t}</text>
                </g>
              ))}
              {data.map((d, i) => {
                const cx = margin.left + band * i + band / 2;
                let cumulative = 0;
                const visible = series.filter((s) => (d.values[s.key] ?? 0) > 0);
                return (
                  <g key={d.key}
                    tabIndex={0}
                    role="button"
                    aria-label={`${d.label}: ${series.map((s) => `${s.label} ${d.values[s.key] ?? 0}`).join(", ")}`}
                    onPointerEnter={() => setActive(i)}
                    onPointerLeave={() => setActive(null)}
                    onFocus={() => setActive(i)}
                    onBlur={() => setActive(null)}
                    className="outline-none"
                  >
                    <rect x={margin.left + band * i} y={margin.top} width={band} height={plotH} fill="transparent" />
                    {visible.map((s, index) => {
                      const value = d.values[s.key] ?? 0;
                      const top = y(cumulative + value);
                      const bottom = y(cumulative);
                      cumulative += value;
                      const gap = index > 0 ? 2 : 0; // 2px surface gap between stacked segments
                      const h = Math.max(1, bottom - top - gap);
                      const isTop = index === visible.length - 1;
                      return isTop ? (
                        <path key={s.key} d={roundedTop(cx - barW / 2, top, barW, h, 4)} fill={s.color}
                          opacity={active === null || active === i ? 1 : 0.55} />
                      ) : (
                        <rect key={s.key} x={cx - barW / 2} y={top} width={barW} height={h} fill={s.color}
                          opacity={active === null || active === i ? 1 : 0.55} />
                      );
                    })}
                    {i % labelEvery === 0 && (
                      <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--chart-label)">{d.label}</text>
                    )}
                  </g>
                );
              })}
            </svg>
          )}
          {active !== null && data[active] && (
            <Tooltip x={margin.left + band * active + band / 2} y={margin.top} width={width}>
              <div className="mb-1 font-medium text-ink-2">{data[active].label}</div>
              {series.map((s) => (
                <TooltipRow key={s.key} color={s.color} value={String(data[active].values[s.key] ?? 0)} label={s.label} />
              ))}
            </Tooltip>
          )}
        </div>
      )}
      <figcaption className="sr-only">{caption}</figcaption>
    </figure>
  );
}

/* ------------------------------------------------------------------------------------------- */

export interface LinePoint {
  key: string;
  label: string;
  values: Record<string, number | null>;
}

/** Multi-series line chart on one 0..1 axis, with crosshair tooltip and direct end labels. */
export function LineChart({ points, series, height = 240, caption, format }: {
  points: LinePoint[];
  series: Series[];
  height?: number;
  caption: string;
  format: (value: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const margin = { top: 14, right: 116, bottom: 26, left: 40 };
  const plotW = Math.max(0, width - margin.left - margin.right);
  const plotH = height - margin.top - margin.bottom;
  const n = points.length;
  const x = (i: number) => margin.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => margin.top + plotH - v * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 90))));

  useEffect(() => {
    if (active !== null && active >= n) setActive(null);
  }, [active, n]);

  // Direct end labels only when they do not collide; otherwise legend + tooltip + table carry identity.
  const last = points[n - 1];
  const ends = series
    .map((s) => ({ s, v: last?.values[s.key] }))
    .filter((e): e is { s: Series; v: number } => typeof e.v === "number")
    .sort((a, b) => b.v - a.v);
  const collide = ends.some((e, i) => i > 0 && Math.abs(y(ends[i - 1].v) - y(e.v)) < 13);

  const onMove = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = (event.currentTarget as SVGRectElement).getBoundingClientRect();
    const px = event.clientX - bounds.left;
    const index = n <= 1 ? 0 : Math.round((px / bounds.width) * (n - 1));
    setActive(Math.max(0, Math.min(n - 1, index)));
  };
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowRight") setActive((a) => Math.min(n - 1, (a ?? -1) + 1));
    if (event.key === "ArrowLeft") setActive((a) => Math.max(0, (a ?? n) - 1));
  };

  return (
    <figure className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Legend series={series} shape="line" />
        <ViewToggle table={table} onToggle={() => setTable((t) => !t)} />
      </div>
      {table ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-1.5 pr-3 font-medium">Run</th>
                {series.map((s) => (
                  <th key={s.key} className="py-1.5 pr-3 text-right font-medium">{s.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.key} className="border-b border-line last:border-0">
                  <td className="py-1.5 pr-3">{p.label}</td>
                  {series.map((s) => (
                    <td key={s.key} className="py-1.5 pr-3 text-right tabular-nums">
                      {typeof p.values[s.key] === "number" ? format(p.values[s.key] as number) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={ref} className="relative" style={{ height }}>
          {width > 0 && (
            <svg width={width} height={height} role="img" aria-label={caption}>
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={margin.left} x2={margin.left + plotW} y1={y(t)} y2={y(t)}
                    stroke={t === 0 ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth={1} />
                  <text x={margin.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11}
                    fill="var(--chart-label)" className="tabular-nums">{format(t)}</text>
                </g>
              ))}
              {points.map((p, i) =>
                i % labelEvery === 0 || i === n - 1 ? (
                  <text key={p.key} x={x(i)} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--chart-label)">
                    {p.label}
                  </text>
                ) : null,
              )}
              {active !== null && (
                <line x1={x(active)} x2={x(active)} y1={margin.top} y2={margin.top + plotH} stroke="var(--chart-axis)" strokeWidth={1} />
              )}
              {series.map((s) => {
                // Break the line where a run has no value instead of drawing through the gap.
                const segments: number[][][] = [[]];
                points.forEach((p, i) => {
                  const v = p.values[s.key];
                  if (typeof v === "number") segments[segments.length - 1].push([x(i), y(v)]);
                  else if (segments[segments.length - 1].length) segments.push([]);
                });
                return (
                  <g key={s.key}>
                    {segments.filter((seg) => seg.length > 1).map((seg, index) => (
                      <polyline key={index} points={seg.map((c) => c.join(",")).join(" ")} fill="none" stroke={s.color}
                        strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                    ))}
                    {points.map((p, i) => {
                      const v = p.values[s.key];
                      if (typeof v !== "number") return null;
                      // A point with no neighbour on either side draws no line, so it always gets a marker.
                      const isolated = typeof points[i - 1]?.values[s.key] !== "number" &&
                        typeof points[i + 1]?.values[s.key] !== "number";
                      const emphasised = active === i || i === n - 1 || isolated;
                      return emphasised ? (
                        <circle key={p.key} cx={x(i)} cy={y(v)} r={4} fill={s.color} stroke="var(--chart-surface)" strokeWidth={2} />
                      ) : null;
                    })}
                  </g>
                );
              })}
              {!collide &&
                ends.map(({ s, v }) => (
                  <text key={s.key} x={x(n - 1) + 10} y={y(v)} dy="0.32em" fontSize={11} fill="var(--rg-ink-2)">
                    <tspan fontWeight={600} fill="var(--rg-ink)">{format(v)}</tspan> {s.label}
                  </text>
                ))}
              <rect x={margin.left} y={margin.top} width={Math.max(1, plotW)} height={plotH} fill="transparent"
                tabIndex={0} aria-label={`${caption}. Use left and right arrow keys to read each run.`}
                onPointerMove={onMove} onPointerLeave={() => setActive(null)} onKeyDown={onKey}
                onFocus={() => setActive((a) => a ?? n - 1)} onBlur={() => setActive(null)} className="outline-none" />
            </svg>
          )}
          {active !== null && points[active] && (
            <Tooltip x={x(active)} y={margin.top} width={width}>
              <div className="mb-1 font-medium text-ink-2">{points[active].label}</div>
              {series.map((s) => {
                const v = points[active].values[s.key];
                return <TooltipRow key={s.key} color={s.color} value={typeof v === "number" ? format(v) : "—"} label={s.label} />;
              })}
            </Tooltip>
          )}
        </div>
      )}
      <figcaption className="sr-only">{caption}</figcaption>
    </figure>
  );
}

/** Horizontal single-hue bars for nominal categories (one series, so no legend). */
export function HBars({ items, format }: { items: { label: string; value: number; hint?: string }[]; format: (v: number) => string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.label} className="grid grid-cols-[minmax(0,8rem)_1fr_auto] items-center gap-3 text-sm" title={item.hint}>
          <span className="truncate text-ink-2">{item.label}</span>
          <span className="h-3 overflow-hidden">
            <span className="block h-full rounded-r-[4px]" style={{ width: `${(item.value / max) * 100}%`, background: "var(--series-1)", minWidth: 2 }} />
          </span>
          <span className="text-right text-xs text-muted tabular-nums">{format(item.value)}</span>
        </li>
      ))}
    </ul>
  );
}
