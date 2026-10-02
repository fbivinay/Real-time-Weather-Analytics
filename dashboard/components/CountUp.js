"use client";
// Site-wide count-up: every metric number runs from 0 to its value (0.8 s)
// each time it scrolls into view. Works on the rendered text, so tables,
// KPIs, pills and panels need no wiring. Dates, times, IDs and years are left alone.
import { usePathname } from "next/navigation";
import { useEffect } from "react";

const TARGETS = [
  ".kpi .v", ".kv .v", "td.num", "td.r", ".pill", ".alerts li", ".contrib .mono",
  ".num", ".headline .red", ".panel b", "td .mono",
].join(",");
const SKIP_TEXT = /(\d{1,2}:\d{2})|ORD-|R-[A-Z]{3}|\b(19|20)\d{2}\b|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|→/;
const NUMBER = /\d[\d,]*(?:\.\d+)?/g;
const DURATION = 800;

function textNodes(el) {
  const out = [];
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const n = walk.currentNode;
    if (/\d/.test(n.nodeValue) && !SKIP_TEXT.test(n.nodeValue)) out.push(n);
  }
  return out;
}

function formatLike(token, value) {
  const decimals = token.includes(".") ? token.split(".")[1].length : 0;
  if (token.includes(",")) {
    return value.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }
  return value.toFixed(decimals);
}

const running = new WeakMap();

function animate(el) {
  textNodes(el).forEach((node) => {
    const final = node.nodeValue;
    const tokens = [...final.matchAll(NUMBER)].map((m) => ({ s: m[0], v: parseFloat(m[0].replace(/,/g, "")), i: m.index }));
    if (!tokens.length || tokens.every((t) => !t.v)) return;
    const prev = running.get(node);
    if (prev) cancelAnimationFrame(prev.raf);
    const start = performance.now();
    let written = null;
    const render = (k) => {
      let out = "";
      let at = 0;
      for (const t of tokens) {
        out += final.slice(at, t.i) + formatLike(t.s, t.v * k);
        at = t.i + t.s.length;
      }
      return out + final.slice(at);
    };
    const step = (now) => {
      // React re-rendered this text (live tick): its value wins, stop here.
      if (written !== null && node.nodeValue !== written) { running.delete(node); return; }
      const k = Math.min(1, (now - start) / DURATION);
      const e = 1 - (1 - k) ** 3;
      written = k < 1 ? render(e) : final;
      node.nodeValue = written;
      if (k < 1) running.set(node, { raf: requestAnimationFrame(step) });
      else running.delete(node);
    };
    running.set(node, { raf: requestAnimationFrame(step) });
  });
}

export default function CountUp() {
  const path = usePathname();
  useEffect(() => {
    const seen = new WeakSet();
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) animate(e.target); });
    }, { threshold: 0.15 });
    let timer;
    const scan = () => {
      document.querySelectorAll(`main ${TARGETS.split(",").join(", main ")}, .drawer .kv .v, .drawer td.r`).forEach((el) => {
        if (!seen.has(el)) { seen.add(el); io.observe(el); }
      });
    };
    scan();
    // The opening loader covers the page: count again the moment it lifts.
    const reveal = () => document.querySelectorAll(`main ${TARGETS.split(",").join(", main ")}`).forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.bottom > 0 && r.top < window.innerHeight) animate(el);
    });
    window.addEventListener("weatherops:reveal", reveal);
    const mo = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 60); });
    mo.observe(document.body, { childList: true, subtree: true });
    return () => { io.disconnect(); mo.disconnect(); clearTimeout(timer); window.removeEventListener("weatherops:reveal", reveal); };
  }, [path]);
  return null;
}
