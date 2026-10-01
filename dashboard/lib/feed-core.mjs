// Pure state handling for the live feed, shared by the WebSocket hook and
// the offline recording player. Messages are exactly what the API sends:
//   snapshot  - full state, first message on every connection
//   tick      - deltas from the engine (changed locations/routes/hubs)
//   incident  - one incident opened / escalated / updated / resolved
//   mode      - the ingestor switched source; everything resets
//   health    - composite pipeline health, polled from /api/health

export function initialState() {
  return {
    mode: null,
    kpis: null,
    locations: {},
    routes: {},
    hubs: {},
    incidents: [],
    dq: null,
    health: null,
    engine: null,
    lastEvent: null,
    updatedAt: null,
  };
}

function merge(current, changed = {}, removed = []) {
  const next = { ...current };
  // Shallow-merge each entry: ticks send only the fields that change for
  // entries the client already holds (names and coordinates arrive once).
  for (const [key, value] of Object.entries(changed)) next[key] = { ...current[key], ...value };
  for (const key of removed) delete next[key];
  return next;
}

export function reduce(state, msg) {
  switch (msg?.type) {
    case "snapshot":
      return {
        ...initialState(),
        mode: msg.mode ?? null,
        kpis: msg.kpis ?? null,
        locations: msg.locations ?? {},
        routes: msg.routes ?? {},
        hubs: msg.hubs ?? {},
        incidents: msg.incidents ?? [],
        dq: msg.dq ?? null,
        health: msg.health ?? null,
        engine: msg.engine ?? null,
        updatedAt: Date.now(),
      };
    case "tick":
      return {
        ...state,
        mode: msg.mode ?? state.mode,
        kpis: msg.kpis ?? state.kpis,
        locations: merge(state.locations, msg.locations, msg.removed?.locations),
        routes: merge(state.routes, msg.routes, msg.removed?.routes),
        hubs: merge(state.hubs, msg.hubs, msg.removed?.hubs),
        incidents: msg.incidents ?? state.incidents,
        dq: msg.dq ?? state.dq,
        engine: msg.health ?? state.engine,
        updatedAt: Date.now(),
      };
    case "incident": {
      const others = state.incidents.filter((i) => i.id !== msg.incident.id);
      return {
        ...state,
        incidents: msg.event === "resolved" ? others : [msg.incident, ...others],
        lastEvent: { event: msg.event, incident: msg.incident, at: Date.now() },
      };
    }
    case "mode":
      return { ...initialState(), mode: msg.mode };
    case "health":
      return { ...state, health: msg.health };
    default:
      return state;
  }
}

// Replays a recorded session: frames are [offset_ms, message] pairs, the first
// one a snapshot. Loops after a pause so the offline dashboard never goes still.
export function createPlayer(recording, dispatch, { loopPauseMs = 5000 } = {}) {
  const frames = recording.frames ?? [];
  let timer = null;
  let stopped = false;

  function run(i, startedAt) {
    if (stopped || frames.length === 0) return;
    if (i >= frames.length) {
      const last = frames[frames.length - 1][0];
      timer = setTimeout(() => run(0, startedAt + last + loopPauseMs), loopPauseMs);
      return;
    }
    const [offset, message] = frames[i];
    const wait = i === 0 ? 0 : offset - frames[i - 1][0];
    timer = setTimeout(() => {
      dispatch(message);
      run(i + 1, startedAt);
    }, wait);
  }

  run(0, 0);
  return {
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
  };
}
