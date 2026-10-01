import assert from "node:assert/strict";
import { mock, test } from "node:test";

import { createPlayer, initialState, reduce } from "./feed-core.mjs";

const snapshot = {
  type: "snapshot",
  mode: { source: "sim", scenario: "storm-chennai", observed_at: "2026-11-20T08:00:00Z", speed: 30 },
  kpis: { active_incidents: 0, deliveries_at_risk: 0 },
  locations: { "REF-CHE": { station_id: "REF-CHE", assessment: { score: 10 } } },
  routes: { "LM-CHE-1": { status: "low" } },
  hubs: { CHE: { status: "low" } },
  incidents: [],
  dq: { suspect: [] },
  health: { status: "ok", components: {} },
};

test("snapshot replaces the whole state", () => {
  const s = reduce({ ...initialState(), locations: { OLD: {} } }, { ...snapshot, engine: { latency_p50_s: 47 } });
  assert.equal(s.engine.latency_p50_s, 47);
  assert.deepEqual(Object.keys(s.locations), ["REF-CHE"]);
  assert.equal(s.mode.scenario, "storm-chennai");
  assert.equal(s.health.status, "ok");
});

test("tick merges changed entries, removes dropped ones and replaces kpis", () => {
  let s = reduce(initialState(), snapshot);
  s = reduce(s, {
    type: "tick",
    mode: snapshot.mode,
    kpis: { active_incidents: 1, deliveries_at_risk: 263 },
    locations: { "CHE-S1": { station_id: "CHE-S1", assessment: { score: 80 } } },
    routes: { "LM-CHE-1": { status: "critical" } },
    hubs: {},
    removed: { locations: ["REF-CHE"], routes: [], hubs: [] },
    incidents: [{ id: "INC-1" }],
    dq: { suspect: [{ station_id: "BLR-S1" }] },
    health: { tick_at: "x", latency_p50_s: 47 },
  });
  assert.deepEqual(Object.keys(s.locations), ["CHE-S1"]);
  assert.equal(s.routes["LM-CHE-1"].status, "critical");
  assert.equal(s.kpis.deliveries_at_risk, 263);
  assert.equal(s.incidents[0].id, "INC-1");
  assert.equal(s.engine.latency_p50_s, 47);
  assert.equal(s.health.status, "ok"); // composite health untouched by ticks
});

test("incident events upsert, and resolved removes from the active list", () => {
  let s = reduce(initialState(), snapshot);
  s = reduce(s, { type: "incident", event: "opened", incident: { id: "INC-1", status: "open" } });
  s = reduce(s, { type: "incident", event: "escalated", incident: { id: "INC-1", status: "escalated" } });
  assert.equal(s.incidents.length, 1);
  assert.equal(s.incidents[0].status, "escalated");
  assert.equal(s.lastEvent.event, "escalated");
  s = reduce(s, { type: "incident", event: "resolved", incident: { id: "INC-1", status: "resolved" } });
  assert.equal(s.incidents.length, 0);
});

test("mode change clears everything but the new mode", () => {
  let s = reduce(initialState(), snapshot);
  s = reduce(s, { type: "mode", mode: { source: "replay", scenario: "michaung-2023" } });
  assert.deepEqual(s.locations, {});
  assert.equal(s.mode.scenario, "michaung-2023");
});

test("pings and unknown messages leave state alone", () => {
  const s = reduce(initialState(), snapshot);
  assert.equal(reduce(s, { type: "ping" }), s);
});

test("health messages replace composite health", () => {
  const s = reduce(reduce(initialState(), snapshot), { type: "health", health: { status: "degraded" } });
  assert.equal(s.health.status, "degraded");
});

test("recording player replays frames on their offsets and loops", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const seen = [];
    const rec = { meta: {}, frames: [[0, { type: "snapshot", n: 1 }], [1000, { type: "tick", n: 2 }], [3000, { type: "tick", n: 3 }]] };
    const player = createPlayer(rec, (m) => seen.push(m.n), { loopPauseMs: 2000 });
    mock.timers.tick(0);
    assert.deepEqual(seen, [1]);
    mock.timers.tick(1000);
    assert.deepEqual(seen, [1, 2]);
    mock.timers.tick(2000);
    assert.deepEqual(seen, [1, 2, 3]);
    mock.timers.tick(2000); // pause, then start over
    assert.deepEqual(seen, [1, 2, 3, 1]);
    player.stop();
    mock.timers.tick(10000);
    assert.deepEqual(seen, [1, 2, 3, 1]);
  } finally {
    mock.timers.reset();
  }
});

test("a tick's partial location keeps the static fields from the snapshot", () => {
  let s = reduce(initialState(), {
    ...snapshot,
    locations: { "REF-CHE": { station_id: "REF-CHE", name: "Chennai", lat: 13.08, lon: 80.27, assessment: { score: 10 } } },
  });
  s = reduce(s, { type: "tick", locations: { "REF-CHE": { assessment: { score: 60 } } } });
  assert.equal(s.locations["REF-CHE"].assessment.score, 60);
  assert.equal(s.locations["REF-CHE"].lat, 13.08);
  assert.equal(s.locations["REF-CHE"].name, "Chennai");
});
