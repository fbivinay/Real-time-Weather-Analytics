"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import HealthPanel from "../components/HealthPanel";
import IncidentFeed from "../components/IncidentFeed";
import KpiStrip from "../components/KpiStrip";
import LocationPanel from "../components/LocationPanel";
import ModeBadge from "../components/ModeBadge";
import OpsMap from "../components/OpsMap";
import { CATEGORY_LABEL, HAZARD_LABEL, categoryOf } from "../lib/categories";
import { scenarioName } from "../lib/format";
import { useFeed } from "../lib/feed";

const HISTORY_POINTS = 120;

function Legend() {
  return (
    <div className="map-legend" aria-hidden="true">
      {["critical", "high", "medium", "low"].map((c) => (
        <span className="legend-row" key={c}>
          <i className="swatch" style={{ background: `var(--risk-${c})` }} />
          {CATEGORY_LABEL[c]}
        </span>
      ))}
      <span className="legend-row"><i className="swatch-line" style={{ background: "var(--risk-high)" }} />Affected route</span>
      <span className="legend-row"><i className="swatch-square" />Logistics hub</span>
      <span className="legend-row"><i className="swatch-ring" />Suspect sensor (excluded)</span>
    </div>
  );
}

function TopLocations({ ranked, onSelect }) {
  return (
    <section className="card" aria-labelledby="top-title">
      <div className="card-head">
        <h2 id="top-title">Highest-risk locations</h2>
        <span className="hint">Table view of the map · select a row for detail</span>
      </div>
      {ranked.length === 0 ? (
        <div className="empty">Waiting for the first feature windows (about a minute after the pipeline starts).</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr><th>Location</th><th>Risk</th><th>Score</th><th>Hazard</th><th>Source</th></tr>
            </thead>
            <tbody>
              {ranked.map((l) => (
                <tr key={l.station_id}>
                  <td>
                    <button type="button" className="linklike" onClick={() => onSelect(l.station_id)}>
                      {l.name || l.station_id}
                    </button>
                  </td>
                  <td><span className={`chip chip-${categoryOf(l)}`}>{CATEGORY_LABEL[categoryOf(l)]}</span></td>
                  <td className="mono">{l.assessment.score}</td>
                  <td>{HAZARD_LABEL[l.assessment.hazard] ?? "—"}</td>
                  <td className="muted">{l.kind === "reference" ? "City reference" : "Hub sensor"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function Page() {
  const { state, connection, recording } = useFeed();
  const [selected, setSelected] = useState(null);
  const history = useRef({});

  useEffect(() => {
    history.current = {};
    setSelected(null);
  }, [state.mode?.source, state.mode?.scenario]);

  useEffect(() => {
    for (const [sid, loc] of Object.entries(state.locations)) {
      const points = (history.current[sid] ??= []);
      const last = points[points.length - 1];
      if (!last || last.t !== loc.observed_at) {
        points.push({ t: loc.observed_at, score: loc.assessment?.score ?? null });
        if (points.length > HISTORY_POINTS) points.shift();
      }
    }
  }, [state.locations]);

  const ranked = useMemo(
    () => Object.values(state.locations).filter((l) => l.assessment).sort((a, b) => b.assessment.score - a.assessment.score),
    [state.locations],
  );
  const focus = state.locations[selected] ?? ranked[0] ?? null;

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <p className="eyebrow">WeatherOps · weather risk and operations intelligence</p>
          <h1>Where weather will hit operations next</h1>
          <p className="lede">
            Risk for 40 Indian cities and 25 logistics hubs, mapped onto linehaul corridors, last-mile routes
            and live deliveries, with recommended actions for dispatch, fleet and warehouse teams.
          </p>
        </div>
        <ModeBadge mode={state.mode} connection={connection} />
      </header>

      {connection === "recording" && (
        <div className="banner">
          <strong>Showing a recording.</strong> The cluster runs on demand to keep costs near zero; this is a
          captured run of <em>{scenarioName(recording)}</em> through the full pipeline (Kafka, Spark, risk engine).
        </div>
      )}
      {connection === "offline" && (
        <div className="banner">
          <strong>Cluster unreachable and no recording available.</strong> Start it with <span className="mono">./deploy.sh</span>.
        </div>
      )}

      <KpiStrip kpis={state.kpis} />

      <div className="grid-main">
        <section className="card map-card" aria-labelledby="map-title">
          <div className="card-head">
            <h2 id="map-title">Risk map</h2>
            <span className="hint">Stations sized and coloured by risk · routes coloured when Medium or above</span>
          </div>
          <div style={{ position: "relative" }}>
            <OpsMap
              locations={state.locations}
              routes={state.routes}
              incidents={state.incidents}
              selected={focus?.station_id}
              onSelect={setSelected}
            />
            <Legend />
          </div>
        </section>
        <IncidentFeed incidents={state.incidents} lastEvent={state.lastEvent} onSelectRegion={(city) => setSelected(`REF-${city}`)} />
      </div>

      <div className="grid-lower">
        <LocationPanel location={focus} history={focus ? history.current[focus.station_id] : null} />
        <HealthPanel health={state.health} engine={state.engine} dq={state.dq} connection={connection} />
      </div>

      <TopLocations ranked={ranked.slice(0, 10)} onSelect={setSelected} />

      <footer>
        <span>Kafka → Spark Structured Streaming → risk engine → Redis → FastAPI WebSocket</span>
        <span>Weather data by <a href="https://open-meteo.com/">Open-Meteo.com</a> (CC BY 4.0)</span>
        <span>Basemap: Natural Earth</span>
        <span>Hub sensors and the logistics network are simulated</span>
        <span className="mono">github.com/fbivinay/Real-time-Weather-Analytics</span>
      </footer>
    </div>
  );
}
