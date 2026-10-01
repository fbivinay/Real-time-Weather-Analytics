// Risk categories and hazards. Colours live in globals.css (--risk-*), the
// one source for both themes; the map reads them with readRiskColors().

export const CATEGORIES = ["low", "medium", "high", "critical"];

export const CATEGORY_LABEL = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
  unknown: "No data",
};

export const HAZARD_LABEL = {
  rain: "Heavy rain",
  wind: "High wind",
  heat: "Heat",
  fog: "Fog",
};

export const RANK = { unknown: -1, low: 0, medium: 1, high: 2, critical: 3 };

export function categoryOf(location) {
  return location?.assessment?.category ?? "unknown";
}

export function readRiskColors() {
  const css = getComputedStyle(document.documentElement);
  const read = (name) => css.getPropertyValue(name).trim();
  return {
    low: read("--risk-low"),
    medium: read("--risk-medium"),
    high: read("--risk-high"),
    critical: read("--risk-critical"),
    unknown: read("--risk-unknown"),
    land: read("--map-land"),
    neighbour: read("--map-neighbour"),
    water: read("--map-water"),
    border: read("--map-border"),
    state: read("--map-state"),
    route: read("--map-route"),
    ink: read("--ink"),
    surface: read("--surface"),
  };
}
