"use client";
// Hand-built SVG charts: thin marks, 4px rounded data ends on the baseline,
// recessive grid, one y-axis, hover tooltip on every mark.
import { useEffect, useRef, useState } from "react";

function useWidth() {
  const ref = useRef(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.max(120, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function niceMax(v) {
  if (!v || v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

// Rounded top corners only (data end), square at the baseline.
function barPath(x, y, w, h, r = 4) {
  const rr = Math.min(r, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

export function BarChart({ data, value, label, format = (v) => v, height = 200, color = () => "var(--ink)",
  tooltip, onClick, every = 1, yFormat, selected }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const pad = { l: 48, r: 8, t: 10, b: 26 };
  const vals = data.map(value);
  const max = niceMax(Math.max(...vals, 0));
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const slot = iw / Math.max(1, data.length);
  const bw = Math.max(2, Math.min(36, slot - 2));
  const ticks = [0, max / 2, max];
  return (
    <div className="chart" ref={ref}>
      <svg height={height} role="img">
        <g className="grid">
          {ticks.map((t) => (
            <line key={t} x1={pad.l} x2={width - pad.r} y1={pad.t + ih - (t / max) * ih} y2={pad.t + ih - (t / max) * ih} />
          ))}
        </g>
        <g className="axis">
          {ticks.map((t) => (
            <text key={t} x={pad.l - 8} y={pad.t + ih - (t / max) * ih + 4} textAnchor="end">{(yFormat || format)(t)}</text>
          ))}
          {data.map((d, i) => (i % every === 0 ? (
            <text key={i} x={pad.l + i * slot + slot / 2} y={height - 8} textAnchor="middle">{label(d)}</text>
          ) : null))}
        </g>
        {data.map((d, i) => {
          const h = (vals[i] / max) * ih;
          const x = pad.l + i * slot + (slot - bw) / 2;
          const on = hover === i || selected === i;
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
              onClick={onClick ? () => onClick(d, i) : undefined} style={{ cursor: onClick ? "pointer" : "default" }}>
              <rect x={pad.l + i * slot} y={pad.t} width={slot} height={ih} fill="transparent" />
              <path className={`mark${hover !== null && !on ? " hover-mark" : ""}`} d={barPath(x, pad.t + ih - h, bw, h)}
                fill={color(d, i)} />
            </g>
          );
        })}
      </svg>
      {hover !== null && data[hover] ? (
        <div className="tip" style={{ left: pad.l + hover * slot + slot / 2, top: pad.t + ih - (vals[hover] / max) * ih }}>
          {tooltip ? tooltip(data[hover]) : <><div className="t">{label(data[hover])}</div>{format(vals[hover])}</>}
        </div>
      ) : null}
    </div>
  );
}

// Tiny bar sparkline (no axes) for the live strip.
export function Spark({ data, value, height = 40, color = "var(--ink)", tooltip }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const vals = data.map(value);
  const max = Math.max(...vals, 1);
  const slot = width / Math.max(1, data.length);
  const bw = Math.max(2, slot - 2);
  return (
    <div className="chart" ref={ref}>
      <svg height={height}>
        {data.map((d, i) => {
          const h = Math.max(1, (vals[i] / max) * (height - 2));
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={i * slot} y={0} width={slot} height={height} fill="transparent" />
              <path className="mark" d={barPath(i * slot + (slot - bw) / 2, height - h, bw, h, 2)} fill={color}
                opacity={hover === null || hover === i ? 1 : 0.55} />
            </g>
          );
        })}
      </svg>
      {hover !== null && tooltip ? (
        <div className="tip" style={{ left: hover * slot + slot / 2, top: 0 }}>{tooltip(data[hover])}</div>
      ) : null}
    </div>
  );
}

// Horizontal bars with direct labels (HTML grid: crisp text, easy hover).
export function HBars({ rows, value, label, format, color = () => "var(--ink)", onClick, sub, max }) {
  const top = max ?? Math.max(...rows.map(value), 1);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {rows.map((r, i) => (
        <div key={i} onClick={onClick ? () => onClick(r) : undefined}
          style={{ display: "grid", gridTemplateColumns: "130px 1fr 130px", gap: 12, alignItems: "center",
            cursor: onClick ? "pointer" : "default", fontSize: 13.5 }}>
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            className={onClick ? "link" : ""}>{label(r)}</div>
          <div className="bar" style={{ height: 8 }}>
            <i style={{ width: `${Math.max(0.5, (value(r) / top) * 100)}%`, background: color(r) }} />
          </div>
          <div className="num" style={{ textAlign: "right" }}>
            {format(value(r))}{sub ? <div className="note">{sub(r)}</div> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

// One row of cells (e.g. rain probability per hour), coloured by a ramp function.
export function HeatStrip({ cells, color, tooltip, height = 18 }) {
  const [hover, setHover] = useState(null);
  return (
    <div className="chart" style={{ display: "flex", gap: 2, height }}>
      {cells.map((c, i) => (
        <div key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
          style={{ flex: 1, background: color(c), borderRadius: 3, outline: hover === i ? "1.5px solid var(--ink)" : "none",
            position: "relative" }}>
          {hover === i && tooltip ? <div className="tip" style={{ left: "50%", top: 0 }}>{tooltip(c)}</div> : null}
        </div>
      ))}
    </div>
  );
}
