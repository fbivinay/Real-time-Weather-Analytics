"use client";

import { useCallback, useEffect, useState } from "react";

const REFRESH_MS = 5000;

// Fixed scale rather than one derived from the current readings: it keeps the
// five range bars comparable with each other and stops the axis jumping on
// every poll. 10-50C covers the generator's normal band (15-35) and its
// extreme heat band (40-45) with room to spare.
const TEMP_MIN = 10;
const TEMP_MAX = 50;

const ALERT_LABEL = {
  heat: { text: "Heat", cls: "chip-heat" },
  heavy_rain: { text: "Heavy rain", cls: "chip-rain" },
  high_wind: { text: "High wind", cls: "chip-wind" },
};

const UNIT = { temperature: "°C", rainfall: "mm", wind_speed: "km/h", humidity: "%" };

function pct(value) {
  const clamped = Math.max(TEMP_MIN, Math.min(TEMP_MAX, value));
  return ((clamped - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * 100;
}

function one(value) {
  return Number(value).toFixed(1);
}

function clockTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function load(resource) {
  const res = await fetch(`/api/proxy?resource=${resource}`, { cache: "no-store" });
  if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
  return res.json();
}

export default function Page() {
  const [stations, setStations] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [s, a, st] = await Promise.all([
        load("stations"),
        load("alerts"),
        load("stats"),
      ]);
      setStations(s.stations || []);
      setAlerts(a.alerts || []);
      setStats(st);
      setError(null);
      setUpdatedAt(new Date());
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const live = !error;

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <p className="eyebrow">Real-time weather analytics</p>
          <h1>Station Monitor</h1>
        </div>
        <span className={`status ${live ? "status-live" : "status-down"}`}>
          <i className="dot" />
          {live ? `Live · updated ${clockTime(updatedAt?.toISOString())}` : "Cluster unreachable"}
        </span>
      </header>

      {error && (
        <div className="banner">
          <strong>No data from the cluster</strong>
          <p>
            {error}. The pipeline runs on a single EC2 node that is torn down between
            sessions — if it has been destroyed, this page stays up and empty rather than
            breaking.
          </p>
        </div>
      )}

      <div className="tiles">
        <div className="tile">
          <span className="label">Records processed</span>
          <span className="value">{stats ? stats.records_processed.toLocaleString() : "—"}</span>
          <span className="sub">alerts + aggregates into Redis</span>
        </div>
        <div className="tile">
          <span className="label">Stations reporting</span>
          <span className="value">{stats ? stats.stations_tracked : "—"}</span>
          <span className="sub">one reading each per 5 seconds</span>
        </div>
        <div className="tile">
          <span className="label">Alerts buffered</span>
          <span className="value">{stats ? stats.alerts_buffered : "—"}</span>
          <span className="sub">most recent 50 kept</span>
        </div>
        <div className="tile">
          <span className="label">Window</span>
          <span className="value">1<span style={{ fontSize: "1rem", marginLeft: ".2rem" }}>min</span></span>
          <span className="sub">tumbling, per station</span>
        </div>
      </div>

      <section>
        <div className="sec-head">
          <h2>Stations</h2>
          <span className="hint">Latest closed one-minute window · bar spans that window&apos;s low to high, dot is the average</span>
        </div>
        {stations.length === 0 ? (
          <div className="empty">
            Waiting for the first window to close. Aggregates appear about a minute after the
            processor starts.
          </div>
        ) : (
          <div className="stations">
            {stations.map((s) => (
              <article className="station" key={s.station_id}>
                <div className="station-head">
                  <h3>{s.city}</h3>
                  <span className="id">{s.station_id}</span>
                </div>

                <div className="temp-now">
                  {one(s.avg_temperature)}
                  <span className="deg">°C avg</span>
                </div>

                <div className="range">
                  <div className="range-track">
                    <div
                      className="range-fill"
                      style={{
                        left: `${pct(s.min_temperature)}%`,
                        width: `${Math.max(pct(s.max_temperature) - pct(s.min_temperature), 1.5)}%`,
                      }}
                    />
                    <div className="range-dot" style={{ left: `${pct(s.avg_temperature)}%` }} />
                  </div>
                  <div className="range-scale">
                    <span>{one(s.min_temperature)}°</span>
                    <span>{one(s.max_temperature)}°</span>
                  </div>
                </div>

{/* Units live in the labels so the values stay bare numbers and line up
    on their tabular figures - a unit suffixed to one value and not
    another is what made wind look unlabelled. */}
                <div className="metrics">
                  <div className="metric">
                    <span className="m-label">Humidity %</span>
                    <span className="m-value">{Math.round(s.avg_humidity)}</span>
                  </div>
                  <div className="metric">
                    <span className="m-label">Rain mm</span>
                    <span className="m-value">{one(s.avg_rainfall)}</span>
                  </div>
                  <div className="metric">
                    <span className="m-label">Wind km/h</span>
                    <span className="m-value">{one(s.avg_wind_speed)}</span>
                  </div>
                </div>

                <div className="window">
                  {clockTime(s.window_start)}–{clockTime(s.window_end)} · {s.reading_count} readings
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="sec-head">
          <h2>Recent alerts</h2>
          <span className="hint">Fired when a reading crosses 40°C, 50mm or 60km/h</span>
        </div>
        {alerts.length === 0 ? (
          <div className="empty">
            No alerts yet. Extreme readings are injected at roughly a 1-in-30 chance per station
            per cycle, so expect one every minute or two.
          </div>
        ) : (
          <div className="alerts">
            {alerts.map((a, i) => {
              const meta = ALERT_LABEL[a.alert_type] || { text: a.alert_type, cls: "chip-heat" };
              return (
                <div className="alert" key={`${a.timestamp}-${a.station_id}-${i}`}>
                  <span className={`chip ${meta.cls}`}>{meta.text}</span>
                  <span className="where">
                    {a.city}
                    <span>
                      {a.station_id} · {clockTime(a.timestamp)}
                    </span>
                  </span>
                  <span className="reading">
                    {one(a.value)}
                    {UNIT[a.field] || ""}
                    <span className="thr"> / {a.threshold}</span>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <footer>
        <span>Kafka → Spark Structured Streaming → Redis → FastAPI</span>
        <span>Refreshes every {REFRESH_MS / 1000}s</span>
        <span className="mono">github.com/fbivinay/Real-time-Weather-Analytics</span>
      </footer>
    </div>
  );
}
