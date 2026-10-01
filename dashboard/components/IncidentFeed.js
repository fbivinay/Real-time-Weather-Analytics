import { CATEGORY_LABEL, HAZARD_LABEL, RANK } from "../lib/categories";
import { istClock, num } from "../lib/format";

const MAX_ACTIONS = 4;

function Incident({ incident, flash, onSelectRegion }) {
  const cat = incident.category ?? "high";
  const actions = incident.actions ?? [];
  const lastmile = (incident.routes ?? []).filter((r) => r.startsWith("LM-")).length;
  const linehaul = (incident.routes ?? []).length - lastmile;
  return (
    <article className={`incident ${cat}${flash ? " flash" : ""}`} aria-label={`${incident.region_name} incident`}>
      <div className="incident-head">
        <h3>
          <button type="button" className="linklike" onClick={() => onSelectRegion?.(incident.region)}>
            {incident.region_name ?? incident.region}
          </button>
        </h3>
        <span className={`chip chip-${cat}`}>{CATEGORY_LABEL[cat]} {incident.score}</span>
        <span className="chip chip-plain">{HAZARD_LABEL[incident.hazard] ?? incident.hazard}</span>
        {incident.status === "escalated" && <span className="chip chip-plain">Escalated</span>}
      </div>
      <p className="impact">
        <strong>{num(incident.deliveries_at_risk)}</strong> deliveries at risk ·{" "}
        <strong>{lastmile}</strong> last-mile, <strong>{linehaul}</strong> linehaul routes
        {(incident.hubs ?? []).length ? <> · <strong>{incident.hubs.length}</strong> hub</> : null}
        <span className="muted"> · since {istClock(incident.opened_at)} IST, peak {incident.peak_score}</span>
      </p>
      {actions.length > 0 && (
        <ul className="actions">
          {actions.slice(0, MAX_ACTIONS).map((a) => (
            <li key={a.id} className="action">
              <span className={`prio prio-${a.priority}`} title={`Priority ${a.priority}`}>{a.priority}</span>
              <span>{a.text}</span>
              <span className="why">{a.owner} · {a.because}</span>
            </li>
          ))}
          {actions.length > MAX_ACTIONS && (
            <li className="muted" style={{ fontSize: ".78rem" }}>+{actions.length - MAX_ACTIONS} more recommendations</li>
          )}
        </ul>
      )}
      {(incident.timeline ?? []).length > 1 && (
        <div className="timeline">
          {incident.timeline.slice(-3).map((t, i) => (
            <span key={i}>{istClock(t.at)} {t.reason}</span>
          ))}
        </div>
      )}
    </article>
  );
}

function PreAlert({ alert, onSelectRegion }) {
  const action = alert.actions?.[0];
  return (
    <article className="incident prealert" aria-label={`${alert.region_name} developing risk`}>
      <div className="incident-head">
        <h3>
          <button type="button" className="linklike" onClick={() => onSelectRegion?.(alert.region)}>
            {alert.region_name}
          </button>
        </h3>
        <span className={`chip chip-${alert.forecast_category}`}>{CATEGORY_LABEL[alert.forecast_category]} in 60 min</span>
        {alert.hazard && <span className="chip chip-plain">{HAZARD_LABEL[alert.hazard] ?? alert.hazard}</span>}
      </div>
      {action && (
        <ul className="actions">
          <li className="action">
            <span className={`prio prio-${action.priority}`}>{action.priority}</span>
            <span>{action.text}</span>
            <span className="why">{action.owner} · {action.because}</span>
          </li>
        </ul>
      )}
    </article>
  );
}

export default function IncidentFeed({ incidents, prealerts, lastEvent, onSelectRegion }) {
  const sorted = [...(incidents ?? [])].sort(
    (a, b) => (RANK[b.category] ?? 0) - (RANK[a.category] ?? 0) || (b.score ?? 0) - (a.score ?? 0),
  );
  const flashId = lastEvent && Date.now() - lastEvent.at < 4000 ? lastEvent.incident.id : null;
  return (
    <section className="card" aria-labelledby="incidents-title">
      <div className="card-head">
        <h2 id="incidents-title">Incidents and recommended actions</h2>
        <span className="hint">One per region · opens at High, resolves after 10 calm minutes</span>
      </div>
      <div className="feed" aria-live="polite">
        {sorted.length === 0 ? (
          <div className="empty">No active incidents: every region is below High risk.</div>
        ) : (
          sorted.map((inc) => (
            <Incident key={inc.id} incident={inc} flash={inc.id === flashId} onSelectRegion={onSelectRegion} />
          ))
        )}
        {(prealerts ?? []).length > 0 && (
          <>
            <h3 className="feed-sub">Developing in the next hour (forecast)</h3>
            {prealerts.map((a) => <PreAlert key={a.region} alert={a} onSelectRegion={onSelectRegion} />)}
          </>
        )}
      </div>
    </section>
  );
}
