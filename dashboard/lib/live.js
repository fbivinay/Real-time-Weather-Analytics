"use client";
// Live operations feed: WebSocket to the API (snapshot, then a tick every
// ~5 s), exponential reconnect, and the last known state - or the committed
// demo snapshot - when the stream is down.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { cache, getJSON, WS_URL } from "./api";

const LiveCtx = createContext(null);

export function LiveProvider({ children }) {
  const [state, setState] = useState({ overview: null, impact: null, status: "connecting", source: null, at: null });
  const [health, setHealth] = useState(null);
  const gotLive = useRef(false);

  const loadRest = useCallback(async () => {
    try {
      const [o, i] = await Promise.all([getJSON("/api/overview"), getJSON("/api/impact")]);
      setState((s) => ({
        ...s, overview: s.source === "live" && s.overview ? s.overview : o.data,
        impact: s.source === "live" && s.impact ? s.impact : i.data,
        source: gotLive.current ? "live" : o.source, at: Date.now(),
        status: gotLive.current ? s.status : o.source === "live" ? "polling" : "offline",
      }));
    } catch {
      setState((s) => ({ ...s, status: s.overview ? s.status : "offline" }));
    }
  }, []);

  useEffect(() => {
    let ws;
    let timer;
    let closed = false;
    let attempt = 0;
    const firstData = setTimeout(() => { if (!gotLive.current) loadRest(); }, 2500);

    function connect() {
      if (!WS_URL) {
        loadRest();
        return;
      }
      ws = new WebSocket(WS_URL);
      ws.onopen = () => { attempt = 0; };
      ws.onmessage = (e) => {
        let m;
        try { m = JSON.parse(e.data); } catch { return; }
        if (m.type !== "snapshot" && m.type !== "tick") return;
        gotLive.current = true;
        setState((s) => ({
          overview: m.overview || s.overview,
          impact: m.impact ? { ...(s.impact || {}), ...m.impact } : s.impact,
          status: "live", source: "live", at: Date.now(),
        }));
      };
      ws.onclose = () => {
        if (closed) return;
        gotLive.current = false;
        setState((s) => ({ ...s, status: s.overview ? "reconnecting" : "offline" }));
        attempt += 1;
        if (attempt === 1) loadRest();
        timer = setTimeout(connect, Math.min(30000, 1000 * 2 ** attempt));
      };
      ws.onerror = () => ws.close();
    }
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      clearTimeout(firstData);
      if (ws) ws.close();
    };
  }, [loadRest]);

  useEffect(() => {
    let stop = false;
    async function poll() {
      try {
        const h = await getJSON("/api/health");
        if (!stop) setHealth({ ...h.data, source: h.source });
      } catch {
        if (!stop) setHealth(null);
      }
    }
    poll();
    const t = setInterval(poll, 15000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  return <LiveCtx.Provider value={{ ...state, health }}>{children}</LiveCtx.Provider>;
}

export function useLive() {
  return useContext(LiveCtx);
}

// One-shot GET with demo fallback; re-runs when `path` changes.
export function useFetch(path, { refreshMs } = {}) {
  const initial = () => {
    const hit = path && cache.get(path);
    return hit ? { data: hit.data, source: hit.source, error: null, loading: false, refreshing: true }
      : { data: null, source: null, error: null, loading: true, refreshing: false };
  };
  const [res, setRes] = useState(initial);
  useEffect(() => {
    if (!path) return undefined;
    const ctl = new AbortController();
    let t;
    const hit = cache.get(path);
    // Keep showing what we have (dimmed) while the new view loads.
    setRes((r) => (hit ? { data: hit.data, source: hit.source, error: null, loading: false, refreshing: true }
      : { ...r, loading: !r.data, refreshing: !!r.data, error: null }));
    async function run() {
      try {
        const out = await getJSON(path, { signal: ctl.signal });
        setRes({ data: out.data, source: out.source, error: null, loading: false, refreshing: false });
      } catch (e) {
        if (!ctl.signal.aborted) setRes((r) => ({ ...r, error: e, loading: false, refreshing: false }));
      }
      if (refreshMs && !ctl.signal.aborted) t = setTimeout(run, refreshMs);
    }
    run();
    return () => { ctl.abort(); clearTimeout(t); };
  }, [path, refreshMs]);
  return res;
}
