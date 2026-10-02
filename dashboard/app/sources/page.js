"use client";
// 05 Sources — how WeatherOps works, told as a self-playing pipeline.
import { useEffect, useRef, useState } from "react";

const STAGE_MS = 4200;

const STAGES = [
  {
    key: "rain", title: "Real rainfall", tag: "Open-Meteo archive · ERA5",
    text: "Daily rain for 40 Indian cities from 2015 to 2025 and hourly rain for 2022–2025. It sets the climatology, drives history and is replayed hour by hour.",
    steps: ["40 cities", "11 years daily", "4 years hourly", "IMD rain classes"],
    stat: [["1,60,720", "daily readings"], ["14,02,560", "hourly readings"]],
    logos: ["python", "pandas", "numpy"],
  },
  {
    key: "events", title: "Operations event stream", tag: "ShopFlow India · 60× time",
    text: "Every order becomes a life story of events: created, assigned, dispatched, moving, at the hub, delayed by rain, delivered — paced against the replayed weather.",
    steps: ["Orders", "Trucks & waves", "Weather each hour", "Delays from real rain"],
    stat: [["~60", "events / second"], ["25,000", "orders a day"]],
    logos: ["python"],
  },
  {
    key: "kafka", title: "Kafka", tag: "Event backbone",
    text: "Every event lands on a Kafka topic first, so producers and processors never wait for each other and a restart replays the stream instead of losing it.",
    steps: ["shopflow-events", "shopflow-clean", "shopflow-metrics", "shopflow-quarantine"],
    stat: [["4", "topics"], ["24 h", "retention"]],
    logos: ["apachekafka"],
  },
  {
    key: "spark", title: "Spark Structured Streaming", tag: "Validate · deduplicate · window",
    text: "Spark checks every event, quarantines the broken ones, drops duplicates inside a watermark and rolls the clean stream into 30-second windows per city.",
    steps: ["Schema check", "Quarantine bad rows", "Dedup on event id", "30 s city windows"],
    stat: [["4", "streaming queries"], ["30 s", "windows"]],
    logos: ["apachespark"],
  },
  {
    key: "engine", title: "Risk & impact engine", tag: "Every 5 seconds",
    text: "Turns weather into delivery impact: ETA under rain, a 0–100 risk score built from seven explained parts, SLA-breach probability and plain-language alerts.",
    steps: ["Weather → ETA", "7-part risk score", "SLA probability", "Alerts"],
    stat: [["7", "risk contributors"], ["5 s", "tick"]],
    logos: ["python", "numpy"],
  },
  {
    key: "store", title: "Storage", tag: "Postgres · Redis · S3",
    text: "Postgres keeps orders, predictions and four and a half years of history; Redis holds the live views and pushes every tick; S3 keeps the raw event lake.",
    steps: ["Orders & history", "Live views", "Pub/sub ticks", "Raw lake"],
    stat: [["2.7 Cr", "orders of history"], ["50k", "monthly roll-ups"]],
    logos: ["postgresql", "redis", "amazons3"],
  },
  {
    key: "app", title: "API & dashboard", tag: "FastAPI · WebSocket · Next.js",
    text: "FastAPI serves drill-downs, history and scenarios, and a WebSocket streams every engine tick straight into the Overview, Map, Future and History pages.",
    steps: ["REST", "WebSocket", "Map drill-down", "What-if scenarios"],
    stat: [["4", "pages"], ["live", "every 5 s"]],
    logos: ["fastapi", "nextdotjs", "react", "maplibre"],
  },
];

const BRAND = {
  python: "#3776AB", pandas: "#150458", numpy: "#013243", apachekafka: "#231F20", apachespark: "#E25A1C",
  postgresql: "#4169E1", redis: "#FF4438", amazons3: "#569A31", fastapi: "#009688", nextdotjs: "#000000",
  react: "#149ECA", maplibre: "#396CB2", vercel: "#000000", terraform: "#844FBA", kubernetes: "#326CE5",
  k3s: "#E8A800", docker: "#2496ED", amazonec2: "#FF9900", traefikproxy: "#24A1C1", letsencrypt: "#003A70",
};

