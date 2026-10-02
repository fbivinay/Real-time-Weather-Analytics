"use client";
// MapLibre over local GeoJSON only (India point-of-view boundaries, no tile
// server): state choropleth, cities sized by orders, warehouses, hubs, routes.
import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

const EMPTY = { type: "FeatureCollection", features: [] };

function bbox(geom) {
  let [x0, y0, x1, y1] = [180, 90, -180, -90];
  const walk = (c) => {
    if (typeof c[0] === "number") {
      x0 = Math.min(x0, c[0]); y0 = Math.min(y0, c[1]); x1 = Math.max(x1, c[0]); y1 = Math.max(y1, c[1]);
    } else c.forEach(walk);
  };
  walk(geom.coordinates);
  return [[x0, y0], [x1, y1]];
}

export default function IndiaMap({ stateColors, cities, routeColors, selected, onSelect, onHover, showRoutes = true,
  showHubs = true, focus }) {
  const box = useRef(null);
  const map = useRef(null);
  const geo = useRef({ states: {}, routes: {} });
  const [ready, setReady] = useState(false);
  const cb = useRef({ onSelect, onHover });
  cb.current = { onSelect, onHover };

  useEffect(() => {
    const m = new maplibregl.Map({
      container: box.current,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#e4e9ee" } }] },
      center: [80.5, 22.5], zoom: 3.9, minZoom: 3.3, maxZoom: 8.5, attributionControl: false,
      dragRotate: false, pitchWithRotate: false,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Natural Earth · Open-Meteo (CC BY 4.0)" }));
    m.touchZoomRotate.disableRotation();
    map.current = m;
    m.on("load", async () => {
      const [india, net] = await Promise.all([fetch("/india.geojson").then((r) => r.json()), fetch("/network.geojson").then((r) => r.json())]);
      india.features.forEach((f, i) => { f.id = i; if (f.properties.layer === "state") geo.current.states[f.properties.name] = f; });
      net.features.forEach((f) => { if (f.properties.layer === "route") geo.current.routes[f.properties.id] = f; });
      m.addSource("india", { type: "geojson", data: india });
      m.addSource("net", { type: "geojson", data: { type: "FeatureCollection", features: net.features.filter((f) => f.properties.layer !== "city") } });
      m.addSource("cities", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "neighbour", type: "fill", source: "india", filter: ["==", ["get", "layer"], "neighbour"], paint: { "fill-color": "#eef0f2", "fill-outline-color": "#d3d8dd" } });
      m.addLayer({ id: "country", type: "fill", source: "india", filter: ["==", ["get", "layer"], "country"], paint: { "fill-color": "#fbfbfb" } });
      m.addLayer({ id: "state-fill", type: "fill", source: "india", filter: ["==", ["get", "layer"], "state"],
        paint: { "fill-color": "#f6f6f6", "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.82, 1] } });
      m.addLayer({ id: "state-line", type: "line", source: "india", filter: ["==", ["get", "layer"], "state"], paint: { "line-color": "#ffffff", "line-width": 1 } });
      m.addLayer({ id: "state-sel", type: "line", source: "india", filter: ["==", ["get", "name"], ""], paint: { "line-color": "#0b0b0c", "line-width": 2 } });
      m.addLayer({ id: "country-line", type: "line", source: "india", filter: ["==", ["get", "layer"], "country"], paint: { "line-color": "#0b0b0c", "line-width": 1.1, "line-opacity": 0.75 } });
      m.addLayer({ id: "routes", type: "line", source: "net", filter: ["==", ["get", "layer"], "route"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#9aa0a8", "line-width": ["case", ["==", ["get", "role"], "primary"], 1.4, 0.8], "line-opacity": 0.55 } });
      m.addLayer({ id: "routes-hit", type: "line", source: "net", filter: ["==", ["get", "layer"], "route"], paint: { "line-color": "#000", "line-width": 10, "line-opacity": 0 } });
      m.addLayer({ id: "route-sel", type: "line", source: "net", filter: ["==", ["get", "id"], ""], layout: { "line-cap": "round" }, paint: { "line-color": "#0b0b0c", "line-width": 4 } });
      m.addLayer({ id: "cities", type: "circle", source: "cities",
        paint: { "circle-color": ["get", "color"], "circle-radius": ["get", "r"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5,
          "circle-opacity": 0.95 } });
      m.addLayer({ id: "city-sel", type: "circle", source: "cities", filter: ["==", ["get", "id"], ""],
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": ["+", ["get", "r"], 4], "circle-stroke-color": "#0b0b0c", "circle-stroke-width": 2 } });
      m.addLayer({ id: "hubs", type: "circle", source: "net", filter: ["==", ["get", "layer"], "hub"], minzoom: 4.6,
        paint: { "circle-color": "#ffffff", "circle-radius": 3, "circle-stroke-color": "#0b0b0c", "circle-stroke-width": 1.2 } });
      m.addLayer({ id: "warehouses", type: "circle", source: "net", filter: ["==", ["get", "layer"], "warehouse"],
        paint: { "circle-color": "#0b0b0c", "circle-radius": 5.5, "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });

      let hovered = null;
      const pick = ["warehouses", "hubs", "cities", "routes-hit", "state-fill"];
      m.on("mousemove", (e) => {
        const f = m.queryRenderedFeatures(e.point, { layers: pick })[0];
        if (hovered !== null) m.setFeatureState({ source: "india", id: hovered }, { hover: false });
        hovered = null;
        if (f && f.layer.id === "state-fill") { hovered = f.id; m.setFeatureState({ source: "india", id: f.id }, { hover: true }); }
        m.getCanvas().style.cursor = f ? "pointer" : "";
        cb.current.onHover?.(f ? { layer: f.layer.id, props: f.properties, x: e.point.x, y: e.point.y } : null);
      });
      m.on("mouseout", () => cb.current.onHover?.(null));
      m.on("click", (e) => {
        const f = m.queryRenderedFeatures(e.point, { layers: pick })[0];
        if (!f) return;
        const p = f.properties;
        const kind = { warehouses: "warehouse", hubs: "hub", cities: "city", "routes-hit": "route", "state-fill": "state" }[f.layer.id];
        cb.current.onSelect?.(kind, kind === "state" ? p.name : p.id);
      });
      setReady(true);
    });
    return () => m.remove();
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!ready || !stateColors) return;
    const expr = ["match", ["get", "name"]];
    Object.entries(stateColors).forEach(([name, color]) => expr.push(name, color));
    expr.push("#f6f6f6");
    m.setPaintProperty("state-fill", "fill-color", Object.keys(stateColors).length ? expr : "#f6f6f6");
  }, [ready, stateColors]);

  useEffect(() => {
    if (!ready) return;
    map.current.getSource("cities").setData({ type: "FeatureCollection", features: cities || [] });
  }, [ready, cities]);

  useEffect(() => {
    const m = map.current;
    if (!ready) return;
    if (routeColors && Object.keys(routeColors).length) {
      const expr = ["match", ["get", "id"]];
      Object.entries(routeColors).forEach(([id, c]) => expr.push(id, c));
      expr.push("#9aa0a8");
      m.setPaintProperty("routes", "line-color", expr);
      const wexpr = ["match", ["get", "id"]];
      Object.keys(routeColors).forEach((id) => wexpr.push(id, 2.6));
      wexpr.push(["case", ["==", ["get", "role"], "primary"], 1.2, 0.7]);
      m.setPaintProperty("routes", "line-width", wexpr);
      m.setPaintProperty("routes", "line-opacity", 0.85);
    } else {
      m.setPaintProperty("routes", "line-color", "#9aa0a8");
      m.setPaintProperty("routes", "line-opacity", 0.5);
    }
  }, [ready, routeColors]);

  useEffect(() => {
    const m = map.current;
    if (!ready) return;
    m.setLayoutProperty("routes", "visibility", showRoutes ? "visible" : "none");
    m.setLayoutProperty("hubs", "visibility", showHubs ? "visible" : "none");
  }, [ready, showRoutes, showHubs]);

  useEffect(() => {
    const m = map.current;
    if (!ready) return;
    const s = selected || {};
    m.setFilter("state-sel", ["==", ["get", "name"], s.kind === "state" ? s.id : focus?.state || ""]);
    m.setFilter("route-sel", ["==", ["get", "id"], s.kind === "route" ? s.id : ""]);
    m.setFilter("city-sel", ["==", ["get", "id"], s.kind === "city" ? s.id : focus?.city || ""]);
    if (s.kind === "state" && geo.current.states[s.id]) {
      m.fitBounds(bbox(geo.current.states[s.id].geometry), { padding: 60, duration: 700, maxZoom: 7 });
    } else if (s.kind === "route" && geo.current.routes[s.id]) {
      m.fitBounds(bbox(geo.current.routes[s.id].geometry), { padding: 120, duration: 700, maxZoom: 7.5 });
    } else if (s.lngLat) {
      m.flyTo({ center: s.lngLat, zoom: Math.max(m.getZoom(), 5.4), duration: 700 });
    } else if (!s.kind) {
      m.fitBounds([[68.5, 7.5], [97.2, 35.2]], { padding: 8, duration: 600 });
    }
  }, [ready, selected, focus]);

  return <div ref={box} style={{ position: "absolute", inset: 0 }} />;
}
