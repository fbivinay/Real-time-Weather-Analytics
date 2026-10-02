"use client";
// Detail panel for one location: state, city, warehouse, hub or route.
import { inr, istTime, MONTHS, mins, num, pct, SEVERITY_LABEL, TIER_LABEL } from "../lib/format";
import { useFetch } from "../lib/live";
import { category, RAIN_RAMP } from "../lib/risk";
import { BarChart, HeatStrip } from "./charts";
import { useUI } from "./Shell";
import { Empty, RiskPill, SkeletonRows } from "./ui";

const KIND_LABEL = { state: "State", city: "City", warehouse: "Fulfilment centre", hub: "Delivery hub", route: "Route" };
const MULT = { none: 1, rain: 1.12, heavy: 1.3, extreme: 1.56 };

function Stat({ l, v, sub }) {
  return <div><div className="l">{l}</div><div className="v">{v}</div>{sub ? <div className="note">{sub}</div> : null}</div>;
}

export default function LocationPanel({ kind, id, name, mapRow, onSelect }) {
  const { openOrder } = useUI();
  const { data, loading, error } = useFetch(`/api/locations/${kind}/${encodeURIComponent(id)}`, { refreshMs: 30000 });
  const live = data?.live || mapRow || {};
  const score = mapRow?.impact_index ?? live.score ?? 0;
  const hist = data?.history;
  const monsoon = hist?.monsoon;
  return (
    <div>
      <div className="eyebrow">{KIND_LABEL[kind]}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0 2px" }}>
        <h2 style={{ fontSize: 27.6, fontWeight: 650, letterSpacing: "-0.02em", margin: 0 }}>{name || id}</h2>
        <RiskPill score={score} category={category(score)} />
      </div>

      <div className="kv" style={{ marginTop: 16 }}>
        <Stat l="Orders, next 12 h" v={num(live.orders)} />
        <Stat l="Weather exposed" v={num(live.exposed)} sub={live.orders ? pct(live.exposed / live.orders, 0) : null} />
        <Stat l="Potential delays" v={num(live.potential_delays ?? (live.avg_delay_min >= 30 ? live.exposed : 0))} />
        <Stat l="Average delay" v={live.avg_delay_min ? `+${mins(live.avg_delay_min)}` : "–"} />
        <Stat l="SLA risk" v={num(live.sla_risk)} />
        <Stat l="In transit now" v={num(live.in_transit)} />
      </div>
      {live.top_route ? <div style={{ marginTop: 12, fontSize: 16.1 }}><span className="muted">Top affected route </span><span className="mono">{live.top_route}</span></div> : null}

      {loading && !data ? <SkeletonRows rows={6} /> : null}
      {error && !data ? <Empty>Details for this location need the live backend.</Empty> : null}

      {kind === "city" && data?.forecast ? (
        <>
          <div className="h3">Rain, next 48 hours <span className="faint" style={{ fontWeight: 400 }}>· now {SEVERITY_LABEL[data.weather?.severity] || "–"}, {data.weather?.rain_mmph ?? 0} mm/h</span></div>
          <HeatStrip cells={data.forecast} color={(c) => RAIN_RAMP[Math.min(4, Math.round(c.p_rain * 4))]}
            tooltip={(c) => <>{pct(c.p_rain, 0)} chance of rain · {SEVERITY_LABEL[c.cls]}</>} />
          <div className="note" style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}><span>now</span><span>+24 h</span><span>+48 h</span></div>
        </>
      ) : null}

      {kind === "city" && data?.climatology?.length ? (
        <>
          <div className="h3">Rainfall climatology, 2015–2025 <span className="faint" style={{ fontWeight: 400 }}>· mm per month</span></div>
          <BarChart data={data.climatology} value={(d) => d.monthly_total_mm} label={(d) => MONTHS[d.month - 1][0]}
            format={(v) => `${Math.round(v)}`} height={130} color={(d) => (d.season === "monsoon" ? "#4d87c4" : "#9fb8d4")}
            tooltip={(d) => <><div className="t">{MONTHS[d.month - 1]} · {d.season}</div>{Math.round(d.monthly_total_mm)} mm · rainy days {pct(d.p_rainy, 0)} · heavy {pct(d.p_heavy, 1)}</>} />
        </>
      ) : null}

      {kind === "route" && data?.route ? (
        <>
          <div className="h3">ETA by weather · {data.route.distance_km} km · sensitivity {data.route.sensitivity}×</div>
          <table className="table">
            <tbody>
              {Object.entries(data.route.eta_by_class_h).map(([cls, h]) => (
                <tr key={cls}><td>{cls === "none" ? "Normal" : SEVERITY_LABEL[cls]}</td>
                  <td className="r mono">{h.toFixed(1)} h</td>
                  <td className="r mono faint">{cls === "none" ? "" : `+${mins((h - data.route.normal_eta_h) * 60)}`}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      {data?.children?.length ? (
        <>
          <div className="h3">{kind === "state" ? "Cities" : kind === "route" ? "Orders on this route (highest risk first)" : "Routes"}</div>
          <table className="table">
            <tbody>
              {kind === "route" ? data.children.map((o) => (
                <tr key={o.id} className="click" onClick={() => openOrder(o.id)}>
                  <td className="mono"><span className="link">{o.id}</span></td>
                  <td>{TIER_LABEL[o.tier]}</td>
                  <td className="num">{istTime(o.planned_dispatch)}</td>
                  <td>{o.score != null ? <RiskPill score={o.score} category={o.category} /> : <span className="tag">{o.status.replace("_", " ")}</span>}</td>
                </tr>
              )) : [...data.children].sort((a, b) => (b.score || 0) - (a.score || 0)).map((c) => (
                <tr key={c.id} className="click" onClick={() => onSelect(kind === "state" ? "city" : "route", c.id)}>
                  <td><span className={`link ${kind === "state" ? "" : "mono"}`}>{c.name || c.code || c.id}</span></td>
                  <td className="r num">{num(c.orders)} orders</td>
                  <td className="r"><RiskPill score={c.score || 0} category={c.category} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      {hist?.last_12_months?.length ? (
        <>
          <div className="h3">Weather cost, last 12 months</div>
          <BarChart data={hist.last_12_months} value={(d) => d.weather_cost || 0} label={(d) => MONTHS[+d.month.slice(5) - 1][0]}
            format={inr} yFormat={(v) => inr(v).replace("₹", "")} height={130}
            color={(d) => ([6, 7, 8, 9].includes(+d.month.slice(5)) ? "#cc5a43" : "#0b0b0c")}
            tooltip={(d) => <><div className="t">{d.month}</div>{inr(d.weather_cost)} · {num(d.affected)} of {num(d.orders)} orders delayed by rain</>} />
          {monsoon?.orders ? (
            <div className="kv" style={{ marginTop: 12 }}>
              <Stat l="Monsoon orders (2021–25)" v={num(monsoon.orders)} />
              <Stat l="Delayed by rain" v={pct(monsoon.affected / monsoon.orders)} />
              <Stat l="Avg weather delay" v={monsoon.avg_delay_min ? `+${mins(monsoon.avg_delay_min)}` : "–"} />
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
