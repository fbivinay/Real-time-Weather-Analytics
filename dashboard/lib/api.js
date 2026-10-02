// API access. Every GET falls back to the committed demo snapshot in
// /public/demo when the backend is unreachable, so no page is ever blank.
import { demoKey } from "./demo-key.mjs";

export { demoKey };

const HOST = process.env.NEXT_PUBLIC_API_HOST || "";
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || (HOST ? `https://${HOST}` : "");
export const WS_URL = API_BASE ? `${API_BASE.replace(/^http/, "ws")}/ws` : null;


async function withTimeout(url, opts = {}, ms = 9000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  if (opts.signal) opts.signal.addEventListener("abort", () => ctl.abort());
  try {
    return await fetch(url, { ...opts, signal: ctl.signal, cache: "no-store" });
  } finally {
    clearTimeout(t);
  }
}

// In-flight requests drive the top progress bar; responses are cached so
// revisiting a page renders instantly while it revalidates.
const listeners = new Set();
let inflight = 0;
function track(delta) {
  inflight = Math.max(0, inflight + delta);
  listeners.forEach((fn) => fn(inflight));
}
export function onInflight(fn) {
  listeners.add(fn);
  fn(inflight);
  return () => listeners.delete(fn);
}
export const cache = new Map();

export async function getJSON(path, opts = {}) {
  track(1);
  try {
    const out = await fetchJSON(path, opts);
    cache.set(path, out);
    return out;
  } finally {
    track(-1);
  }
}

export function prefetch(paths) {
  paths.forEach((p) => { if (!cache.has(p)) getJSON(p).catch(() => {}); });
}

async function fetchJSON(path, { signal } = {}) {
  let error;
  if (API_BASE) {
    try {
      const r = await withTimeout(API_BASE + path, { signal });
      if (r.ok) return { data: await r.json(), source: "live" };
      if (r.status === 404) {
        const e = new Error("not found");
        e.status = 404;
        throw e;
      }
      error = new Error(`HTTP ${r.status}`);
    } catch (e) {
      if (signal?.aborted || e.status === 404) throw e;
      error = e;
    }
  }
  const d = await fetch(`/demo/${demoKey(path)}.json`).catch(() => null);
  if (d && d.ok) return { data: await d.json(), source: "demo" };
  throw error || new Error("offline: this view is not in the demo snapshot");
}

export async function postJSON(path, body) {
  if (!API_BASE) throw new Error("Scenario runs need the live backend.");
  track(1);
  try {
    const r = await withTimeout(API_BASE + path, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }, 20000);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally {
    track(-1);
  }
}
