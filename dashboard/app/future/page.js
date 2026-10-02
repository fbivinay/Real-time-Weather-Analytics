"use client";
// 03 Future — which upcoming orders are at risk, why, and what could change it.
import { useMemo, useState } from "react";

import { BarChart, HeatStrip } from "../../components/charts";
import { useUI } from "../../components/Shell";
import { Empty, Kpi, RiskPill, SkeletonRows } from "../../components/ui";
import { postJSON } from "../../lib/api";
import { inr, istHour, istTime, mins, num, pct, SEVERITY_LABEL, TIER_LABEL } from "../../lib/format";
import { useFetch, useLive } from "../../lib/live";
import { RAIN_RAMP, RISK_HEX } from "../../lib/risk";

const WINDOWS = [6, 12, 24, 48];
const SORTS = { score: "Risk", dispatch: "Dispatch", delay: "Expected delay", breach: "SLA probability" };

function Timeline({ data, window }) {
  const buckets = useMemo(() => {
    if (!data?.timeline) return [];
    const out = [];
    for (let i = 0; i < Math.min(window, data.timeline.length); i += 2) {
      const a = data.timeline[i];
      const b = data.timeline[i + 1] || { orders: 0, high_plus: 0, sla_breaches: 0, rain_probability: a.rain_probability, avg_score: 0 };
      const orders = a.orders + b.orders;
      out.push({ hour: a.hour, orders, high_plus: a.high_plus + b.high_plus, sla: a.sla_breaches + b.sla_breaches,
        rain: Math.max(a.rain_probability, b.rain_probability),
        score: orders ? (a.avg_score * a.orders + b.avg_score * b.orders) / orders : 0, idx: i });
    }
    return out;
  }, [data, window]);
  if (!buckets.length) return <SkeletonRows rows={5} />;
  return (
    <div>
      <BarChart data={buckets} value={(d) => d.high_plus} label={(d) => istHour(d.hour)} height={300}
        every={window <= 12 ? 1 : window <= 24 ? 2 : 3}
        format={(v) => num(v)} color={(d) => (d.score >= 50 ? RISK_HEX.High : "#0b0b0c")}
        tooltip={(d) => <><div className="t">{istTime(d.hour)} – {istHour(new Date(new Date(d.hour).getTime() + 7.2e6).toISOString())} IST dispatch wave</div>
          {num(d.high_plus)} High/Critical of {num(d.orders)} orders · avg risk {Math.round(d.score)} · ~{num(d.sla)} SLA breaches · rain {pct(d.rain, 0)}</>} />
      <div style={{ display: "grid", gridTemplateColumns: "48px 1fr", gap: 0, alignItems: "center", marginTop: 6 }}>
        <span className="note">Rain</span>
        <div style={{ paddingRight: 8 }}>
          <HeatStrip cells={buckets} color={(c) => RAIN_RAMP[Math.min(4, Math.round(c.rain * 4))]} height={14}
            tooltip={(c) => <>{istTime(c.hour)}: {pct(c.rain, 0)} demand-weighted chance of rain</>} />
        </div>
      </div>
      <div className="legend" style={{ marginTop: 12 }}>
        <span><i style={{ background: RISK_HEX.High }} />Wave averaging High risk</span>
        <span><i style={{ background: "#0b0b0c" }} />Other waves</span>
        <span><i style={{ background: RAIN_RAMP[3] }} />Network rain probability</span>
      </div>
    </div>
  );
}

