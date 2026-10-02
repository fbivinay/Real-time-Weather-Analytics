// Risk categories and colour ramps (tokens live in globals.css; hex mirrors for MapLibre).

export const CATEGORIES = ["Low", "Medium", "High", "Critical"];
export const RISK_HEX = { Low: "#8d97a1", Medium: "#bf9200", High: "#d13f14", Critical: "#94155a" };
export const IMPACT_RAMP = ["#f7ece9", "#f0c9bf", "#e39581", "#cc5a43", "#9b2418"];
export const RAIN_RAMP = ["#eef4fa", "#c9dcef", "#8db7df", "#4d87c4", "#1f4f8c"];

export function category(score) {
  if (score >= 75) return "Critical";
  if (score >= 50) return "High";
  if (score >= 25) return "Medium";
  return "Low";
}

// Impact index 0-100 -> ramp step (same edges as the risk categories, plus a 10 split for "barely").
export const IMPACT_STOPS = [0, 10, 25, 50, 75];
export function impactColor(v) {
  let i = 0;
  IMPACT_STOPS.forEach((s, k) => { if (v >= s) i = k; });
  return IMPACT_RAMP[i];
}

// Monthly rainfall (mm) -> ramp step.
export const RAIN_STOPS = [0, 50, 150, 300, 600];
export function rainColor(mm) {
  let i = 0;
  RAIN_STOPS.forEach((s, k) => { if (mm >= s) i = k; });
  return RAIN_RAMP[i];
}
