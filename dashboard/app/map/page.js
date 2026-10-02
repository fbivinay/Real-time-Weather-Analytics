"use client";
// 02 Map — where is the problem? India -> state -> city -> warehouse/hub -> route -> orders.
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import IndiaMap, { loadGeo } from "../../components/IndiaMap";
import LocationPanel from "../../components/LocationPanel";
import { Empty, RiskPill, SkeletonRows } from "../../components/ui";
import { MONTHS, mins, num, pct } from "../../lib/format";
import { useFetch, useLive } from "../../lib/live";
import { category, IMPACT_RAMP, IMPACT_STOPS, impactColor, RAIN_RAMP, RAIN_STOPS, rainColor, RISK_HEX } from "../../lib/risk";

function useNetwork() {
  const [net, setNet] = useState(null);
  useEffect(() => {
    loadGeo("/network.geojson").then((g) => {
      const by = { city: {}, warehouse: {}, hub: {}, route: {} };
      g.features.forEach((f) => { by[f.properties.layer][f.properties.id] = { ...f.properties, coords: f.geometry.coordinates }; });
      setNet(by);
    });
  }, []);
  return net;
}

function Legend({ mode }) {
  const ramp = mode === "rainfall" ? RAIN_RAMP : IMPACT_RAMP;
  const stops = mode === "rainfall" ? RAIN_STOPS : IMPACT_STOPS;
  const fmt = mode === "rainfall" ? (v) => `${v}` : (v) => `${v}`;
  return (
    <div className="map-legend">
      <span>{mode === "rainfall" ? "Average rainfall, mm/month" : "Delivery impact index"}</span>
      {ramp.map((c, i) => (
        <span key={c} className="sw"><i style={{ background: c }} />{i < stops.length - 1 ? `${fmt(stops[i])}–${fmt(stops[i + 1])}` : `${fmt(stops[i])}+`}</span>
      ))}
      <span className="sw"><i style={{ background: "#0b0b0c", borderRadius: 99 }} />FC</span>
    </div>
  );
}

function Tooltip({ hover, mode, data, net }) {
  if (!hover || !net) return null;
  const p = hover.props;
  let body = null;
  if (hover.layer === "state-fill") {
    const s = mode === "rainfall" ? data?.statesRain?.[p.name] : data?.states?.[p.name];
    body = s ? (mode === "rainfall"
      ? <><b>{p.name}</b><div className="muted">{Math.round(s.monthly_total_mm)} mm in a typical {data.monthName} · rainy days {pct(s.p_rainy, 0)}</div></>
      : <><b>{p.name}</b><div className="muted">Impact {Math.round(s.impact_index)} · {num(s.orders)} orders next 12 h · {num(s.exposed)} exposed</div></>)
      : <><b>{p.name}</b><div className="muted">Outside the ShopFlow network</div></>;
  } else if (hover.layer === "cities") {
    const c = data?.cities?.[p.id];
    body = <><b>{p.name}</b><div className="muted">{mode === "rainfall" ? `${Math.round(p.mm)} mm in a typical ${data?.monthName}` :
      c ? `Impact ${Math.round(c.impact_index)} · ${num(c.orders)} orders · ${c.avg_delay_min ? `+${mins(c.avg_delay_min)}` : "no delay"}` : ""}</div></>;
  } else if (hover.layer === "routes-hit") {
    const r = data?.routes?.[p.id];
    body = <><b className="mono">{p.code}</b><div className="muted">{Math.round(p.distance_km)} km · {r ? `${num(r.orders)} orders · +${mins(r.avg_delay_min)}` : "no orders next 12 h"}</div></>;
  } else if (hover.layer === "warehouses" || hover.layer === "hubs") {
    body = <><b>{p.name}</b><div className="muted">{hover.layer === "warehouses" ? "Fulfilment centre" : "Delivery hub"} · {p.city_id}</div></>;
  }
  return body ? <div className="map-tip" style={{ left: hover.x, top: hover.y }}>{body}</div> : null;
}

