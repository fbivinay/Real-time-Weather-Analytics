"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { getJSON, onInflight, prefetch } from "../lib/api";
import { ago, istDay, istHour, num } from "../lib/format";
import { LiveProvider, useLive } from "../lib/live";
import CountUp from "./CountUp";
import OrderDrawer from "./OrderDrawer";
import { StatusDot } from "./ui";

const NAV = [["/", "01", "Overview"], ["/map", "02", "Map"], ["/future", "03", "Future"], ["/history", "04", "History"]];
const UICtx = createContext(null);
export const useUI = () => useContext(UICtx);

export function Mark({ className = "brand-mark" }) {
  // Rain cloud over a delivery route: weather -> deliveries. Same art as app/icon.svg (the tab icon).
  return (
    <svg className={className} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="15" fill="#0b0b0c" />
      <path d="M19.5 33.5h25.2a7.6 7.6 0 0 0 .9-15.1 11.2 11.2 0 0 0-21.3-2.6 8.9 8.9 0 0 0-4.8 17.7z" fill="#fff" />
      <g stroke="#7db4f0" strokeWidth="3.2" strokeLinecap="round">
        <path d="M24.5 38.5l-2 5" /><path d="M33 38.5l-2 5" /><path d="M41.5 38.5l-2 5" />
      </g>
      <path d="M11.5 53h10.5l5-4.5 6 4.5h19.5" fill="none" stroke="#e05a47" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="11.5" cy="53" r="3.4" fill="#fff" />
      <circle cx="52.5" cy="53" r="3.4" fill="#e05a47" />
    </svg>
  );
}

// Phones and tablets (touch-only, mobile/tablet user agents, iPadOS, narrow screens) get a message instead of the app.
function isDesktop() {
  const ua = navigator.userAgent;
  const mobileUA = /Android|iPhone|iPad|iPod|Mobile|Tablet|Silk|Kindle|PlayBook|BlackBerry|Opera Mini|IEMobile/i.test(ua);
  const iPadOS = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  const touchOnly = window.matchMedia("(pointer: coarse)").matches && !window.matchMedia("(any-pointer: fine)").matches;
  return !(mobileUA || iPadOS || touchOnly || window.innerWidth < 1200);
}

function DeviceGate() {
  return (
    <div className="gate-msg">
      <Mark className="gate-mark" />
      <h1>Please open WeatherOps on a desktop or laptop</h1>
      <p>This app is built for large screens and isn’t available on phones or tablets.</p>
    </div>
  );
}

