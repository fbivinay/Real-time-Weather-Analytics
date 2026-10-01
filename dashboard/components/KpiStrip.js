import { num } from "../lib/format";

export default function KpiStrip({ kpis }) {
  const k = kpis ?? {};
  const routes = k.routes_affected ?? { linehaul: 0, lastmile: 0 };
  const routesTotal = (routes.linehaul ?? 0) + (routes.lastmile ?? 0);
  const tiles = [
    { label: "Active incidents", value: num(k.active_incidents), sub: "regions at High or above", alert: k.active_incidents > 0 },
    { label: "Locations High+", value: num(k.locations_high), sub: "stations scoring 50 or more", alert: k.locations_high > 0 },
    { label: "Routes affected", value: num(routesTotal), sub: `${num(routes.linehaul)} linehaul · ${num(routes.lastmile)} last-mile`, alert: routesTotal > 0 },
    { label: "Deliveries at risk", value: num(k.deliveries_at_risk), sub: `of ${num(k.deliveries_active)} in progress`, alert: k.deliveries_at_risk > 0 },
    { label: "Hubs affected", value: num(k.hubs_affected), sub: "of 25 hubs", alert: k.hubs_affected > 0 },
  ];
  return (
    <div className="kpis">
      {tiles.map((t) => (
        <div key={t.label} className={`kpi${t.alert ? " alert" : ""}`}>
          <span className="label">{t.label}</span>
          <span className="value">{kpis ? t.value : "—"}</span>
          <span className="sub">{t.sub}</span>
        </div>
      ))}
    </div>
  );
}
