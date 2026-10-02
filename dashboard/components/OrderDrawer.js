"use client";
import { useEffect } from "react";

import { CATEGORY_LABEL, inr, istTime, mins, pct, SEVERITY_LABEL, TIER_LABEL, TYPE_LABEL } from "../lib/format";
import { useFetch } from "../lib/live";
import { RISK_HEX } from "../lib/risk";
import { Empty, RiskPill, ScoreBar, SkeletonRows } from "./ui";

const MULTIPLIER = { none: 1, rain: 1.12, heavy: 1.3, extreme: 1.56 };

export function Contributions({ prediction }) {
  const color = RISK_HEX[prediction.category];
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 34.5, fontWeight: 650, letterSpacing: "-0.02em" }}>{Math.round(prediction.score)}</span>
        <span className="muted">/ 100 risk score =</span>
      </div>
      <div style={{ marginBottom: 14 }}><ScoreBar contributions={prediction.contributions} score={prediction.score} /></div>
      <div className="contrib">
        {prediction.contributions.map((c) => (
          <div key={c.key} style={{ display: "contents" }}>
            <span>{c.label}</span>
            <div className="bar"><i style={{ width: `${(c.points / c.cap) * 100}%`, background: color }} /></div>
            <span className="mono" style={{ textAlign: "right" }}>+{c.points.toFixed(1)}</span>
            <span className="d">{c.detail} <span className="faint">(max {c.cap})</span></span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function OrderDrawer({ id, onClose }) {
  const { data: o, loading, error } = useFetch(`/api/orders/${encodeURIComponent(id)}`);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const p = o?.prediction;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`Order ${id}`}>
        <button type="button" className="x" onClick={onClose} aria-label="Close">×</button>
        <div className="drawer-h">
          <div className="eyebrow">Order</div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 4 }}>
            <span className="mono" style={{ fontSize: 25.3, fontWeight: 600 }}>{id}</span>
            {p ? <RiskPill score={p.score} category={p.category} /> : null}
            {o ? <span className="tag">{o.status.replace("_", " ")}</span> : null}
          </div>
          {o ? <div className="muted" style={{ marginTop: 4 }}>{o.route} · {o.warehouse} → {o.hub}, {o.state}</div> : null}
        </div>
        <div className="drawer-b">
          {loading ? <SkeletonRows rows={10} /> : null}
          {error ? <Empty>{error.status === 404 ? "This order is no longer in the live window (delivered orders are kept for two days)." : "Order details need the live backend."}</Empty> : null}
          {o ? (
            <>
              <div className="kv">
                <div><div className="l">Planned dispatch</div><div className="v">{istTime(o.planned_dispatch)}</div></div>
                <div><div className="l">Promised by (SLA)</div><div className="v">{istTime(o.promised_at)}</div></div>
                <div><div className="l">Service</div><div className="v">{TIER_LABEL[o.tier]}</div></div>
                <div><div className="l">Category</div><div className="v">{o.category || CATEGORY_LABEL[o.category_id]}</div></div>
                <div><div className="l">Order value</div><div className="v">{inr(o.value_inr)}</div></div>
                <div><div className="l">Distance</div><div className="v">{Math.round(o.distance_km)} km</div></div>
              </div>

              {p ? (
                <>
                  <div className="h3">Why this order is at risk</div>
                  <div className="kv" style={{ marginBottom: 16 }}>
                    <div><div className="l">Expected weather</div><div className="v">{SEVERITY_LABEL[p.expected_class]}</div></div>
                    <div><div className="l">Expected delay</div><div className="v">+{mins(p.expected_delay_min)}</div></div>
                    <div><div className="l">SLA breach probability</div><div className="v">{pct(p.p_breach, 0)}</div></div>
                  </div>
                  <Contributions prediction={p} />
                </>
              ) : o.status === "delivered" ? (
                <>
                  <div className="h3">Outcome</div>
                  <div className="kv">
                    <div><div className="l">Delivered</div><div className="v">{istTime(o.delivered_at)}</div></div>
                    <div><div className="l">Weather on trip</div><div className="v">{SEVERITY_LABEL[o.weather_class] || "–"}</div></div>
                    <div><div className="l">Delay vs plan</div><div className="v">{mins(o.delay_min)}</div></div>
                  </div>
                </>
              ) : <div className="note" style={{ marginTop: 16 }}>Risk is scored for orders not yet dispatched.</div>}

              <div className="h3">ETA on this route by weather</div>
              <table className="table">
                <thead><tr><th>Conditions</th><th className="r">ETA</th><th className="r">Weather delay</th></tr></thead>
                <tbody>
                  {Object.entries(MULTIPLIER).map(([cls, m]) => {
                    const eta = o.normal_eta_h * (1 + (m - 1) * o.sensitivity);
                    return (
                      <tr key={cls} className={p?.expected_class === cls ? "sel" : ""}>
                        <td>{cls === "none" ? "Normal (dry)" : SEVERITY_LABEL[cls]}</td>
                        <td className="r mono">{eta.toFixed(1)} h</td>
                        <td className="r mono">{cls === "none" ? "–" : `+${mins((eta - o.normal_eta_h) * 60)}`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {o.items?.length ? (
                <>
                  <div className="h3">Items</div>
                  <table className="table"><tbody>
                    {o.items.map((it) => (
                      <tr key={it.product_id}><td>{it.name}</td><td className="r mono">{it.qty} × {inr(it.price_inr)}</td></tr>
                    ))}
                  </tbody></table>
                </>
              ) : null}

              {o.events?.length ? (
                <>
                  <div className="h3">Event stream</div>
                  <table className="table"><tbody>
                    {o.events.map((e, i) => (
                      <tr key={i}><td>{TYPE_LABEL[e.type] || e.type}</td><td className="r mono">{istTime(e.sim_time)}</td></tr>
                    ))}
                  </tbody></table>
                </>
              ) : null}
            </>
          ) : null}
        </div>
      </aside>
    </>
  );
}
