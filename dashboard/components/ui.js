"use client";
// Small shared pieces: risk pill, animated numbers, bars, skeletons.
import { useEffect, useRef, useState } from "react";

import { num } from "../lib/format";
import { category as catOf, RISK_HEX } from "../lib/risk";

export function RiskPill({ score, category }) {
  const cat = category || catOf(score || 0);
  return (
    <span className={`pill ${cat}`} title={`${cat} risk, score ${Math.round(score ?? 0)} of 100`}>
      {cat.toUpperCase()} {Math.round(score ?? 0)}
    </span>
  );
}

// Tweens to the new value over 450 ms so ticking KPIs read as change, not flicker.
export function Num({ value, format = num, className }) {
  const [shown, setShown] = useState(value ?? 0);
  const from = useRef(value ?? 0);
  useEffect(() => {
    if (value === null || value === undefined) return undefined;
    const start = performance.now();
    const a = from.current;
    const b = value;
    if (a === b) return undefined;
    let raf;
    const step = (t) => {
      const k = Math.min(1, (t - start) / 450);
      const e = 1 - (1 - k) ** 3;
      setShown(a + (b - a) * e);
      if (k < 1) raf = requestAnimationFrame(step);
      else from.current = b;
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); from.current = b; };
  }, [value]);
  if (value === null || value === undefined) return <span className={className}>–</span>;
  return <span className={className}>{format(shown)}</span>;
}

export function Bar({ value, max = 1, color = "var(--ink)" }) {
  const w = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return <div className="bar"><i style={{ width: `${w}%`, background: color }} /></div>;
}

// Score split into its contributions (risk explanation), in the risk colour.
export function ScoreBar({ contributions, score }) {
  const color = RISK_HEX[catOf(score || 0)];
  const parts = (contributions || []).filter((c) => c.points > 0);
  return (
    <div className="segbar" title={parts.map((c) => `${c.label} +${c.points}`).join(" · ")}>
      {parts.map((c, i) => (
        <i key={c.key} style={{ width: `${c.points}%`, background: color, opacity: 1 - i * 0.11 }} />
      ))}
    </div>
  );
}

export function Skeleton({ h = 18, w = "100%", style }) {
  return <div className="sk" style={{ height: h, width: w, ...style }} />;
}

export function SkeletonRows({ rows = 6, h = 20 }) {
  return (
    <div style={{ display: "grid", gap: 12, padding: "8px 0" }}>
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={h} />)}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function Kpi({ label, value, sub, format, hot }) {
  return (
    <div className={`kpi${hot ? " hot" : ""}`}>
      <div className="l">{label}</div>
      <div className="v"><Num value={value} format={format} /></div>
      {sub ? <div className="s">{sub}</div> : null}
    </div>
  );
}

export function StatusDot({ status, label }) {
  return <span className="dotlabel"><i className={`dot ${status || ""}`} />{label}</span>;
}