const STACK = [
  ["Data", [["apachekafka", "Apache Kafka", "Durable event log between every service.", "Replays the stream after any restart."],
    ["apachespark", "Spark Streaming", "Validates, deduplicates and windows events.", "Writes clean data to Postgres and S3."],
    ["postgresql", "PostgreSQL", "Orders, predictions and 4.5 years of history.", "Monthly roll-ups keep History instant."],
    ["redis", "Redis", "Live views the engine rewrites every tick.", "Pub/sub fans updates out to browsers."]]],
  ["Models", [["python", "Python", "Engine, event stream, seed and API.", "One shared package for every service."],
    ["numpy", "NumPy", "Vectorised history and the SLA model fit.", "Logistic regression solved by IRLS."],
    ["pandas", "pandas", "Climatology and history building.", "Daily rain to monthly percentiles."]]],
  ["Interface", [["fastapi", "FastAPI", "REST drill-downs, history and scenarios.", "WebSocket for every live tick."],
    ["nextdotjs", "Next.js", "Four-page desktop app.", "Cached, prefetched, animated views."],
    ["react", "React", "Live state, drawers and charts.", "Hand-built SVG, no chart library."],
    ["maplibre", "MapLibre", "India impact and rainfall map.", "Local India-view boundaries, no tiles."],
    ["vercel", "Vercel", "Hosts the dashboard worldwide.", "Falls back to a saved snapshot offline."]]],
  ["Platform", [["amazonec2", "AWS EC2", "One on-demand node runs the pipeline.", "Started and stopped by one script."],
    ["amazons3", "Amazon S3", "Raw event lake, partitioned by day.", "Kept when the node is off."],
    ["k3s", "k3s", "Lightweight Kubernetes on that node.", "Every service is a deployment."],
    ["terraform", "Terraform", "Node, IP, bucket and access as code.", "Up or down in minutes."],
    ["traefikproxy", "Traefik", "HTTPS ingress for the API.", "Routes REST and WebSocket."],
    ["letsencrypt", "Let's Encrypt", "Free TLS certificate for the API.", "Renewed automatically."]]],
];

const RISK = [["Rainfall severity", 30], ["Route history", 20], ["Route exposure", 15], ["SLA tightness", 12],
  ["Warehouse load", 10], ["Delivery timing", 8], ["On-time history", 5]];
const RISK_SAMPLE = [21, 15, 2, 12, 1, 4, 0.4];

function Logo({ name, size = 28 }) {
  return (
    <span className="src-logo" style={{ width: size, height: size, background: BRAND[name] || "#0b0b0c",
      WebkitMaskImage: `url(/logos/${name}.svg)`, maskImage: `url(/logos/${name}.svg)` }} />
  );
}

// ---------- hero: rain over a moving delivery ----------
function Hero() {
  return (
    <div className="src-hero">
      <div className="src-hero-text">
        <h1 className="src-title">How WeatherOps works</h1>
        <p className="src-sub">From real rainfall to a ranked list of deliveries to act on — live, every five seconds.</p>
      </div>
      <div className="src-scene" aria-hidden="true">
        <div className="src-cloud c1" /><div className="src-cloud c2" />
        {Array.from({ length: 26 }, (_, i) => (
          <i key={i} className="src-drop" style={{ left: `${(i * 37) % 100}%`, animationDelay: `${(i * 0.13) % 1.4}s`,
            animationDuration: `${0.8 + ((i * 7) % 5) / 10}s` }} />
        ))}
        <div className="src-road"><i /></div>
        <div className="src-truck">
          <svg viewBox="0 0 64 32"><rect x="2" y="6" width="36" height="18" rx="3" fill="#0b0b0c" /><path d="M38 12h12l8 7v5H38z" fill="#0b0b0c" />
            <rect x="42" y="14" width="8" height="5" rx="1" fill="#7db4f0" /><circle cx="14" cy="26" r="4.5" fill="#e05a47" /><circle cx="48" cy="26" r="4.5" fill="#e05a47" /></svg>
        </div>
        <div className="src-pin start" /><div className="src-pin end" />
      </div>
    </div>
  );
}