function RiskTable({ window }) {
  const { openOrder } = useUI();
  const opts = useFetch("/api/history/options");
  const [sort, setSort] = useState("score");
  const [page, setPage] = useState(1);
  const [f, setF] = useState({ category: "", city: "", warehouse: "", tier: "", q: "" });
  const [q, setQ] = useState("");
  const query = new URLSearchParams({ window, sort, page, size: 15, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) });
  const { data, loading, error, refreshing } = useFetch(`/api/future?${query}`, { refreshMs: 30000 });
  const t = data?.table;
  const pages = t ? Math.max(1, Math.ceil(t.total / t.size)) : 1;
  const set = (k, v) => { setF((x) => ({ ...x, [k]: v })); setPage(1); };
  return (
    <div className="panel">
      <div className="panel-h">
        <h2>Upcoming orders by risk</h2>
        <span className="q">{t ? `${num(t.total)} orders in the next ${window} h` : ""}</span>
      </div>
      <div className="panel-b">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
          <input className="input" style={{ width: 210 }} placeholder="Order or route (MUM → GOA)" value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") set("q", q.trim()); }}
            onBlur={() => set("q", q.trim())} />
          <select className="select" value={f.category} onChange={(e) => set("category", e.target.value)} aria-label="Risk">
            <option value="">All risk levels</option>{["Critical", "High", "Medium", "Low"].map((c) => <option key={c}>{c}</option>)}
          </select>
          <select className="select" value={f.city} onChange={(e) => set("city", e.target.value)} aria-label="City">
            <option value="">All cities</option>{(opts.data?.cities || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select className="select" value={f.warehouse} onChange={(e) => set("warehouse", e.target.value)} aria-label="Warehouse">
            <option value="">All warehouses</option>{(opts.data?.warehouses || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
          <select className="select" value={f.tier} onChange={(e) => set("tier", e.target.value)} aria-label="Service">
            <option value="">All services</option>{Object.entries(TIER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {loading && !t ? <SkeletonRows rows={10} /> : null}
        {error && !t ? <Empty>The order table needs the live backend.</Empty> : null}
        {t ? (
          <>
            <table className={`table fade${refreshing ? " refreshing" : ""}`}>
              <thead>
                <tr>
                  <th>Order</th><th>Route</th>
                  <th className="sort" onClick={() => { setSort("dispatch"); setPage(1); }}>Dispatch → ETA {sort === "dispatch" ? "↑" : ""}</th>
                  <th>Service</th><th>Weather</th>
                  <th className="sort" onClick={() => { setSort("score"); setPage(1); }}>Risk {sort === "score" ? "↓" : ""}</th>
                  <th className="r sort" onClick={() => { setSort("delay"); setPage(1); }}>Expected delay {sort === "delay" ? "↓" : ""}</th>
                  <th className="r sort" onClick={() => { setSort("breach"); setPage(1); }}>SLA probability {sort === "breach" ? "↓" : ""}</th>
                </tr>
              </thead>
              <tbody>
                {t.rows.map((o) => (
                  <tr key={o.id} className="click" onClick={() => openOrder(o.id)}>
                    <td className="mono"><span className="link">{o.id}</span></td>
                    <td className="mono">{o.route}</td>
                    <td className="num">{istTime(o.planned_dispatch)} <span className="faint">→ {istTime(o.eta_at)}</span></td>
                    <td>{TIER_LABEL[o.tier]}</td>
                    <td className="nowrap">{SEVERITY_LABEL[o.expected_class]} <span className="faint">{pct(o.p_rain, 0)}</span></td>
                    <td><RiskPill score={o.score} category={o.category} /></td>
                    <td className="r num">{o.expected_delay_min ? `+${mins(o.expected_delay_min)}` : "–"}</td>
                    <td className="r num">{pct(o.p_breach, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!t.rows.length ? <Empty>No upcoming orders match these filters.</Empty> : null}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
              <span />
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <button type="button" className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
                <span className="mono" style={{ fontSize: 14.9, padding: "0 6px" }}>{page} / {num(pages)}</span>
                <button type="button" className="btn ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

const PRESETS = [
  ["Dispatch 2 h later", { dispatch_shift_h: 2 }],
  ["Dispatch 2 h earlier", { dispatch_shift_h: -2 }],
  ["Reroute exposed orders", { reroute: true }],
  ["Rain 50% heavier", { rain_scale: 1.5 }],
];

function Scenario({ window, topCity }) {
  const opts = useFetch("/api/history/options");
  const [levers, setLevers] = useState({ dispatch_shift_h: 0, reroute: false, add_hub: "", rain_scale: 1 });
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const run = async (l = levers) => {
    setBusy(true); setErr(null);
    try {
      setRes(await postJSON("/api/scenario", { window_h: window, ...l, add_hub: l.add_hub || null }));
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const set = (k, v) => setLevers((x) => ({ ...x, [k]: v }));
  const rows = res ? [
    ["Late deliveries (45+ min)", "late", num],
    ["Average weather delay", "avg_delay_min", (v) => mins(v)],
    ["SLA breaches (expected)", "sla_breaches", num],
    ["Weather-exposed orders", "exposed", num],
    ["Estimated cost", "cost_inr", inr],
  ] : [];
  return (
    <div className="panel">
      <div className="panel-h"><h2>What if we change the operation?</h2></div>
      <div className="panel-b">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
          {PRESETS.map(([label, l]) => (
            <button key={label} type="button" className="btn ghost" style={{ height: 32, fontSize: 14.9, padding: "0 12px" }}
              onClick={() => { const n = { dispatch_shift_h: 0, reroute: false, add_hub: "", rain_scale: 1, ...l }; setLevers(n); run(n); }}>{label}</button>
          ))}
          {topCity ? (
            <button type="button" className="btn ghost" style={{ height: 32, fontSize: 14.9, padding: "0 12px" }}
              onClick={() => { const n = { dispatch_shift_h: 0, reroute: false, add_hub: topCity.id, rain_scale: 1 }; setLevers(n); run(n); }}>
              Micro-hub in {topCity.name}</button>
          ) : null}
        </div>
        <div className="lever">
          <div className="t"><span>Shift dispatch</span><span className="mono">{levers.dispatch_shift_h > 0 ? "+" : ""}{levers.dispatch_shift_h} h</span></div>
          <input type="range" min={-4} max={8} step={1} value={levers.dispatch_shift_h} onChange={(e) => set("dispatch_shift_h", +e.target.value)} />
        </div>
        <div className="lever">
          <label className="toggle"><input type="checkbox" checked={levers.reroute} onChange={(e) => set("reroute", e.target.checked)} />
            Reroute exposed orders via the other warehouse when its trip is drier</label>
        </div>
        <div className="lever">
          <div className="t"><span>Temporary micro-fulfilment hub</span></div>
          <select className="select" style={{ width: "100%" }} value={levers.add_hub} onChange={(e) => set("add_hub", e.target.value)}>
            <option value="">None</option>{(opts.data?.cities || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="lever">
          <div className="t"><span>Rainfall severity</span><span className="mono">{levers.rain_scale.toFixed(1)}×</span></div>
          <input type="range" min={0.5} max={2} step={0.1} value={levers.rain_scale} onChange={(e) => set("rain_scale", +e.target.value)} />
        </div>
        <button type="button" className="btn" style={{ width: "100%", marginTop: 8 }} onClick={() => run()} disabled={busy}>
          {busy ? "Running…" : "Run scenario"}</button>
        {err ? <div className="note" style={{ marginTop: 10, color: "var(--down)" }}>{err}</div> : null}
        {res ? (
          <div style={{ marginTop: 16, animation: "fadein .3s" }}>
            <table className="table compact">
              <thead><tr><th>Metric</th><th className="r">Current</th><th className="r">With change</th><th className="r">Change</th></tr></thead>
              <tbody>
                {rows.map(([label, k, fmt]) => {
                  const d = res.simulated[k] - res.current[k];
                  return (
                    <tr key={k}>
                      <td>{label}</td>
                      <td className="r num">{fmt(res.current[k])}</td>
                      <td className="r num"><b>{fmt(res.simulated[k])}</b></td>
                      <td className={`r num ${d < 0 ? "delta-good" : d > 0 ? "delta-bad" : ""}`}>
                        {d === 0 ? "–" : `${d > 0 ? "+" : "−"}${fmt(Math.abs(d))}`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function Future() {
  const live = useLive();
  const [window, setWindow] = useState(24);
  const tl = useFetch("/api/future/timeline", { refreshMs: 30000 });
  const s = tl.data?.summary?.[String(window)] || live.overview?.future?.[String(window)];
  const topCity = live.overview?.top?.cities?.[0];
  return (
    <main className="page">
      <div className="pagehead">
        <div>
          <h1 className="headline">Upcoming delivery risk</h1>
        </div>
        <div className="seg">{WINDOWS.map((w) => <button key={w} type="button" className={w === window ? "on" : ""} onClick={() => setWindow(w)}>Next {w} h</button>)}</div>
      </div>
      {s ? (
        <div className="panel kpis">
          <Kpi label="Scheduled orders" value={s.scheduled} sub={`dispatching in ${window} h`} />
          <Kpi label="Weather exposed" value={s.exposed} sub="likely rain on trip" />
          <Kpi label="Heavy rain exposure" value={s.heavy_exposed} sub="heavy or extreme" hot={s.heavy_exposed > 0} />
          <Kpi label="High risk" value={s.high} sub="score 50–74" />
          <Kpi label="Critical" value={s.critical} sub="score 75+" hot={s.critical > 0} />
          <Kpi label="Predicted delay" value={s.expected_delay_min} sub="per order" format={(v) => mins(v)} />
          <Kpi label="SLA breaches" value={s.sla_breaches} sub="expected count" hot={s.sla_breaches > 0} />
        </div>
      ) : <SkeletonRows rows={2} h={60} />}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 420px", gap: 18, marginTop: 18, alignItems: "stretch" }}>
        <div className="panel">
          <div className="panel-h"><h2>Risk timeline, next {window} h</h2></div>
          <div className="panel-b"><Timeline data={tl.data} window={window} /></div>
        </div>
        <Scenario window={window} topCity={topCity} />
      </div>
      <div className="sect"><RiskTable window={window} /></div>
    </main>
  );
}
