"use client";

// Live feed: holds wss://<NEXT_PUBLIC_API_HOST>/ws with exponential backoff.
// If the cluster is unreachable for 15 s (or no host is configured at all -
// the node only runs on demand) it plays a recorded session instead, and
// switches back the moment the live socket opens.

import { useEffect, useReducer, useState } from "react";

import { createPlayer, initialState, reduce } from "./feed-core.mjs";

const HOST = process.env.NEXT_PUBLIC_API_HOST || "";
const RECORDING = process.env.NEXT_PUBLIC_RECORDING || "montha-2025";
const FALLBACK_AFTER_MS = 15000;
const HEALTH_EVERY_MS = 30000;

function endpoints() {
  if (!HOST) return null;
  const local = /^(localhost|127\.0\.0\.1)(:|$)/.test(HOST);
  return {
    http: `${local ? "http" : "https"}://${HOST}`,
    ws: `${local ? "ws" : "wss"}://${HOST}/ws`,
  };
}

export function useFeed() {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [connection, setConnection] = useState(HOST ? "connecting" : "recording");

  useEffect(() => {
    const api = endpoints();
    let socket = null;
    let player = null;
    let fallback = null;
    let healthTimer = null;
    let retry = 1000;
    let stopped = false;

    async function playRecording() {
      fallback = null;
      if (player || stopped) return;
      try {
        const res = await fetch(`/recordings/${RECORDING}.json`);
        if (!res.ok) throw new Error(`recording ${res.status}`);
        const recording = await res.json();
        if (stopped || player) return;
        player = createPlayer(recording, dispatch);
        setConnection("recording");
      } catch {
        setConnection("offline");
      }
    }

    function armFallback() {
      if (!fallback && !player) fallback = setTimeout(playRecording, FALLBACK_AFTER_MS);
    }

    async function pollHealth() {
      try {
        const res = await fetch(`${api.http}/api/health`, { cache: "no-store" });
        if (res.ok) dispatch({ type: "health", health: await res.json() });
      } catch {
        // the socket's close handler reports connection loss
      }
    }

    function connect() {
      if (stopped) return;
      socket = new WebSocket(api.ws);
      socket.onopen = () => {
        retry = 1000;
        clearTimeout(fallback);
        fallback = null;
        if (player) {
          player.stop();
          player = null;
        }
        setConnection("live");
        clearInterval(healthTimer);
        healthTimer = setInterval(pollHealth, HEALTH_EVERY_MS);
      };
      socket.onmessage = (event) => {
        try {
          dispatch(JSON.parse(event.data));
        } catch {
          // ignore a malformed frame rather than drop the connection
        }
      };
      socket.onclose = () => {
        clearInterval(healthTimer);
        if (stopped) return;
        setConnection((c) => (c === "recording" ? c : "connecting"));
        armFallback();
        setTimeout(connect, retry);
        retry = Math.min(retry * 2, 30000);
      };
    }

    if (api) {
      armFallback();
      connect();
    } else {
      playRecording();
    }

    return () => {
      stopped = true;
      clearTimeout(fallback);
      clearInterval(healthTimer);
      socket?.close();
      player?.stop();
    };
  }, []);

  return { state, connection, recording: RECORDING };
}
