import assert from "node:assert/strict";
import test from "node:test";

import { demoKey } from "./demo-key.mjs";

test("demo keys are stable file names", () => {
  assert.equal(demoKey("/api/overview"), "overview");
  assert.equal(demoKey("/api/map?mode=rainfall&month=7"), "map_mode_rainfall_month_7");
  assert.equal(demoKey("/api/locations/route/R-BLR-MYS"), "locations_route_R_BLR_MYS");
  assert.equal(demoKey(`/api/locations/state/${encodeURIComponent("Tamil Nadu")}`), "locations_state_Tamil_Nadu");
});
