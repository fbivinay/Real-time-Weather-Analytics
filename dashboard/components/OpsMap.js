"use client";

// India operations map. Every layer comes from local GeoJSON (Natural Earth
// basemap with India's official boundary, the logistics network exported by
// weatherops.network, live stations from the feed): no tile server, no key.

import { useEffect, useRef } from "react";

import { CATEGORY_LABEL, HAZARD_LABEL, categoryOf, readRiskColors } from "../lib/categories";
import { num } from "../lib/format";

const BOUNDS = [[67.5, 6.2], [98.2, 37.4]];
const EMPTY = { type: "FeatureCollection", features: [] };
const byCategory = (c, fallback) => ["match", ["get", "category"],
  "critical", c.critical, "high", c.high, "medium", c.medium, "low", c.low, fallback];

function buildStyle(c) {
  return {
    version: 8,
    sources: {
      india: { type: "geojson", data: "/india.geojson", attribution: "Basemap: Natural Earth" },
      network: { type: "geojson", data: "/network.geojson", promoteId: "id" },
      stations: { type: "geojson", data: EMPTY, attribution: "Weather: Open-Meteo.com (CC BY 4.0)" },
      incidents: { type: "geojson", data: EMPTY },
    },
    layers: [
      { id: "water", type: "background", paint: { "background-color": c.water } },
      { id: "neighbours", type: "fill", source: "india", filter: ["==", ["get", "layer"], "neighbour"],
        paint: { "fill-color": c.neighbour } },
      { id: "india", type: "fill", source: "india", filter: ["==", ["get", "layer"], "country"],
        paint: { "fill-color": c.land } },
      { id: "states", type: "line", source: "india", filter: ["==", ["get", "layer"], "state"],
        paint: { "line-color": c.state, "line-width": 0.6 } },
      { id: "border", type: "line", source: "india", filter: ["==", ["get", "layer"], "country"],
        paint: { "line-color": c.border, "line-width": 1 } },
      { id: "incident-halo", type: "circle", source: "incidents",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3.5, 22, 6, 46, 8, 90],
          "circle-color": byCategory(c, c.high), "circle-opacity": 0.13,
          "circle-stroke-color": byCategory(c, c.high), "circle-stroke-width": 1.5, "circle-stroke-opacity": 0.7,
        } },
      { id: "routes", type: "line", source: "network", filter: ["==", ["get", "layer"], "route"],
        layout: { "line-cap": "round", "line-join": "round",
                  "line-sort-key": ["case", ["==", ["get", "kind"], "linehaul"], 1, 0] },
        paint: {
          "line-color": ["match", ["coalesce", ["feature-state", "status"], "none"],
            "critical", c.critical, "high", c.high, "medium", c.medium, c.route],
          "line-width": ["case", ["==", ["get", "kind"], "linehaul"],
            ["match", ["coalesce", ["feature-state", "status"], "none"], "critical", 3.6, "high", 3, "medium", 2.2, 1.4],
            ["match", ["coalesce", ["feature-state", "status"], "none"], "critical", 2.6, "high", 2.2, "medium", 1.6, 0.8]],
        } },
      { id: "hubs", type: "symbol", source: "network", filter: ["==", ["get", "layer"], "hub"],
        layout: { "icon-image": "hub", "icon-allow-overlap": true, "icon-ignore-placement": true } },
      { id: "stations", type: "circle", source: "stations",
        layout: { "circle-sort-key": ["get", "score"] },
        paint: {
          "circle-radius": ["case", ["==", ["get", "kind"], "reference"],
            ["match", ["get", "category"], "critical", 9.5, "high", 8, "medium", 6.5, 5],
            ["match", ["get", "category"], "critical", 5.5, "high", 4.8, "medium", 4, 2.8]],
          "circle-color": byCategory(c, c.surface),
          "circle-stroke-color": ["case", ["get", "selected"], c.ink,
            ["==", ["get", "verdict"], "sensor_suspect"], c.ink, ["==", ["get", "verdict"], "stale"], c.unknown, c.surface],
          "circle-stroke-width": ["case", ["get", "selected"], 3,
            ["any", ["==", ["get", "verdict"], "sensor_suspect"], ["==", ["get", "verdict"], "stale"]], 2, 1.5],
        } },
    ],
  };
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

// Hubs are squares so they never read as a station or a flagged sensor.
function hubIcon(c) {
  const size = 12;
  const data = new Uint8Array(size * size * 4);
  const ink = hexToRgb(c.ink);
  const fill = hexToRgb(c.surface);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const edge = x < 2 || y < 2 || x >= size - 2 || y >= size - 2;
      data.set([...(edge ? ink : fill), 255], (y * size + x) * 4);
    }
  }
  return { width: size, height: size, data };
}

