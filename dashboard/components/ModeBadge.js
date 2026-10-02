import { istDateTime, scenarioName } from "../lib/format";

const CONNECTION = {
  live: { cls: "badge-live", text: "Live" },
  connecting: { cls: "", text: "Connecting…" },
  recording: { cls: "badge-recording", text: "Recording" },
  offline: { cls: "badge-offline", text: "Offline" },
};

export default function ModeBadge({ mode, connection }) {
  const conn = CONNECTION[connection] ?? CONNECTION.offline;
  let source = "No data yet";
  if (mode?.source === "live") source = "Open-Meteo, real time";
  if (mode?.source === "replay") source = `Replay · ${scenarioName(mode.scenario)}`;
  if (mode?.source === "sim") source = `Simulation · ${scenarioName(mode.scenario)}`;

  return (
    <div className="badges" aria-live="polite">
      <span className={`badge ${conn.cls}`}>
        <i className="dot" aria-hidden="true" />
        <strong>{conn.text}</strong>
      </span>
      <span className="badge">
        <strong>{mode?.source?.toUpperCase() ?? "—"}</strong> {source}
        {mode?.speed && mode.speed > 1 ? ` · ${mode.speed}×` : ""}
      </span>
      {mode?.observed_at && (
        <span className="badge" title="Weather time (IST) the risk is computed for">
          Weather clock {istDateTime(mode.observed_at)}
        </span>
      )}
    </div>
  );
}
