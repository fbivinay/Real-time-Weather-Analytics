"use client";
// 04 History — what happened, and how much did weather cost us?
import { useMemo, useState } from "react";

import { BarChart, HBars } from "../../components/charts";
import { Empty, Kpi, SkeletonRows } from "../../components/ui";
import { CATEGORY_LABEL, inr, MONTHS, mins, num, pct, SEVERITY_LABEL } from "../../lib/format";
import { useFetch } from "../../lib/live";
import { RISK_HEX } from "../../lib/risk";

const METRICS = {
  weather_cost: ["Weather cost", (d) => d.weather_cost || 0, inr],
  affected_share: ["Delayed by rain", (d) => (d.orders ? d.affected / d.orders : 0), (v) => pct(v, 1)],
  orders: ["Orders", (d) => d.orders, num],
  avg_delay_min: ["Avg weather delay", (d) => d.avg_delay_min || 0, (v) => mins(v)],
};
const SEASONS = ["winter", "pre-monsoon", "monsoon", "post-monsoon"];
const SEV_COLOR = { none: RISK_HEX.Low, rain: RISK_HEX.Medium, heavy: RISK_HEX.High, extreme: RISK_HEX.Critical };
const per1000 = (d) => (d.orders ? (d.weather_cost / d.orders) * 1000 : 0);

function Filters({ f, set, opts }) {
  const o = opts || {};
  const cities = (o.cities || []).filter((c) => !f.state || c.state === f.state);
  const routes = (o.routes || []).filter((r) => (!f.city || r.city_id === f.city) && (!f.warehouse || r.warehouse_id === f.warehouse));
  const sel = (k, label, items, val = (x) => x.id, lab = (x) => x.name) => (
    <select className="select" value={f[k] || ""} onChange={(e) => set(k, e.target.value)} aria-label={label}>
      <option value="">{label}</option>
      {items.map((x) => <option key={val(x)} value={val(x)}>{lab(x)}</option>)}
    </select>
  );
  return (
    <div className="filterbar">
      {sel("year", "All years", o.years || [], (y) => y, (y) => y)}
      {sel("month", "All months", MONTHS.map((m, i) => i + 1), (m) => m, (m) => MONTHS[m - 1])}
      {sel("state", "All states", o.states || [], (s) => s, (s) => s)}
      {sel("city", "All cities", cities)}
      {sel("warehouse", "All warehouses", o.warehouses || [])}
      {sel("hub", "All hubs", (o.hubs || []).filter((h) => !f.city || h.city_id === f.city))}
      {sel("route", "All routes", routes, (r) => r.id, (r) => r.code)}
      {sel("category", "All categories", o.categories || [])}
      {sel("severity", "Any weather", o.severities || [], (s) => s, (s) => SEVERITY_LABEL[s])}
      <button type="button" className="btn ghost" onClick={() => set(null)}>Reset</button>
    </div>
  );
}

