// Number, money and time formatting (Indian conventions: lakh/crore grouping, IST).

const IN = new Intl.NumberFormat("en-IN");

export function num(n, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return digits ? Number(n).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : IN.format(Math.round(n));
}

export function inr(n) {
  if (n === null || n === undefined) return "–";
  const a = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${IN.format(Math.round(a))}`;
}

export function pct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return `${(x * 100).toFixed(digits)}%`;
}

export function mins(m) {
  if (m === null || m === undefined) return "–";
  const v = Math.round(m);
  if (Math.abs(v) < 60) return `${v} min`;
  const h = Math.floor(Math.abs(v) / 60);
  const r = Math.abs(v) % 60;
  return `${v < 0 ? "−" : ""}${h} h${r ? ` ${r} min` : ""}`;
}

const IST = { timeZone: "Asia/Kolkata" };

export function istTime(iso, opts = {}) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { ...IST, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    hour12: false, ...opts });
}

export function istHour(iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString("en-IN", { ...IST, hour: "2-digit", minute: "2-digit", hour12: false });
}

export function istDay(iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleDateString("en-IN", { ...IST, weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

export function ago(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${Math.round(seconds)} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

export function hoursFrom(iso, nowIso) {
  if (!iso || !nowIso) return null;
  return (new Date(iso) - new Date(nowIso)) / 3.6e6;
}

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const TYPE_LABEL = {
  ORDER_CREATED: "Order created", ORDER_ASSIGNED: "Order assigned", VEHICLE_DISPATCHED: "Vehicle dispatched",
  VEHICLE_MOVEMENT: "Vehicle movement", HUB_ARRIVAL: "Hub arrival", DELIVERY_DELAY: "Delivery delay",
  DELIVERY_COMPLETED: "Delivery completed", WEATHER_EVENT: "Weather event", RISK_UPDATE: "Risk update",
};
export const SEVERITY_LABEL = { none: "Dry", rain: "Rain", heavy: "Heavy rain", extreme: "Extreme rain" };
export const TIER_LABEL = { express: "Express", standard: "Standard", economy: "Economy" };
export const CATEGORY_LABEL = { ELEC: "Electronics", FASH: "Fashion", GROC: "Groceries", HOME: "Home & Kitchen",
  BEAU: "Beauty", BOOK: "Books & Toys" };