function IndiaPanel({ data, mode, onSelect }) {
  if (!data) return <SkeletonRows rows={10} />;
  const rows = mode === "rainfall"
    ? Object.entries(data.statesRain || {}).map(([id, s]) => ({ id, v: s.monthly_total_mm, sub: `rainy days ${pct(s.p_rainy, 0)}` }))
    : Object.values(data.states || {}).map((s) => ({ id: s.id, v: s.impact_index, sub: `${num(s.orders)} orders · ${num(s.exposed)} exposed` }));
  rows.sort((a, b) => b.v - a.v);
  return (
    <div>
      
      <h2 style={{ fontSize: 27.6, fontWeight: 650, letterSpacing: "-0.02em", margin: "4px 0 2px" }}>
        {mode === "rainfall" ? `Where it rains in ${data.monthName}` : "Where rain hurts deliveries"}
      </h2>
      <table className="table" style={{ marginTop: 12 }}>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="click" onClick={() => onSelect("state", r.id)}>
              <td><span className="link">{r.id}</span><div className="note">{r.sub}</div></td>
              <td className="r">{mode === "rainfall" ? <span className="mono">{Math.round(r.v)} mm</span> : <RiskPill score={r.v} category={category(r.v)} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MapPage() {
  const router = useRouter();
  const params = useSearchParams();
  const live = useLive();
  const net = useNetwork();
  const [mode, setMode] = useState("impact");
  const simMonth = live.overview ? new Date(live.overview.sim_time).getUTCMonth() + 1 : 7;
  const [month, setMonth] = useState(null);
  const m = month || simMonth;
  const [hover, setHover] = useState(null);
  const [showRoutes, setShowRoutes] = useState(true);
  const [showHubs, setShowHubs] = useState(true);
  const impact = useFetch("/api/map?mode=impact", { refreshMs: 20000 });
  const rain = useFetch(`/api/map?mode=rainfall&month=${m}`);

  const sel = useMemo(() => {
    const raw = params.get("sel");
    if (!raw) return null;
    const i = raw.indexOf(":");
    return { kind: raw.slice(0, i), id: raw.slice(i + 1) };
  }, [params]);
  const select = useCallback((kind, id) => {
    router.replace(kind ? `/map?sel=${kind}:${encodeURIComponent(id)}` : "/map", { scroll: false });
  }, [router]);

  const data = useMemo(() => {
    const d = impact.data;
    const r = rain.data;
    return {
      states: d?.states, cities: d?.cities, routes: d?.routes, warehouses: d?.warehouses, hubs: d?.hubs,
      statesRain: r ? Object.fromEntries(r.states.map((s) => [s.id, s])) : null,
      citiesRain: r ? Object.fromEntries(r.cities.map((c) => [c.id, c])) : null,
      monthName: MONTHS[m - 1],
    };
  }, [impact.data, rain.data, m]);

  const stateColors = useMemo(() => {
    if (mode === "rainfall") return data.statesRain ? Object.fromEntries(Object.entries(data.statesRain).map(([k, s]) => [k, rainColor(s.monthly_total_mm)])) : null;
    return data.states ? Object.fromEntries(Object.values(data.states).map((s) => [s.id, impactColor(s.impact_index)])) : null;
  }, [mode, data]);

  const cityFeatures = useMemo(() => {
    if (!net) return [];
    return Object.values(net.city).map((c) => {
      const imp = data.cities?.[c.id];
      const rn = data.citiesRain?.[c.id];
      const orders = imp?.orders || 0;
      const color = mode === "rainfall" ? rainColor(rn?.monthly_total_mm || 0) : RISK_HEX[category(imp?.impact_index || 0)];
      const r = mode === "rainfall" ? 4 + Math.sqrt(rn?.monthly_total_mm || 0) / 2.6 : 4 + Math.sqrt(orders) / 2.4;
      return { type: "Feature", geometry: { type: "Point", coordinates: c.coords },
        properties: { id: c.id, name: c.name, color, r: Math.min(22, r), mm: rn?.monthly_total_mm || 0 } };
    });
  }, [net, data, mode]);

  const routeColors = useMemo(() => {
    if (mode !== "impact" || !data.routes) return null;
    return Object.fromEntries(Object.values(data.routes).filter((r) => r.score >= 25).map((r) => [r.id, RISK_HEX[r.category]]));
  }, [mode, data.routes]);

  const selected = useMemo(() => {
    if (!sel || !net) return sel;
    const c = sel.kind === "city" ? net.city[sel.id] : sel.kind === "warehouse" ? net.warehouse[sel.id] : sel.kind === "hub" ? net.hub[sel.id] : null;
    return c ? { ...sel, lngLat: c.coords } : sel;
  }, [sel, net]);

  // Breadcrumb: India > state > city > route / FC / hub.
  const crumbs = useMemo(() => {
    if (!sel || !net) return [];
    const out = [];
    const cityOf = (cid) => net.city[cid];
    if (sel.kind === "state") out.push(["state", sel.id, sel.id]);
    if (sel.kind === "city") { const c = cityOf(sel.id); if (c) out.push(["state", c.state, c.state], ["city", c.id, c.name]); }
    if (sel.kind === "route") { const r = net.route[sel.id]; const c = r && cityOf(r.city_id); if (c) out.push(["state", c.state, c.state], ["city", c.id, c.name], ["route", r.id, r.code]); }
    if (sel.kind === "warehouse" || sel.kind === "hub") { const w = net[sel.kind][sel.id]; const c = w && cityOf(w.city_id); if (c) out.push(["state", c.state, c.state], ["city", c.id, c.name], [sel.kind, w.id, w.name]); }
    return out;
  }, [sel, net]);
  const selName = crumbs.length ? crumbs[crumbs.length - 1][2] : sel?.id;
  const focus = useMemo(() => {
    if (!sel || !crumbs.length) return null;
    return { state: crumbs[0]?.[1], city: crumbs.find((c) => c[0] === "city")?.[1] };
  }, [sel, crumbs]);
  const mapRow = sel ? ({ state: data.states, city: data.cities, route: data.routes, warehouse: data.warehouses, hub: data.hubs }[sel.kind] || {})[sel.id] : null;

  return (
    <main className="page" style={{ paddingTop: 22 }}>
      <div className="pagehead" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="headline" style={{ fontSize: 32.2 }}>{mode === "rainfall" ? `Rainfall pattern in ${MONTHS[m - 1]}` : "Weather → delivery impact across India"}</h1>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <div className="seg">
            <button type="button" className={mode === "impact" ? "on" : ""} onClick={() => setMode("impact")}>Delivery impact</button>
            <button type="button" className={mode === "rainfall" ? "on" : ""} onClick={() => setMode("rainfall")}>Historical rainfall</button>
          </div>
          {mode === "rainfall" ? (
            <select className="select" value={m} onChange={(e) => setMonth(+e.target.value)} aria-label="Month">
              {MONTHS.map((n, i) => <option key={n} value={i + 1}>{n}</option>)}
            </select>
          ) : null}
          <label className="toggle"><input type="checkbox" checked={showRoutes} onChange={(e) => setShowRoutes(e.target.checked)} />Routes</label>
          <label className="toggle"><input type="checkbox" checked={showHubs} onChange={(e) => setShowHubs(e.target.checked)} />Hubs</label>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 440px", gap: 18, alignItems: "start" }}>
        <div className="mapwrap" style={{ height: "calc(100vh - 230px)", minHeight: 560 }}>
          <Legend mode={mode} />
          <IndiaMap stateColors={stateColors} cities={cityFeatures} routeColors={routeColors} selected={selected}
            focus={focus} onSelect={select} onHover={setHover} showRoutes={showRoutes} showHubs={showHubs} />
          <Tooltip hover={hover} mode={mode} data={data} net={net} />
          {impact.error && !impact.data ? <div className="map-tip" style={{ left: 16, top: 70 }}>Map data needs the live backend or a demo snapshot.</div> : null}
        </div>
        <div className="panel side">
          <div className="panel-b" style={{ paddingTop: 18 }}>
            <div className="crumbs" style={{ marginBottom: 12 }}>
              <button type="button" onClick={() => select(null)}>India</button>
              {crumbs.map(([k, id, label], i) => (
                <span key={k + id} style={{ display: "contents" }}>
                  <span>›</span>
                  {i === crumbs.length - 1 ? <span style={{ color: "var(--ink)" }}>{label}</span>
                    : <button type="button" onClick={() => select(k, id)}>{label}</button>}
                </span>
              ))}
            </div>
            {sel ? <LocationPanel key={`${sel.kind}:${sel.id}`} kind={sel.kind} id={sel.id} name={selName} mapRow={mapRow} onSelect={select} />
              : <IndiaPanel data={impact.data || rain.data ? data : null} mode={mode} onSelect={select} />}
            {!sel && impact.error && !impact.data ? <Empty>Impact data unavailable.</Empty> : null}
          </div>
        </div>
      </div>
    </main>
  );
}

export default function Page() {
  return <Suspense fallback={<main className="page"><SkeletonRows rows={12} /></main>}><MapPage /></Suspense>;
}