function stationFeatures(locations, selected) {
  return {
    type: "FeatureCollection",
    features: Object.values(locations).map((l) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [l.lon, l.lat] },
      properties: {
        id: l.station_id, name: l.name, kind: l.kind, category: categoryOf(l),
        score: l.assessment?.score ?? -1, hazard: l.assessment?.hazard ?? "",
        verdict: l.verdict?.status ?? "", selected: l.station_id === selected,
      },
    })),
  };
}

function incidentFeatures(incidents, locations) {
  return {
    type: "FeatureCollection",
    features: incidents
      .map((i) => ({ i, ref: locations[`REF-${i.region}`] }))
      .filter(({ ref }) => ref)
      .map(({ i, ref }) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [ref.lon, ref.lat] },
        properties: { id: i.id, category: i.category },
      })),
  };
}

export default function OpsMap({ locations, routes, incidents, selected, onSelect }) {
  const container = useRef(null);
  const mapRef = useRef(null);
  const live = useRef({ locations, routes, incidents, selected });
  live.current = { locations, routes, incidents, selected };

  function apply(map) {
    if (!map?.isStyleLoaded()) return;
    const { locations: locs, routes: rts, incidents: incs, selected: sel } = live.current;
    map.getSource("stations")?.setData(stationFeatures(locs, sel));
    map.getSource("incidents")?.setData(incidentFeatures(incs, locs));
    for (const [id, r] of Object.entries(rts)) {
      map.setFeatureState({ source: "network", id }, { status: r.status });
    }
  }

  useEffect(() => {
    let map;
    let cancelled = false;
    (async () => {
      const maplibregl = (await import("maplibre-gl")).default;
      if (cancelled || !container.current) return;
      map = new maplibregl.Map({
        container: container.current,
        style: buildStyle(readRiskColors()),
        bounds: BOUNDS,
        fitBoundsOptions: { padding: 12 },
        minZoom: 3,
        maxZoom: 10,
        dragRotate: false,
        pitchWithRotate: false,
        attributionControl: { compact: true },
      });
      map.touchZoomRotate.disableRotation();
      mapRef.current = map;
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });

      map.on("styleimagemissing", (e) => {
        if (e.id === "hub" && !map.hasImage("hub")) map.addImage("hub", hubIcon(readRiskColors()));
      });
      map.on("load", () => apply(map));
      map.on("sourcedata", (e) => {
        if (e.sourceId === "network" && e.isSourceLoaded) apply(map);
      });
      map.on("click", "stations", (e) => onSelect?.(e.features[0].properties.id));
      map.on("mousemove", "stations", (e) => {
        const p = e.features[0].properties;
        map.getCanvas().style.cursor = "pointer";
        popup.setLngLat(e.lngLat).setHTML(
          `<div class="pop-title">${p.name || p.id}</div>` +
          `<div>${CATEGORY_LABEL[p.category] ?? "No data"}${p.score >= 0 ? ` · risk ${p.score}` : ""}` +
          `${p.hazard ? ` · ${HAZARD_LABEL[p.hazard]}` : ""}</div>` +
          `<div class="pop-sub">${p.kind === "reference" ? "City reference (Open-Meteo)" : "Hub sensor (simulated)"}` +
          `${p.verdict === "sensor_suspect" ? " · suspect, excluded" : ""}</div>`,
        ).addTo(map);
      });
      map.on("mouseleave", "stations", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });
      map.on("mousemove", "routes", (e) => {
        if (map.queryRenderedFeatures(e.point, { layers: ["stations"] }).length) return;
        const p = e.features[0].properties;
        const r = live.current.routes[p.id];
        if (!r || r.status === "low" || r.status === "unknown") return popup.remove();
        popup.setLngLat(e.lngLat).setHTML(
          `<div class="pop-title">${p.name}</div>` +
          `<div>${CATEGORY_LABEL[r.status]} · ${num(r.at_risk)} ${p.kind === "linehaul" ? "trucks" : "deliveries"} at risk</div>` +
          `<div class="pop-sub">${num(Math.round(r.exposure * 100))}% of the route at High or above</div>`,
        ).addTo(map);
      });
      map.on("mouseleave", "routes", () => popup.remove());

      const scheme = window.matchMedia("(prefers-color-scheme: dark)");
      const restyle = () => {
        if (map.hasImage("hub")) map.removeImage("hub");
        map.setStyle(buildStyle(readRiskColors()));
        map.once("styledata", () => setTimeout(() => apply(map), 0));
      };
      scheme.addEventListener("change", restyle);
      map.on("remove", () => scheme.removeEventListener("change", restyle));
    })();
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    apply(mapRef.current);
  }, [locations, routes, incidents, selected]);

  return <div ref={container} className="map" role="region" aria-label="Map of India with risk by station and route" />;
}