// ---------- per-stage visuals ----------
const RAIN_BARS = [2, 1, 3, 12, 60, 520, 840, 560, 340, 90, 20, 4];
const EVENT_TYPES = ["ORDER_CREATED", "ORDER_ASSIGNED", "VEHICLE_DISPATCHED", "VEHICLE_MOVEMENT", "WEATHER_EVENT",
  "DELIVERY_DELAY", "HUB_ARRIVAL", "DELIVERY_COMPLETED"];

function Visual({ k }) {
  if (k === "rain") {
    return (
      <div className="v-rain">
        {RAIN_BARS.map((v, i) => <i key={i} style={{ "--h": `${(v / 840) * 100}%`, animationDelay: `${i * 0.07}s` }} />)}
        <span className="v-cap">Mumbai · average rain by month</span>
      </div>
    );
  }
  if (k === "events") {
    const rows = [...EVENT_TYPES, ...EVENT_TYPES];
    return (
      <div className="v-feed"><div className="v-feed-in">
        {rows.map((t, i) => (
          <div key={i} className="v-ev"><b className={t.includes("DELAY") ? "red" : ""}>{t.replaceAll("_", " ").toLowerCase()}</b>
            <span className="mono">ORD-{String(4100000 + i * 137).slice(0, 7)}</span></div>
        ))}
      </div></div>
    );
  }
  if (k === "kafka") {
    return (
      <div className="v-lanes">
        {["events", "clean", "metrics", "quarantine"].map((t, li) => (
          <div key={t} className="v-lane"><span className="mono">{t}</span><div className="v-track">
            {Array.from({ length: 5 }, (_, j) => <i key={j} className={li === 3 ? "bad" : ""} style={{ animationDelay: `${j * 0.55 + li * 0.2}s` }} />)}
          </div></div>
        ))}
      </div>
    );
  }
  if (k === "spark") {
    return (
      <div className="v-spark">
        <div className="v-in">{Array.from({ length: 9 }, (_, j) => <i key={j} className={j % 4 === 3 ? "bad" : j % 5 === 2 ? "dup" : ""} style={{ animationDelay: `${j * 0.32}s` }} />)}</div>
        <div className="v-gate"><span>validate</span><span>dedup</span></div>
        <div className="v-wins">{Array.from({ length: 6 }, (_, j) => <i key={j} style={{ animationDelay: `${j * 0.45}s` }} />)}</div>
      </div>
    );
  }
  if (k === "engine") return <RiskBuild compact />;
  if (k === "store") {
    return (
      <div className="v-store">
        {[["postgresql", "Postgres"], ["redis", "Redis"], ["amazons3", "S3"]].map(([n, l], i) => (
          <div key={n} className="v-db" style={{ animationDelay: `${i * 0.4}s` }}><Logo name={n} size={30} /><span>{l}</span><i style={{ animationDelay: `${i * 0.4}s` }} /></div>
        ))}
      </div>
    );
  }
  return (
    <div className="v-screen">
      <div className="v-bar"><i /><i /><i /></div>
      <div className="v-kpis">{[["In transit", 6357], ["Exposed", 759], ["SLA risk", 271]].map(([l, v], i) => (
        <div key={l}><span>{l}</span><b style={{ animationDelay: `${i * 0.2}s` }}>{v.toLocaleString("en-IN")}</b></div>))}</div>
      <div className="v-map">{Array.from({ length: 14 }, (_, i) => <i key={i} style={{ left: `${12 + ((i * 29) % 76)}%`, top: `${14 + ((i * 41) % 70)}%`, animationDelay: `${i * 0.21}s` }} />)}</div>
    </div>
  );
}