function Search({ onClose }) {
  const router = useRouter();
  const { openOrder } = useUI();
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [active, setActive] = useState(0);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return undefined; }
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const r = await getJSON(`/api/search?q=${encodeURIComponent(q.trim())}`, { signal: ctl.signal });
        setRes(r.data);
        setActive(0);
      } catch { if (!ctl.signal.aborted) setRes({ error: true }); }
    }, 160);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q]);

  const items = [];
  if (res && !res.error) {
    (res.orders || []).forEach((o) => items.push({ g: "Orders", label: o.id, sub: `${o.status} · ${o.route_id.replace("R-", "").replace("-", " → ")}`, go: () => openOrder(o.id) }));
    (res.routes || []).forEach((r) => items.push({ g: "Routes", label: r.code, sub: "route", go: () => router.push(`/map?sel=route:${r.id}`) }));
    (res.cities || []).forEach((c) => items.push({ g: "Cities", label: c.name, sub: c.state, go: () => router.push(`/map?sel=city:${c.id}`) }));
    (res.warehouses || []).forEach((w) => items.push({ g: "Warehouses", label: w.name, sub: w.id, go: () => router.push(`/map?sel=warehouse:${w.id}`) }));
    (res.hubs || []).forEach((h) => items.push({ g: "Hubs", label: h.name, sub: h.id, go: () => router.push(`/map?sel=hub:${h.id}`) }));
  }
  const pick = (it) => { onClose(); it.go(); };
  const onKey = (e) => {
    if (e.key === "Escape") onClose();
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    if (e.key === "Enter" && items[active]) pick(items[active]);
  };
  let lastGroup = null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="palette" role="dialog" aria-label="Search">
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
          placeholder="Order ID, route (BLR → MYS), city, warehouse or hub" />
        <div className="res">
          {q.trim().length < 2 ? <div className="empty">Try <span className="mono">ORD-0001234</span>, <span className="mono">BLR → MYS</span>, Mangaluru or Bhiwandi.</div> : null}
          {res?.error ? <div className="empty">Search needs the live backend.</div> : null}
          {res && !res.error && !items.length ? <div className="empty">Nothing matches “{q}”.</div> : null}
          {items.map((it, i) => {
            const head = it.g !== lastGroup ? <div className="grp">{it.g}</div> : null;
            lastGroup = it.g;
            return (
              <div key={`${it.g}-${it.label}`}>
                {head}
                <div className={`it${i === active ? " on" : ""}`} onMouseEnter={() => setActive(i)} onClick={() => pick(it)}>
                  <span className={it.g === "Orders" || it.g === "Routes" ? "mono" : ""}>{it.label}</span>
                  <span className="faint" style={{ fontSize: 14.9 }}>{it.sub}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

const STATUS_WORD = { live: "Live", polling: "Live (polling)", reconnecting: "Reconnecting…", connecting: "Connecting…", offline: "Demo snapshot" };

// Thin bar under the header while any request is in flight.
function TopProgress() {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let t;
    return onInflight((n) => {
      clearTimeout(t);
      // Short requests never flash the bar; it hides a beat after the last one ends.
      t = setTimeout(() => setBusy(n > 0), n > 0 ? 120 : 250);
    });
  }, []);
  return <div className={`topprogress${busy ? " on" : ""}`} aria-hidden="true"><i /></div>;
}

// Opening splash: holds ~2.5 s (or until the first live data arrives, max 4 s)
// while every page's data is prefetched, then fades away.
function Splash() {
  const live = useLive();
  const [phase, setPhase] = useState("show");
  const started = useRef(0);
  useEffect(() => {
    started.current = performance.now();
    prefetch(["/api/map?mode=impact", "/api/future/timeline", "/api/history", "/api/history/options",
      "/api/future?window=24&sort=score&page=1&size=15"]);
    const max = setTimeout(() => setPhase("hide"), 4000);
    return () => clearTimeout(max);
  }, []);
  useEffect(() => {
    if (phase !== "show" || !live.overview) return undefined;
    const wait = Math.max(0, 2400 - (performance.now() - started.current));
    const t = setTimeout(() => setPhase("hide"), wait);
    return () => clearTimeout(t);
  }, [live.overview, phase]);
  useEffect(() => {
    if (phase !== "hide") return undefined;
    window.dispatchEvent(new Event("weatherops:reveal"));   // numbers count up as the page appears
    const t = setTimeout(() => setPhase("gone"), 650);
    return () => clearTimeout(t);
  }, [phase]);
  if (phase === "gone") return null;
  return (
    <div className={`splash${phase === "hide" ? " out" : ""}`} aria-label="Loading WeatherOps">
      <div className="splash-inner">
        <Mark />
        <div className="splash-name">WeatherOps</div>
        <div className="splash-bar"><i /></div>
      </div>
    </div>
  );
}

function StatusBar() {
  const live = useLive();
  const h = live.health?.components || {};
  const ov = live.overview;
  const stream = ov?.stream || {};
  const lastType = stream.last_event?.type?.replaceAll("_", " ").toLowerCase();
  const spark = h.spark?.status === "ok" ? "Processing" : h.spark?.status === "degraded" ? "Lagging" : "Down";
  const on = live.health && live.health.source === "live";
  return (
    <div className="statusbar">
      <div className="group">
        <StatusDot status={live.status === "live" || live.status === "polling" ? "ok" : live.status === "offline" ? "down" : "degraded"}
          label={STATUS_WORD[live.status] || live.status} />
        {ov ? <span className="mono">{istDay(ov.sim_time).replace(/, \d{4}$/, "")} · {istHour(ov.sim_time)} IST</span> : null}
      </div>
      <div className="group">
        {on ? (
          <>
            <StatusDot status={h.kafka?.status} label="Kafka" />
            <StatusDot status={h.spark?.status} label={spark === "Processing" ? "Spark" : `Spark ${spark.toLowerCase()}`} />
            <StatusDot status={h.api?.status} label="API" />
            <StatusDot status={h.database?.status} label="Database" />
            <StatusDot status={h.stream?.status} label="Stream" />
          </>
        ) : <span>Pipeline status unavailable</span>}
        <span className="mono">{num(stream.events_per_s ?? 0, 1)} ev/s</span>
        <span title="Last processed event" className="hide-narrow">{lastType ? `last: ${lastType}` : ""}</span>
        <span className="mono" title="Data freshness">{stream.freshness_s != null && live.status === "live" ? ago(stream.freshness_s) : ""}</span>
      </div>
    </div>
  );
}

function Chrome({ children }) {
  const path = usePathname();
  const live = useLive();
  const { openSearch } = useUI();
  const stale = live.status === "reconnecting" || (live.status === "offline" && live.overview);
  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <Link href="/" className="brand">
            <Mark />
            <div>
              <div className="brand-name">WeatherOps</div>
              <div className="brand-sub"><span className="red">Weather-aware</span> delivery intelligence · ShopFlow India</div>
            </div>
          </Link>
          <nav className="pillnav" aria-label="Primary">
            {NAV.map(([href, , label]) => (
              <Link key={href} href={href} className={path === href ? "on" : ""}>{label}</Link>
            ))}
          </nav>
          <div className="header-right">
            <button type="button" className="searchpill" onClick={openSearch}>
              Search orders, routes, cities
            </button>
          </div>
        </div>
        <StatusBar />
        <TopProgress />
      </header>
      {stale ? (
        <div style={{ padding: "0 32px" }}>
          <div className="banner">
            <i className="dot degraded" />
            Live stream disconnected — displaying last known state{live.at ? ` from ${new Date(live.at).toLocaleTimeString("en-IN")}` : ""}.
            {live.status === "reconnecting" ? " Reconnecting…" : ""}
          </div>
        </div>
      ) : null}
      {children}
      <footer className="footer">
        <div>
          <div className="wordmark">WeatherOps</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: "6px 16px", alignContent: "start" }}>
          <span>Pipeline</span><span>Event stream → Kafka → Spark Structured Streaming → Postgres / Redis → FastAPI → WebSocket</span>
          <span>Source</span><a className="link" href="https://github.com/fbivinay/Real-time-Weather-Analytics">github.com/fbivinay/Real-time-Weather-Analytics</a>
        </div>
      </footer>
    </div>
  );
}

function UIProvider({ children }) {
  const [order, setOrder] = useState(null);
  const [search, setSearch] = useState(false);
  const openOrder = useCallback((id) => setOrder(id), []);
  const openSearch = useCallback(() => setSearch(true), []);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearch(true); }
      if (e.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) { e.preventDefault(); setSearch(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <UICtx.Provider value={{ openOrder, openSearch }}>
      {children}
      {search ? <Search onClose={() => setSearch(false)} /> : null}
      {order ? <OrderDrawer id={order} onClose={() => setOrder(null)} /> : null}
    </UICtx.Provider>
  );
}

export default function Shell({ children }) {
  const [desktop, setDesktop] = useState(null);
  useEffect(() => {
    const check = () => setDesktop(isDesktop());
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  if (desktop === false) return <DeviceGate />;
  return (
    <LiveProvider>
      <UIProvider>
        <Splash />
        <CountUp />
        <Chrome>{children}</Chrome>
        <div className="gate"><DeviceGate /></div>
      </UIProvider>
    </LiveProvider>
  );
}
