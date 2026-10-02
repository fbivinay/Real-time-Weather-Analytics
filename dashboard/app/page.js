"use client";
// 01 Overview — what is happening right now.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Spark } from "../components/charts";
import { useUI } from "../components/Shell";
import { Empty, Kpi, Num, RiskPill, Skeleton, SkeletonRows } from "../components/ui";
import { inr, istHour, istTime, mins, num, pct, SEVERITY_LABEL, TIER_LABEL, TYPE_LABEL } from "../lib/format";
import { useLive } from "../lib/live";

function headline(ov) {
  const k = ov.kpis;
  const f = ov.future?.["12"] || {};
  if (k.delayed_trips || f.heavy_exposed || f.exposed) {
    const parts = [];
    if (k.delayed_trips) parts.push(`slowing ${num(k.delayed_trips)} trucks on the road`);
    if (f.exposed) parts.push(`putting ${num(f.exposed)} deliveries of the next 12 hours in its path`);
    return `Rain is ${parts.join(" and ")}.`;
  }
  return `No significant rain on the network — ${num(k.in_transit)} deliveries are moving normally.`;
}

function KpiBand({ ov }) {
  const k = ov.kpis;
  return (
    <div className="panel kpis">
      <Kpi label="Total orders" value={k.total_orders} sub="created today (sim day)" />
      <Kpi label="In transit" value={k.in_transit} sub={`${num(k.active_trips)} trucks on the road`} />
      <Kpi label="Weather exposed" value={k.weather_exposed} sub="in transit through rain now" />
      <Kpi label="Weather affected" value={k.weather_affected} sub="delayed by rain today" hot={k.weather_affected > 0} />
      <Kpi label="Delayed" value={k.delayed} sub="30+ min late, any cause" />
      <Kpi label="SLA risk" value={k.sla_risk} sub="likely to miss promise (24 h)" hot={k.sla_risk > 0} />
      <Kpi label="Average delay" value={k.avg_delay_min} sub={`on-time ${pct(k.on_time_rate)}`} format={(v) => mins(v)} />
      <Kpi label="Weather impact cost" value={k.weather_cost_inr} sub="today, simulated estimate" format={inr} />
    </div>
  );
}

function LiveOps({ ov }) {
  const s = ov.stream || {};
  const k = ov.kpis;
  const last = s.last_event || {};
  const windows = s.windows || [];
  return (
    <div className="panel">
      <div className="panel-h"><h2>Live operations</h2><span className="q">Spark 30-second windows, network total</span></div>
      <div className="panel-b">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 16 }}>
          <div><div className="note">Events / second</div><div style={{ fontSize: 22, fontWeight: 650 }}><Num value={s.events_per_s} format={(v) => num(v, 1)} /></div></div>
          <div><div className="note">Active deliveries</div><div style={{ fontSize: 22, fontWeight: 650 }}><Num value={k.in_transit} /></div></div>
          <div><div className="note">Trucks delayed by rain</div><div style={{ fontSize: 22, fontWeight: 650 }} className={k.delayed_trips ? "red" : ""}><Num value={k.delayed_trips} /></div></div>
          <div><div className="note">Delivered today</div><div style={{ fontSize: 22, fontWeight: 650 }}><Num value={k.delivered_today} /></div></div>
        </div>
        {windows.length ? (
          <Spark data={windows} value={(w) => w.events} height={44}
            tooltip={(w) => <><div className="t">{istHour(w.window_start)} wall clock</div>{num(w.events)} events · {num(w.created)} orders · {num(w.completed)} delivered</>} />
        ) : <Skeleton h={44} />}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: 13 }} className="muted">
          <span>Last event: <b className="mono" style={{ color: "var(--ink)" }}>{TYPE_LABEL[last.type] || "–"}</b>{last.order_id ? ` · ${last.order_id}` : ""}{last.city_id ? ` · ${last.city_id}` : ""}</span>
          <span>Freshness <span className="mono">{s.freshness_s != null ? `${s.freshness_s}s` : "–"}</span> · lag <span className="mono">{num(s.consumer_lag ?? 0)}</span></span>
        </div>
        {k.wet_cities?.length ? (
          <div style={{ marginTop: 12, fontSize: 13 }}>
            <span className="muted">Raining now: </span>{k.wet_cities.map((c) => <Link key={c} href={`/map?sel=city:${c}`} className="tag" style={{ marginRight: 4, textDecoration: "none" }}>{c}</Link>)}
          </div>
        ) : null}
      </div>
    </div>
  );
}

