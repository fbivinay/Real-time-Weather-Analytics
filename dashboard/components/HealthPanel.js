import { num, seconds } from "../lib/format";

const LABEL = {
  api: "API",
  redis: "Redis (live state)",
  engine: "Risk engine",
  spark: "Spark processor",
  ingest: "Ingestion (sensors)",
};

function Status({ value }) {
  const v = value ?? "down";
  return <span className={`status status-${v}`}><i className="dot" aria-hidden="true" />{v}</span>;
}

export default function HealthPanel({ health, engine, dq, connection }) {
  const components = health?.components ?? {};
  const spark = components.spark?.queries?.["features-kafka"];
  const quarantine = Object.entries(dq?.quarantine ?? {}).sort((a, b) => b[1] - a[1]);
  const quarantined = quarantine.reduce((s, [, n]) => s + n, 0);
  return (
    <section className="card" aria-labelledby="health-title">
      <div className="card-head">
        <h2 id="health-title">Data quality and pipeline health</h2>
        <span className="hint">{connection === "recording" ? "As recorded" : "Live from the cluster"}</span>
      </div>
      {health ? (
        <div className="health">
          {Object.keys(LABEL).filter((k) => components[k]).map((k) => (
            <div className="health-row" key={k}>
              <span>{LABEL[k]}</span>
              <Status value={components[k].status} />
            </div>
          ))}
        </div>
      ) : (
        <div className="empty">No health data yet.</div>
      )}
      <div className="metric-grid">
        <div><span className="k">Latency p50</span><span className="v">{seconds(engine?.latency_p50_s)}</span></div>
        <div><span className="k">Latency p95</span><span className="v">{seconds(engine?.latency_p95_s)}</span></div>
        <div><span className="k">Consumer lag</span><span className="v">{num(engine?.consumer_lag)}</span></div>
        <div><span className="k">Spark batch</span><span className="v">{spark ? `${num(spark.batch_ms)} ms` : "—"}</span></div>
        <div><span className="k">Quarantined</span><span className="v">{num(quarantined)}</span></div>
        <div><span className="k">Late dropped</span><span className="v">{num(dq?.dropped_late)}</span></div>
        <div><span className="k">Duplicates</span><span className="v">{num(dq?.dropped_duplicates)}</span></div>
        <div><span className="k">Missing</span><span className="v">{num(dq?.missing_readings)}</span></div>
      </div>
      {quarantine.length > 0 && (
        <p className="muted" style={{ fontSize: ".78rem" }}>
          Quarantine reasons: {quarantine.map(([r, n]) => `${r.replaceAll("_", " ")} ${n}`).join(" · ")}
        </p>
      )}
      <div>
        <h3 style={{ fontSize: ".86rem", marginBottom: ".35rem" }}>Sensors needing attention</h3>
        {(dq?.suspect?.length ?? 0) + (dq?.stale?.length ?? 0) === 0 ? (
          <p className="muted" style={{ fontSize: ".8rem" }}>All hub sensors reporting and consistent.</p>
        ) : (
          <ul className="suspects">
            {(dq?.suspect ?? []).map((s) => (
              <li key={s.station_id}>
                <span className="mono">{s.station_id}</span> suspect: {s.reason} on {s.field?.replace("_", " ")}
              </li>
            ))}
            {(dq?.stale ?? []).map((sid) => (
              <li key={sid}><span className="mono">{sid}</span> silent (missing readings)</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
