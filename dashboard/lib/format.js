// Display helpers. Weather time is shown in IST: the operators are in India.

const IST = "Asia/Kolkata";

export function istClock(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit" });
}

export function istDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: IST, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function seconds(value) {
  if (value === null || value === undefined) return "—";
  return value >= 120 ? `${Math.round(value / 60)} min` : `${Math.round(value)} s`;
}

export const SCENARIO_NAMES = {
  "montha-2025": "Cyclone Montha, Oct 2025",
  "michaung-2023": "Cyclone Michaung, Dec 2023",
  "fog-north-2025": "Dense fog, north India, Dec 2025",
  "heatwave-2024": "Heatwave, May 2024",
  "gujarat-rain-2024": "Extreme rain, Gujarat, Aug 2024",
  "storm-chennai": "Severe thunderstorm, Chennai",
  "monsoon-mumbai": "Monsoon bands, Mumbai",
  "fog-north": "Dense fog, Delhi NCR",
  "heatwave-north": "Heatwave, north-west plains",
};

export function scenarioName(id) {
  return SCENARIO_NAMES[id] ?? id ?? "";
}