// ---------- the risk score assembling itself ----------
function RiskBuild({ compact }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((x) => (x + 1) % (RISK.length + 3)), 650);
    return () => clearInterval(t);
  }, []);
  const shown = Math.min(n, RISK.length);
  const total = RISK_SAMPLE.slice(0, shown).reduce((a, b) => a + b, 0);
  return (
    <div className={`v-risk${compact ? " compact" : ""}`}>
      <div className="v-risk-score"><b>{Math.round(total)}</b><span>/ 100</span>
        <em className={total >= 50 ? "on" : ""}>{total >= 75 ? "CRITICAL" : total >= 50 ? "HIGH" : total >= 25 ? "MEDIUM" : "LOW"}</em></div>
      <div className="v-risk-bar">{RISK_SAMPLE.map((v, i) => <i key={i} style={{ width: i < shown ? `${v}%` : 0, opacity: 1 - i * 0.1 }} />)}</div>
      {!compact ? (
        <div className="v-risk-rows">
          {RISK.map(([l, cap], i) => (
            <div key={l} className={i < shown ? "on" : ""}><span>{l}</span>
              <div className="bar"><i style={{ width: i < shown ? `${(RISK_SAMPLE[i] / cap) * 100}%` : 0 }} /></div>
              <span className="mono">+{RISK_SAMPLE[i]}</span><span className="faint mono">/{cap}</span></div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ---------- self-playing pipeline ----------
function Player() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const [cycle, setCycle] = useState(0);
  useEffect(() => {
    if (paused) return undefined;
    const t = setTimeout(() => { setI((x) => (x + 1) % STAGES.length); setCycle((c) => c + 1); }, STAGE_MS);
    return () => clearTimeout(t);
  }, [i, paused, cycle]);
  const s = STAGES[i];
  return (
    <div className="panel src-player" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <div className="src-track">
        <div className="src-line"><i className="flow" /><i className="flow f2" /><i className="flow f3" />
          <b style={{ width: `${(i / (STAGES.length - 1)) * 100}%` }} /></div>
        {STAGES.map((st, k) => (
          <button key={st.key} type="button" className={`src-node${k === i ? " on" : ""}${k < i ? " done" : ""}`}
            onClick={() => { setI(k); setCycle((c) => c + 1); }}>
            <span className="n">{k + 1}</span><span className="t">{st.title}</span>
          </button>
        ))}
      </div>
      <div className="src-progress"><i key={`${i}-${cycle}`} className={paused ? "paused" : ""} style={{ animationDuration: `${STAGE_MS}ms` }} /></div>
      <div className="src-stage" key={s.key}>
        <div className="src-stage-l">
          <div className="src-step-n">Step {i + 1} of {STAGES.length}</div>
          <h2>{s.title}</h2>
          <div className="src-tag">{s.tag}</div>
          <p>{s.text}</p>
          <div className="src-chips">{s.steps.map((c, k) => <span key={c} style={{ animationDelay: `${0.25 + k * 0.35}s` }}>{c}</span>)}</div>
          <div className="src-stats">{s.stat.map(([v, l]) => <div key={l}><b className="num">{v}</b><span>{l}</span></div>)}</div>
          <div className="src-uses">{s.logos.map((l) => <Logo key={l} name={l} size={26} />)}</div>
        </div>
        <div className="src-stage-r"><Visual k={s.key} /></div>
      </div>
      <div className="note src-hint">{paused ? "Paused — move away to keep playing, or click a step." : "Playing — hover to pause, click a step to jump."}</div>
    </div>
  );
}

function Reveal({ children, delay = 0 }) {
  const ref = useRef(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => setOn(e.isIntersecting), { threshold: 0.12 });
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);
  return <div ref={ref} className={`reveal${on ? " in" : ""}`} style={{ transitionDelay: `${delay}ms` }}>{children}</div>;
}

export default function Sources() {
  return (
    <main className="page src">
      <Hero />
      <Player />
      <Reveal>
        <div className="panel sect src-riskpanel">
          <div className="panel-h"><h2>How a risk score is built</h2></div>
          <div className="panel-b"><RiskBuild /></div>
        </div>
      </Reveal>
      <div className="sect">
        <h2 className="src-h2">Built with</h2>
        {STACK.map(([group, items], g) => (
          <div key={group} className="src-group">
            <div className="src-group-t">{group}</div>
            <div className="src-grid">
              {items.map(([logo, name, a, b], k) => (
                <Reveal key={logo} delay={k * 90}>
                  <div className="src-card" style={{ animationDelay: `${(g * 4 + k) * 0.35}s` }}>
                    <div className="src-card-logo" style={{ animationDelay: `${(g * 4 + k) * 0.27}s` }}><Logo name={logo} size={34} /></div>
                    <div><b>{name}</b><p>{a}<br />{b}</p></div>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
