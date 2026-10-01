// Records the live WebSocket feed into a file the dashboard can replay when
// the cluster is down (public/recordings/<event>.json).
//
//   node scripts/record.mjs --url ws://localhost:8000/ws --minutes 14 --event michaung-2023
//
// Keeps the first snapshot, every incident and mode message, and at most one
// tick per --tick-gap seconds; drops pings. Uses Node's built-in WebSocket.

import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export function select(frames, minTickGapMs = 5000) {
  const out = [];
  let lastTick = -Infinity;
  let haveSnapshot = false;
  for (const [ms, msg] of frames) {
    if (msg.type === "ping") continue;
    if (msg.type === "snapshot") {
      if (haveSnapshot) continue; // the player resets from the first one only
      haveSnapshot = true;
    }
    if (msg.type === "tick") {
      if (ms - lastTick < minTickGapMs) continue;
      lastTick = ms;
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
    minutes: Number(a.minutes ?? 14),
    event: a.event ?? "recording",
    tickGap: Number(a["tick-gap"] ?? 5) * 1000,
    out: a.out ?? `public/recordings/${a.event ?? "recording"}.json`,
  };
}

function main() {
  const opts = args();
  const frames = [];
  const started = Date.now();
  const socket = new WebSocket(opts.url);
  socket.onopen = () => console.log(`recording ${opts.url} for ${opts.minutes} min`);
  socket.onmessage = (e) => frames.push([Date.now() - started, JSON.parse(e.data)]);
  socket.onerror = (e) => console.error("socket error", e.message ?? e);
  setTimeout(() => {
    socket.close();
    const kept = select(frames, opts.tickGap);
    const recording = {
      meta: { recorded_at: new Date(started).toISOString(), url: opts.url, minutes: opts.minutes, event: opts.event },
      frames: kept,
    };
    const body = JSON.stringify(recording);
    writeFileSync(opts.out, body);
    console.log(`${opts.out}: ${kept.length} frames, ${(body.length / 1e6).toFixed(2)} MB`);
  }, opts.minutes * 60 * 1000);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) main();
