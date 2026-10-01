import assert from "node:assert/strict";
import { test } from "node:test";

import { select } from "./record.mjs";

test("keeps the first snapshot, incidents and spaced ticks; drops pings; rebases time", () => {
  const frames = [
    [100, { type: "ping" }],
    [200, { type: "snapshot", n: 1 }],
    [1200, { type: "tick", n: 2 }],
    [3200, { type: "tick", n: 3 }],
    [3300, { type: "incident", n: 4 }],
    [7000, { type: "tick", n: 5 }],
    [8000, { type: "snapshot", n: 6 }],
    [9000, { type: "mode", n: 7 }],
  ];
  const kept = select(frames, 5000);
  assert.deepEqual(kept.map(([, m]) => m.n), [1, 2, 4, 5, 7]);
  assert.deepEqual(kept.map(([ms]) => ms), [0, 1000, 3100, 6800, 8800]);
});

test("a recording without a snapshot is empty", () => {
  assert.deepEqual(select([[0, { type: "tick" }]]), []);
});