function YearTable({ rows }) {
  return (
    <table className="table">
      <thead><tr><th>Year</th><th className="r">Orders</th><th className="r">Growth</th><th className="r">Delayed by rain</th><th className="r">Avg delay</th><th className="r">Weather cost</th><th className="r">Per 1,000 orders</th></tr></thead>
      <tbody>
        {rows.map((y, i) => {
          const prev = rows[i - 1];
          const perDay = y.orders / (y.days || 1);
          const growth = prev ? perDay / (prev.orders / (prev.days || 1)) - 1 : null;
          return (
            <tr key={y.year}>
              <td className="mono">{y.year}{y.days < 360 ? <span className="faint"> · {y.days} d</span> : null}</td>
              <td className="r num">{num(y.orders)}</td>
              <td className="r num">{growth === null ? "–" : `${growth >= 0 ? "+" : ""}${pct(growth, 0)}`}</td>
              <td className="r num">{pct(y.affected / y.orders)}</td>
              <td className="r num">{y.avg_delay_min ? `+${mins(y.avg_delay_min)}` : "–"}</td>
              <td className="r num">{inr(y.weather_cost)}</td>
              <td className="r num">{inr(per1000(y))}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Breakdown({ rows, label, onPick }) {
  const max = Math.max(...rows.map((r) => r.weather_cost || 0), 1);
  return (
    <table className="table">
      <thead><tr><th>{label}</th><th>Weather cost</th><th className="r">Orders</th><th className="r">Delayed by rain</th><th className="r">Avg delay</th><th className="r">SLA breaches</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className={onPick ? "click" : ""} onClick={onPick ? () => onPick(r) : undefined}>
            <td>{onPick ? <span className="link">{r.name || r.id}</span> : r.name || CATEGORY_LABEL[r.id] || r.id}</td>
            <td style={{ width: "30%", minWidth: 170 }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 72px", gap: 8, alignItems: "center" }}>
                <div className="bar"><i style={{ width: `${((r.weather_cost || 0) / max) * 100}%` }} /></div>
                <span className="num" style={{ textAlign: "right", fontSize: 14.9 }}>{inr(r.weather_cost)}</span>
              </div>
            </td>
            <td className="r num">{num(r.orders)}</td>
            <td className="r num">{pct(r.affected / (r.orders || 1))}</td>
            <td className="r num">{r.avg_delay_min ? `+${mins(r.avg_delay_min)}` : "–"}</td>
            <td className="r num">{num(r.sla_breaches)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function History() {
  const [f, setF] = useState({});
  const [metric, setMetric] = useState("weather_cost");
  const opts = useFetch("/api/history/options");
  const qs = new URLSearchParams(Object.fromEntries(Object.entries(f).filter(([, v]) => v))).toString();
  const { data, loading, error } = useFetch(`/api/history${qs ? `?${qs}` : ""}`);
  const set = (k, v) => setF(k === null ? {} : (x) => ({ ...x, [k]: v,
    ...(k === "state" ? { city: "", route: "", hub: "" } : {}), ...(k === "city" ? { route: "", hub: "" } : {}) }));
  const t = data?.totals;
  const seasons = useMemo(() => SEASONS.map((s) => data?.seasons?.find((x) => x.season === s)).filter(Boolean), [data]);
  const sev = useMemo(() => ["none", "rain", "heavy", "extreme"].map((s) => data?.severity?.find((x) => x.severity === s)).filter(Boolean), [data]);
  const [mLabel, mValue, mFormat] = METRICS[metric];
  const geo = data?.geography;
  const pickGeo = geo ? (r) => set(geo.level === "state" ? "state" : geo.level === "city" ? "city" : "route", r.id) : null;
  const heavy = sev.find((s) => s.severity === "heavy");
  const rain = sev.find((s) => s.severity === "rain");
  const dry = sev.find((s) => s.severity === "none");
  const active = Object.entries(f).filter(([, v]) => v);

  return (
    <main className="page">
      <div className="pagehead">
        <div>
          <h1 className="headline">Delivery history</h1>
        </div>
      </div>
      <Filters f={f} set={set} opts={opts.data} />
      {loading && !data ? <div className="sect"><SkeletonRows rows={10} h={28} /></div> : null}
      {error && !data ? <Empty>History needs the live backend or a demo snapshot.</Empty> : null}
      {t && !t.orders ? <div className="sect panel"><Empty>No orders match these filters.</Empty></div> : null}
      {t && t.orders ? (
        <>
          <div className="panel kpis sect" style={{ opacity: loading ? 0.6 : 1, transition: "opacity .2s" }}>
            <Kpi label="Orders" value={t.orders} />
            <Kpi label="Weather exposed" value={t.exposed} sub={pct(t.exposed / t.orders)} />
            <Kpi label="Weather affected" value={t.affected} sub={`${pct(t.affected / t.orders)} of orders`} hot />
            <Kpi label="Delayed (30+ min)" value={t.delayed} sub={pct(t.delayed / t.orders)} />
            <Kpi label="SLA breaches" value={t.sla_breaches} sub={pct(t.sla_breaches / t.orders, 2)} />
            <Kpi label="Avg weather delay" value={t.avg_delay_min} sub="per affected order" format={(v) => mins(v)} />
            <Kpi label="Weather cost" value={t.weather_cost} sub={`${inr(per1000(t))} per 1,000 orders`} format={inr} hot />
          </div>

          <div className="panel sect">
            <div className="panel-h">
              <h2>Month by month</h2>
              <div className="seg">{Object.entries(METRICS).map(([k, [l]]) => <button key={k} type="button" className={metric === k ? "on" : ""} onClick={() => setMetric(k)}>{l}</button>)}</div>
            </div>
            <div className="panel-b">
              <BarChart data={data.monthly} value={mValue} format={mFormat} height={220}
                yFormat={metric === "weather_cost" ? (v) => inr(v).replace("₹", "") : mFormat}
                label={(d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : MONTHS[+d.month.slice(5) - 1][0])}
                every={data.monthly.length > 24 ? 3 : 1}
                color={(d) => ([6, 7, 8, 9].includes(+d.month.slice(5)) ? "#cc5a43" : "#0b0b0c")}
                onClick={(d) => setF((x) => ({ ...x, year: d.month.slice(0, 4), month: String(+d.month.slice(5)) }))}
                tooltip={(d) => <><div className="t">{MONTHS[+d.month.slice(5) - 1]} {d.month.slice(0, 4)}</div>
                  {num(d.orders)} orders · {pct(d.affected / d.orders)} delayed by rain · {inr(d.weather_cost)}</>} />
              <div className="legend" style={{ marginTop: 10 }}>
                <span><i style={{ background: "#cc5a43" }} />Monsoon months (Jun–Sep)</span>
                <span><i style={{ background: "#0b0b0c" }} />Other months</span>
                
              </div>
            </div>
          </div>

          <div className="grid sect" style={{ gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)" }}>
            <div className="panel">
              <div className="panel-h"><h2>Year over year</h2></div>
              <div className="panel-b"><YearTable rows={data.yearly} /></div>
            </div>
            <div className="panel">
              <div className="panel-h"><h2>Seasons</h2></div>
              <div className="panel-b">
                <HBars rows={seasons} value={(s) => s.affected / s.orders} label={(s) => s.season[0].toUpperCase() + s.season.slice(1)}
                  format={(v) => pct(v)} color={(s) => (s.season === "monsoon" ? "#cc5a43" : "#0b0b0c")}
                  sub={(s) => `${inr(per1000(s))} / 1k orders`} />
              </div>
            </div>
          </div>

          <div className="panel sect">
            <div className="panel-h"><h2>Rain severity vs delivery outcome</h2></div>
            <div className="panel-b">
              {heavy && rain && dry ? (
                <p style={{ margin: "0 0 16px", fontSize: 17.2 }}>
                  On heavy-rain route-days an exposed order is delayed <b>{mins(heavy.delay_per_exposed_min)}</b> on average —
                  {" "}<b>{(heavy.delay_per_exposed_min / Math.max(1, rain.delay_per_exposed_min)).toFixed(1)}×</b> a normal rainy day —
                  and the SLA breach rate rises from <b>{pct(dry.breach_rate)}</b> when dry to <b>{pct(heavy.breach_rate)}</b>.
                </p>
              ) : null}
              <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 32 }}>
                <div>
                  <div className="h3" style={{ marginTop: 0 }}>Weather delay per exposed order</div>
                  <HBars rows={sev} value={(s) => s.delay_per_exposed_min || 0} label={(s) => SEVERITY_LABEL[s.severity]}
                    format={(v) => mins(v)} color={(s) => SEV_COLOR[s.severity]} sub={(s) => `${num(s.exposed)} exposed`} />
                </div>
                <div>
                  <div className="h3" style={{ marginTop: 0 }}>SLA breach rate</div>
                  <HBars rows={sev} value={(s) => s.breach_rate || 0} label={(s) => SEVERITY_LABEL[s.severity]}
                    format={(v) => pct(v, 2)} color={(s) => SEV_COLOR[s.severity]} sub={(s) => `${num(s.orders)} orders`} />
                </div>
              </div>
            </div>
          </div>

          <div className="grid sect" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
            <div className="panel">
              <div className="panel-h"><h2>By {geo.level}</h2></div>
              <div className="panel-b"><Breakdown rows={geo.rows} label={geo.level[0].toUpperCase() + geo.level.slice(1)} onPick={geo.level === "route" ? null : pickGeo} /></div>
            </div>
            <div className="panel">
              <div className="panel-h"><h2>By product category</h2></div>
              <div className="panel-b"><Breakdown rows={data.categories} label="Category" /></div>
            </div>
          </div>
        </>
      ) : null}
    </main>
  );
}
