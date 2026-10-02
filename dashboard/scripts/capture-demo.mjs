// Saves the live API's responses into public/demo/ so the dashboard still
// shows a complete, consistent picture when the backend is down.
//   API=https://35-170-210-110.sslip.io node scripts/capture-demo.mjs
import { mkdir, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";

import { demoKey } from "../lib/demo-key.mjs";

const API = process.env.API;
if (!API) throw new Error("set API=https://<host>");
const out = new URL("../public/demo/", import.meta.url);

async function get(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function save(path) {
  const data = await get(path);
  await writeFile(new URL(`${demoKey(path)}.json`, out), JSON.stringify(data));
  return data;
}

await mkdir(out, { recursive: true });
const overview = await save("/api/overview");
await save("/api/impact");
await save("/api/health");
await save("/api/map?mode=impact");
for (let m = 1; m <= 12; m += 1) await save(`/api/map?mode=rainfall&month=${m}`);
await save("/api/future/timeline");
for (const w of [6, 12, 24, 48]) await save(`/api/future?window=${w}&sort=score&page=1&size=15`);
await save("/api/history");
await save("/api/history/options");
const net = JSON.parse(readFileSync(new URL("../public/network.geojson", import.meta.url)));
const states = [...new Set(net.features.filter((f) => f.properties.layer === "city").map((f) => f.properties.state))];
const ids = { state: states };
for (const f of net.features) {
  const k = f.properties.layer;
  if (k === "city" || k === "warehouse" || k === "hub" || k === "route") (ids[k] ||= []).push(f.properties.id);
}
for (const [kind, list] of Object.entries(ids)) {
  for (const id of list) await save(`/api/locations/${kind}/${encodeURIComponent(id)}`);
}
for (const o of overview.critical_orders || []) await save(`/api/orders/${encodeURIComponent(o.id)}`);
console.log("captured demo snapshot at", overview.sim_time);
