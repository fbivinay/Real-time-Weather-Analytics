"use client";

import { useState } from "react";

import { CATEGORY_LABEL, HAZARD_LABEL, categoryOf } from "../lib/categories";
import { istClock, num } from "../lib/format";

const FACTORS = [
  ["rain", "Rain"],
  ["wind", "Wind"],
  ["heat", "Heat"],
  ["fog", "Fog"],
];

const VERDICT = {
  ok: "Sensor healthy",
  weather_event: "Genuine local weather (neighbours agree)",
  sensor_suspect: "Suspect sensor - excluded from risk",
  stale: "Sensor silent - excluded from risk",
};

function Sparkline({ points }) {
  const [hover, setHover] = useState(null);
  if (!points || points.length < 2) return <p className="muted" style={{ fontSize: ".8rem" }}>Collecting history…</p>;
  const w = 320;
  const h = 56;
  const x = (i) => (i / (points.length - 1)) * (w - 4) + 2;
  const y = (s) => h - 3 - (Math.max(0, Math.min(100, s ?? 0)) / 100) * (h - 6);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join("");
  const shown = hover ?? points.length - 1;
  return (
    <div>
      <p className="muted" style={{ fontSize: ".74rem" }}>
        Risk score {num(points[shown].score)} at {istClock(points[shown].t)} IST
        {hover === null ? " (latest)" : ""}
      </p>
      <svg
        className="spark"
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Risk score history, latest ${points[points.length - 1].score}`}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHover(Math.round(((e.clientX - r.left) / r.width) * (points.length - 1)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        <line x1="0" x2={w} y1={y(50)} y2={y(50)} stroke="var(--rule)" strokeDasharray="3 3" />
        <line x1="0" x2={w} y1={y(75)} y2={y(75)} stroke="var(--rule)" strokeDasharray="3 3" />
        <path d={path} fill="none" stroke="var(--ink-soft)" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        <circle cx={x(shown)} cy={y(points[shown].score)} r="3.5" fill="var(--ink)" stroke="var(--surface)" strokeWidth="2" />
      </svg>
    </div>
  );
}

export default function LocationPanel({ location, history }) {
  if (!location) {
    return (
      <section className="card">
        <h2>Location detail</h2>
        <div className="empty">Select a station on the map.</div>
      </section>
    );
  }
  const a = location.assessment;
  const cat = categoryOf(location);
  const v = location.values ?? {};
  const forecast = a?.forecast_category ? `${CATEGORY_LABEL[a.forecast_category]} (${a.forecast_score}) expected within 60 min` : null;
  return (
    <section className="card" aria-labelledby="loc-title">
      <div className="card-head">
        <h2 id="loc-title">{location.name || location.station_id}</h2>
        <span className="hint mono">{location.station_id} · {location.kind === "reference" ? "Open-Meteo city reference" : "simulated hub sensor"}</span>
      </div>
      <div className="loc-head">
        <span className="loc-score">{a ? a.score : "—"}</span>
        <span className={`chip chip-${cat}`}>{CATEGORY_LABEL[cat]}</span>
        {a?.hazard && <span className="chip chip-plain">{HAZARD_LABEL[a.hazard]}</span>}
        {a?.unusual && <span className="chip chip-plain" title="Beyond this city's 95th percentile for the month">Unusual for here</span>}
        {a?.developing && <span className="chip chip-plain">Developing</span>}
      </div>
      {location.verdict && <p className="muted" style={{ fontSize: ".82rem" }}>{VERDICT[location.verdict.status]}{location.verdict.reason ? ` (${location.verdict.reason} on ${location.verdict.field})` : ""}</p>}
      {forecast && <p style={{ fontSize: ".86rem" }}>Forecast: {forecast}</p>}
      {a && (
        <div className="factors" aria-label="Risk factors">
          {FACTORS.map(([key, label]) => (
            <div className="factor" key={key}>
              <span>{label}</span>
              <span className="bar"><i style={{ width: `${Math.round((a.factors?.[key] ?? 0) * 100)}%` }} /></span>
              <span className="num">{Math.round((a.factors?.[key] ?? 0) * 100)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="values">
        <div><span className="k">Rain mm/h</span><span className="v">{num(v.rain_avg, 1)}</span></div>
        <div><span className="k">Gust km/h</span><span className="v">{num(v.gust_max)}</span></div>
        <div><span className="k">Temp °C</span><span className="v">{num(v.temp_avg, 1)}</span></div>
        <div><span className="k">Humidity %</span><span className="v">{num(v.humidity_avg)}</span></div>
        <div><span className="k">Visibility m</span><span className="v">{num(v.visibility_min)}</span></div>
        <div><span className="k">Pressure hPa</span><span className="v">{num(v.pressure_avg, 1)}</span></div>
      </div>
      <Sparkline points={history} />
    </section>
  );
}
