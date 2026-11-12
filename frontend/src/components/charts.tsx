// Small hand-built SVG charts in the house style. Colors are a validated
// categorical palette (light and dark steps), defined in styles/index.css.
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type Series = { key: string; label: string; color: string };

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function niceMax(v: number): number {
  if (v <= 4) return 4;
  if (v <= 40) return Math.ceil(v / 4) * 4; // whole numbers on all four grid lines
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

const fmt = (n: number) => (n >= 10000 ? `${Math.round(n / 1000)}K` : n.toLocaleString());

export function Legend({ series }: { series: Series[] }) {
  return (
    <div className="legend" role="list">
      {series.map((s) => (
        <span key={s.key} role="listitem">
          <i style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/** Stacked columns over time; one hover/focus target per column. */
export function StackedColumns<T extends Record<string, unknown>>({
  data, series, xLabel, xTick, height = 220, label,
}: {
  data: T[];
  series: Series[];
  xLabel: (d: T) => string; // full label for tooltip and table
  xTick: (d: T, i: number) => string | null; // short axis label or null to skip
  height?: number;
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 40, r: 8, t: 8, b: 24 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const totals = data.map((d) => series.reduce((s, x) => s + Number(d[x.key] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const ticks = [0, max / 4, max / 2, (3 * max) / 4, max];
  const slot = iw / Math.max(1, data.length);
  const bw = Math.min(24, Math.max(3, slot - 4));
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  const GAP = 2;

  const tip = hover !== null ? data[hover] : null;
  const tipLeft = hover !== null ? Math.min(Math.max(pad.l + slot * hover + slot / 2, 90), width - 90) : 0;

  return (
    <div ref={ref} className="chart-wrap">
      <svg width={width} height={height} role="img" aria-label={label} className="chart" style={{ height }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = pad.l + slot * i + (slot - bw) / 2;
          let acc = 0;
          const segs = series
            .map((s) => ({ s, v: Number(d[s.key] ?? 0) }))
            .filter((x) => x.v > 0);
          return (
            <g key={i} opacity={hover === null || hover === i ? 1 : 0.55}>
              {segs.map(({ s, v }, j) => {
                const y0 = y(acc);
                acc += v;
                const y1 = y(acc);
                const top = j === segs.length - 1;
                const h = Math.max(0, y0 - y1 - (top ? 0 : GAP));
                if (h <= 0) return null;
                const r = top ? Math.min(4, h, bw / 2) : 0;
                const yy = y1 + (top ? 0 : GAP);
                // Rounded data-end, square at the baseline.
                const path = `M${x},${yy + h} V${yy + r} Q${x},${yy} ${x + r},${yy} H${x + bw - r} Q${x + bw},${yy} ${x + bw},${yy + r} V${yy + h} Z`;
                return <path key={s.key} d={path} fill={s.color} />;
              })}
              {xTick(d, i) && (
                <text x={pad.l + slot * i + slot / 2} y={height - 6} textAnchor="middle">{xTick(d, i)}</text>
              )}
              <rect
                x={pad.l + slot * i} y={pad.t} width={slot} height={ih} fill="transparent"
                tabIndex={0} aria-label={`${xLabel(d)}: ${totals[i]}`}
                onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                style={{ outline: "none" }}
              />
            </g>
          );
        })}
        <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} className="axis" />
      </svg>
      {tip && (
        <div className="chart-tip" style={{ left: tipLeft }} role="status">
          <div className="tip-head">{xLabel(tip)}</div>
          {series.map((s) => (
            <div key={s.key} className="tip-row">
              <i style={{ background: s.color }} />
              <b className="num">{Number(tip[s.key] ?? 0).toLocaleString()}</b>
              <span>{s.label}</span>
            </div>
          ))}
          <div className="tip-row total">
            <b className="num">{totals[hover!].toLocaleString()}</b>
          </div>
        </div>
      )}
    </div>
  );
}

/** Ranked horizontal bars, one series. */
export function BarList({ items, color, empty, render }: {
  items: { name: string; count: number }[];
  color: string;
  empty: ReactNode;
  render?: (name: string) => ReactNode;
}) {
  if (!items.length) return <div className="empty">{empty}</div>;
  const max = Math.max(...items.map((i) => i.count));
  return (
    <div className="barlist">
      {items.map((it) => (
        <div key={it.name} className="barlist-row" title={`${it.name}: ${it.count}`}>
          <div className="barlist-label">
            <span className="barlist-name">{render ? render(it.name) : it.name}</span>
            <b className="num">{it.count.toLocaleString()}</b>
          </div>
          <div className="barlist-track">
            <i style={{ width: `${Math.max(2, (it.count / max) * 100)}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Tiny trend line for a KPI tile: area fill, line and an emphasised last point. */
export function Sparkline({ values, color, label }: { values: number[]; color: string; label?: string }) {
  if (values.length < 2) return null;
  const w = 120, h = 34, pad = 3;
  const max = Math.max(1, ...values);
  const x = (i: number) => (i / (values.length - 1)) * w;
  const y = (v: number) => h - pad - (v / max) * (h - pad * 2);
  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const last = values.length - 1;
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <path className="area" d={`${line} L${w} ${h} L0 ${h} Z`} fill={color} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={x(last)} cy={y(values[last])} r="2.6" fill={color} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