const TABS = [["states", "Regions"], ["routes", "Routes"], ["warehouses", "Warehouses"], ["cities", "Cities"]];
const KIND = { states: "state", routes: "route", warehouses: "warehouse", cities: "city" };

function CurrentRisk({ ov }) {
  const router = useRouter();
  const [tab, setTab] = useState("states");
  const rows = ov.top?.[tab] || [];
  return (
    <div className="panel">
      <div className="panel-h">
        <h2>Where rain hits deliveries next</h2>
        <div className="seg">{TABS.map(([k, l]) => <button key={k} type="button" className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      </div>
      <div className="panel-b">
        <table className="table">
          <thead><tr><th>{TABS.find((t) => t[0] === tab)[1].replace(/s$/, "")}</th><th>Impact</th><th className="r">Orders 12 h</th><th className="r">Exposed</th><th className="r">Avg delay</th><th>Top route</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="click" onClick={() => router.push(`/map?sel=${KIND[tab]}:${encodeURIComponent(r.id)}`)}>
                <td><span className="link">{r.code || r.name || r.id}</span></td>
                <td><RiskPill score={r.score} category={r.category} /></td>
                <td className="r num">{num(r.orders)}</td>
                <td className="r num">{num(r.exposed)}</td>
                <td className="r num">{r.avg_delay_min ? `+${mins(r.avg_delay_min)}` : "–"}</td>
                <td className="mono" style={{ fontSize: 13 }}>{r.top_route || (tab === "routes" ? SEVERITY_LABEL[r.rain_now] : "–")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length ? <Empty>No impact data yet.</Empty> : null}
      </div>
    </div>
  );
}

function CriticalOrders({ ov }) {
  const { openOrder } = useUI();
  const rows = ov.critical_orders || [];
  return (
    <div className="panel">
      <div className="panel-h">
        <h2>Critical upcoming orders</h2>
        <span className="q">One row per route, service and dispatch wave · <Link href="/future" className="link">all upcoming orders →</Link></span>
      </div>
      <div className="panel-b">
        {rows.length ? (
          <table className="table">
            <thead><tr><th>Order</th><th>Route</th><th>Dispatch</th><th>Service</th><th>Weather</th><th>Risk</th><th className="r">Expected delay</th><th className="r">SLA breach</th></tr></thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.id} className="click" onClick={() => openOrder(o.id)}>
                  <td className="mono"><span className="link">{o.id}</span>{o.similar ? <span className="faint"> +{num(o.similar)} similar</span> : null}</td>
                  <td className="mono">{o.route}</td>
                  <td className="num">{istTime(o.planned_dispatch)}</td>
                  <td>{TIER_LABEL[o.tier]}</td>
                  <td className="nowrap">{SEVERITY_LABEL[o.expected_class]} <span className="faint">({pct(o.p_rain, 0)})</span></td>
                  <td><RiskPill score={o.score} category={o.category} /></td>
                  <td className="r num">+{mins(o.expected_delay_min)}</td>
                  <td className="r num">{pct(o.p_breach, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No orders in the High or Critical band right now.</Empty>}
      </div>
    </div>
  );
}

export default function Overview() {
  const live = useLive();
  const ov = live.overview;
  if (!ov) {
    return (
      <main className="page">
        <Skeleton h={14} w={220} />
        <Skeleton h={40} w={760} style={{ margin: "12px 0 24px" }} />
        <Skeleton h={96} />
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 18 }}><SkeletonRows rows={8} /><SkeletonRows rows={8} /></div>
        {live.status === "offline" ? <Empty>The live stream is unavailable and no snapshot could be loaded.</Empty> : null}
      </main>
    );
  }
  return (
    <main className="page">
      <div className="pagehead">
        <div>
          <div className="eyebrow">01 · Overview · what is happening right now</div>
          <h1 className="headline">{headline(ov)}</h1>
          <p className="lede">ShopFlow India across 40 cities, 10 fulfilment centres and 80 routes. Figures update with every
            engine tick from the live event stream.</p>
        </div>
      </div>
      <KpiBand ov={ov} />
      <div className="grid sect" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.25fr)" }}>
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="panel">
            <div className="panel-h"><h2>Alerts</h2><span className="q">Plain-language, from the engine</span></div>
            <div className="panel-b">
              <ul className="alerts">
                {ov.alerts.map((a) => <li key={a.text} className={a.level}><i className="mk" /><span>{a.text}</span></li>)}
              </ul>
            </div>
          </div>
          <LiveOps ov={ov} />
        </div>
        <CurrentRisk ov={ov} />
      </div>
      <div className="sect"><CriticalOrders ov={ov} /></div>
    </main>
  );
}
