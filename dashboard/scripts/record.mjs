// Records the live WebSocket feed into a file the dashboard can replay when
// the cluster is down (public/recordings/<event>.json).
//
//   node scripts/record.mjs --url ws://localhost:8000/ws --minutes 14 --event montha-2025
//   node scripts/record.mjs --from raw.json --event montha-2025      # a raw [[ms, msg], ...] dump
//
// Keeps the first snapshot, every incident and mode message, and at most one
// tick per --tick-gap seconds; drops pings. Ticks carry only what changed, so
// a dropped tick's changes ride along with the next kept one - nothing is
// lost, the playback is just coarser. Uses Node's built-in WebSocket.

import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const MAPS = ["locations", "routes", "hubs"];

function carry(pending, tick) {
  if (!pending) return tick;
  const out = { ...tick, removed: {} };
  for (const key of MAPS) {
    const gone = new Set([...(pending.removed?.[key] ?? []), ...(tick.removed?.[key] ?? [])]);
    const changed = { ...(pending[key] ?? {}) };
    for (const [id, v] of Object.entries(tick[key] ?? {})) {
      changed[id] = { ...(changed[id] ?? {}), ...v };
      gone.delete(id);
    }
    for (const id of gone) delete changed[id];
    out[key] = changed;
    out.removed[key] = [...gone];
  }
  return out;
}

export function select(frames, minTickGapMs = 5000) {
  const out = [];
  let lastTick = -Infinity;
  let haveSnapshot = false;
  let pending = null;
  for (const [ms, msg] of frames) {
    if (msg.type === "ping") continue;
    if (msg.type === "snapshot") {
      if (haveSnapshot) continue; // the player resets from the first one only
      haveSnapshot = true;
    }
    if (msg.type === "mode") pending = null; // state resets; older deltas are moot
    if (msg.type === "tick") {
      const merged = carry(pending, msg);
      if (ms - lastTick < minTickGapMs) {
        pending = merged;
        continue;
      }
      pending = null;
      lastTick = ms;
      out.push([ms, merged]);
      continue;
    }
    out.push([ms, msg]);
  }
  if (!haveSnapshot) return [];
  const t0 = out[0][0];
  return out.map(([ms, msg]) => [ms - t0, msg]);
}

function args() {
  const a = Object.fromEntries(
    process.argv.slice(2).reduce((pairs, v, i, all) => (v.startsWith("--") ? [...pairs, [v.slice(2), all[i + 1]]] : pairs), []),
  );
  return {
    url: a.url ?? "ws://localhost:8000/ws",
    from: a.from,
    minutes: Number(a.minutes ?? 14),
    event: a.event ?? "recording",
    tickGap: Number(a["tick-gap"] ?? 5) * 1000,
    out: a.out ?? `public/recordings/${a.event ?? "recording"}.json`,
  };
}

function save(opts, frames, recordedAt) {
  const kept = select(frames, opts.tickGap);
  const body = JSON.stringify({
    meta: { recorded_at: recordedAt, source: opts.from ?? opts.url, event: opts.event, tick_gap_s: opts.tickGap / 1000 },
    frames: kept,
  });
  writeFileSync(opts.out, body);
  console.log(`${opts.out}: ${kept.length} frames, ${(body.length / 1e6).toFixed(2)} MB`);
}

function main() {
  const opts = args();
  if (opts.from) {
    save(opts, JSON.parse(readFileSync(opts.from, "utf8")), new Date().toISOString());
    return;
  }
  const frames = [];
  const started = Date.now();
  const socket = new WebSocket(opts.url);
  socket.onopen = () => console.log(`recording ${opts.url} for ${opts.minutes} min`);
  socket.onmessage = (e) => frames.push([Date.now() - started, JSON.parse(e.data)]);
  socket.onerror = (e) => console.error("socket error", e.message ?? e);
  setTimeout(() => {
    socket.close();
    save(opts, frames, new Date(started).toISOString());
  }, opts.minutes * 60 * 1000);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) main();
