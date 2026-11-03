import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type {
  GeoJSONSource,
  Map as MapLibreMap,
  MapMouseEvent,
} from "maplibre-gl";
// MapLibre 6 loads its tile worker from a sibling file at runtime. Vite only
// rewrites worker references it can see, so a plain build points at a URL that
// was never emitted: no tiles, no `load` event, no camera animation. Importing
// through `?worker&url` makes Vite emit the worker and hand back its URL.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { Protocol } from "pmtiles";
import { cellToBoundary } from "h3-js";

import type { Coverage, Hotspot, RiskLevel, Route, SafeStop } from "@/lib/contract";
import type { Point } from "@/lib/api";
import { RISK_COLORS, formatDuration } from "@/lib/risk";
import { segmentPaths, type LonLat } from "@/lib/route-geometry";
import { routeLabel } from "@/lib/routes";
import { stopKey, stopMeta } from "@/lib/stops";
import { SunLighting } from "@/map/SunLighting";
import { globeFramingAt, subsolarPoint } from "@/map/sun";
import { WORLD_COORDINATES, paintNightTexture, sunElevation } from "@/map/daynight";
import { applyBasemapTheme, themedStyle, type MapTheme } from "@/map/theme";

const RISK_SOURCE = "risk-hexes";
const RISK_FILL = "risk-hexes-fill";
const RISK_OUTLINE = "risk-hexes-outline";
const RISK_SELECTED = "risk-hexes-selected";
const HOTSPOT_SOURCE = "hotspots";
const HOTSPOT_LAYER = "hotspots-dots";
const SEGMENT_SOURCE = "route-segments";
const SEGMENT_CASING = "route-segments-casing";
const SEGMENT_LINE = "route-segments-line";
const SELECTED_SOURCE = "selected-segment";
const SELECTED_LINE = "selected-segment-line";
// Routes other than the selected one: a quiet dashed line you can click.
const ALT_CASING = "route-alt-casing";
const ALT_LINE = "route-alt-line";
const STOP_SOURCE = "safe-stops";
const STOP_LAYER = "safe-stops-dots";
const NIGHT_SOURCE = "night-side";
const NIGHT_LAYER = "night-side-shade";

/**
 * Below this zoom the view spans day and night, so the map keeps the day
 * palette and the shadow layer draws the night side. Above it the view is one
 * place at one time of day, and the whole palette follows that place's sun.
 */
const THEME_SWITCH_ZOOM = 5.6;

/** Idle spin at globe zoom: degrees per second, and how long to wait after input. */
const SPIN_DEG_PER_S = 3;
const SPIN_IDLE_MS = 4000;
const SPIN_MAX_ZOOM = 3;
/** Back off when the median frame takes longer than this (about 24 fps)... */
const SPIN_MAX_FRAME_MS = 42;
/** ...measured over this many spinning frames (or this long), twice in a row. */
const SPIN_WINDOW_FRAMES = 45;
const SPIN_WINDOW_MS = 2500;

/**
 * Data-layer colours that must change with the basemap to keep their contrast.
 * `dim` quiets the risk surface while routes are on screen, so the routes lead.
 */
function applyDataTheme(map: MapLibreMap, theme: MapTheme, dim = false) {
  const light = theme === "light";
  const set = (layer: string, property: string, value: unknown) => {
    if (map.getLayer(layer)) {
      map.setPaintProperty(layer, property as Parameters<MapLibreMap["setPaintProperty"]>[1], value as never);
    }
  };
  // The surface now tiles the whole city, so it is drawn lighter than the old
  // scattered sample was: the streets have to stay readable under it.
  const fill = dim ? (light ? 0.14 : 0.1) : light ? 0.3 : 0.22;
  set(RISK_FILL, "fill-opacity", ["case", ["==", ["get", "coverage"], "rule_based_only"], light ? 0.12 : 0.08, fill]);
  set(RISK_OUTLINE, "line-opacity", dim ? (light ? 0.22 : 0.14) : light ? 0.5 : 0.3);
  set(RISK_SELECTED, "line-color", light ? "#15181b" : "#f4f0e6");
  set(SELECTED_LINE, "line-color", light ? "#15181b" : "#ffffff");
  set(ALT_CASING, "line-color", light ? "#ffffff" : "#020407");
  set(ALT_CASING, "line-opacity", light ? 0.8 : 0.55);
}

// Must be set before the first Map is constructed; the worker pool is global.
maplibregl.setWorkerUrl(maplibreWorkerUrl);

let protocolReady = false;

function ensurePmtilesProtocol() {
  if (protocolReady) return;
  try {
    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);
  } catch {
    // A hot reload can leave the protocol registered. The map still works.
  }
  protocolReady = true;
}

export type SegmentSelection = {
  routeIndex: number;
  segmentIndex: number;
} | null;

/** A place the camera should dive to, bumped with a new `key` to replay. */
export type RevealTarget = {
  key: number;
  lat: number;
  lon: number;
  /** Where the descent stops; street level when omitted. */
  zoom?: number;
};

/** One hex of the risk surface: a model cell, or a sample of the global estimate. */
export type MapCell = {
  cell: string | null;
  lat: number;
  lon: number;
  /** Neighbourhood name, for the hover label (model cells only). */
  name?: string | null;
  risk_level: RiskLevel;
  risk_score: number | null;
  percentile: number | null;
  coverage: Coverage;
};

type MapCanvasProps = {
  point: Point;
  cells: MapCell[];
  hotspots: Hotspot[];
  routes: Route[];
  selectedRoute: number;
  selectedSegment: SegmentSelection;
  selectedCell: string | null;
  onSelectCell: (cell: string) => void;
  onSelectSegment: (routeIndex: number, segmentIndex: number) => void;
  onMapClick: (point: Point) => void;
  fitRoutesKey: string | null;
  expectRoute: boolean;
  /**
   * The instant the sun is drawn for (epoch ms): the real clock in live mode,
   * or the time-dock hour when the sun follows the dock. Drives the sun, the
   * night side and the light or dark theme.
   */
  sunTime: number;
  /** Latest place chosen in the planner; drives the reveal animation. */
  reveal: RevealTarget | null;
  /** Only Explore owns the camera when no route is being fitted. */
  exploreMode: boolean;
  /** Choose another route by clicking its line or its chip on the map. */
  onSelectRoute: (routeIndex: number) => void;
  /** Safe stops to draw; an empty list draws none. */
  stops: SafeStop[];
  selectedStopKey: string | null;
  onSelectStop: (key: string | null) => void;
  /** Place B of the compare tool, drawn beside the selected point as place A. */
  compareTarget: Point | null;
  /** While true the next click chooses place B and must not move the point. */
  picking: boolean;
};

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    properties: Record<string, string | number | boolean | null>;
    geometry:
      | { type: "Polygon"; coordinates: number[][][] }
      | { type: "LineString"; coordinates: number[][] }
      | { type: "Point"; coordinates: number[] };
  }>;
};

function emptyCollection(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

/**
 * H3 boundaries never change, but `cellToBoundary` is not cheap and the same
 * 1120 cells are rebuilt every time the hour changes. Cache the closed ring in
 * GeoJSON winding order (lon, lat).
 */
const hexRings = new Map<string, Array<[number, number]>>();

function hexRing(cell: string): Array<[number, number]> | null {
  const cached = hexRings.get(cell);
  if (cached) return cached;
  let boundary: Array<[number, number]>;
  try {
    boundary = cellToBoundary(cell) as Array<[number, number]>;
  } catch {
    return null;
  }
  if (!boundary.length) return null;
  const ring = boundary.map(([lat, lon]) => [lon, lat] as [number, number]);
  ring.push(ring[0]!);
  hexRings.set(cell, ring);
  return ring;
}

function hexCollection(cells: MapCell[]): FeatureCollection {
  const seen = new Set<string>();
  const features: FeatureCollection["features"] = [];
  for (const cell of cells) {
    if (!cell.cell || seen.has(cell.cell)) continue;
    seen.add(cell.cell);
    const ring = hexRing(cell.cell);
    if (!ring) continue;
    features.push({
      type: "Feature",
      properties: {
        cell: cell.cell,
        name: cell.name ?? null,
        level: cell.risk_level,
        score: cell.risk_score,
        percentile: cell.percentile,
        coverage: cell.coverage,
      },
      geometry: { type: "Polygon", coordinates: [ring] },
    });
  }
  return { type: "FeatureCollection", features };
}

function hotspotCollection(hotspots: Hotspot[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: hotspots.map((spot) => ({
      type: "Feature",
      properties: {
        rank: spot.rank,
        crash_count: spot.crash_count,
        level: spot.risk_level,
      },
      geometry: { type: "Point", coordinates: [spot.lon, spot.lat] },
    })),
  };
}

function stopCollection(
  stops: SafeStop[],
  selectedKey: string | null,
): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: stops.map((stop) => {
      const key = stopKey(stop);
      return {
        type: "Feature",
        properties: {
          key,
          name: stop.name,
          color: stopMeta(stop.type).color,
          selected: key === selectedKey,
        },
        geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
      };
    }),
  };
}

function segmentCollection(
  routes: Route[],
  selectedRoute: number,
): FeatureCollection {
  const features: FeatureCollection["features"] = [];
  routes.forEach((route, routeIndex) => {
    // Each stretch follows the road between its ends (see segmentPaths); a
    // straight line from entry to exit cut across rivers and blocks.
    const paths = segmentPaths(route);
    route.segments.forEach((segment, segmentIndex) => {
      features.push({
        type: "Feature",
        properties: {
          routeIndex,
          segmentIndex,
          bucket: segment.bucket,
          selected: routeIndex === selectedRoute,
        },
        geometry: {
          type: "LineString",
          coordinates: paths[segmentIndex] ?? [
            [segment.start[1], segment.start[0]],
            [segment.end[1], segment.end[0]],
          ],
        },
      });
    });
  });
  return { type: "FeatureCollection", features };
}

/** The road-following line of one selected stretch, or null. */
function selectedSegmentPath(routes: Route[], selection: SegmentSelection): LonLat[] | null {
  if (!selection) return null;
  const route = routes[selection.routeIndex];
  if (!route || !route.segments[selection.segmentIndex]) return null;
  return segmentPaths(route)[selection.segmentIndex] ?? null;
}

function selectedSegmentCollection(
  routes: Route[],
  selection: SegmentSelection,
): FeatureCollection {
  const path = selectedSegmentPath(routes, selection);
  if (!path) return emptyCollection();
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { selected: true },
        geometry: { type: "LineString", coordinates: path },
      },
    ],
  };
}

function lineColorExpression() {
  return [
    "match",
    ["get", "bucket"],
    "low",
    "#59e3b0",
    "medium",
    "#f3c15d",
    "high",
    "#ff8a4c",
    "extreme",
    "#ff5268",
    "#748295",
  ];
}

function fillColorExpression() {
  return [
    "case",
    ["==", ["get", "coverage"], "rule_based_only"],
    "#4d5a6b",
    ["match",
      ["get", "level"],
      "LOW", RISK_COLORS.LOW,
      "MEDIUM", RISK_COLORS.MEDIUM,
      "HIGH", RISK_COLORS.HIGH,
      "CRITICAL", RISK_COLORS.CRITICAL,
      RISK_COLORS.UNKNOWN,
    ],
  ];
}

function routeBounds(routes: Route[], selectedRoute: number) {
  const route = routes[selectedRoute] ?? routes[0];
  if (!route || route.geometry.length === 0) return null;
  const bounds = new maplibregl.LngLatBounds();
  for (const [lat, lon] of route.geometry) bounds.extend([lon, lat]);
  return bounds;
}

const GLOBE_HOLD_MS = 900;
const REGIONAL_ZOOM = 6.2;

/** Keep the globe comfortably framed from phone to projector. */
function globeZoom() {
  const width = window.innerWidth;
  if (width < 600) return 0.95;
  if (width < 1000) return 1.35;
  if (width < 1500) return 1.8;
  if (width < 1800) return 2;
  return 2.15;
}

/**
 * The part of the map not covered by the app's panels, as insets in pixels.
 *
 * Present mode puts a rail across the top right of the map. The route used to
 * be framed as if it were not there, so at "Explain the worst segment" the
 * segment (and the route's start) sat behind the rail. The rail is measured,
 * not assumed: its height depends on the caption.
 */
function routeInsets(map?: MapLibreMap) {
  const wide = window.innerWidth > 900;
  const inset = { left: wide ? 440 : 24, right: wide ? 80 : 24, top: 96, bottom: 150 };
  const container = map?.getContainer();
  const rail = container
    ?.closest(".app-shell")
    ?.querySelector<HTMLElement>(".present-rail");
  if (container && rail) {
    const below = rail.getBoundingClientRect().bottom - container.getBoundingClientRect().top;
    inset.top = Math.max(inset.top, Math.round(below + 18));
  }
  return inset;
}

function mercatorY(lat: number) {
  const clamped = Math.max(-85.051129, Math.min(85.051129, lat));
  return Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360));
}

function inverseMercatorY(y: number) {
  return ((Math.atan(Math.exp(y)) - Math.PI / 4) * 360) / Math.PI;
}

/**
 * Camera that frames the route in the map area not covered by the planner
 * panel. Computed directly in Web Mercator so the result does not depend on
 * the projection the camera happens to be in when the intro starts.
 */
function routeCamera(map: MapLibreMap, bounds: maplibregl.LngLatBounds, maxZoom = 14) {
  const width = map.getContainer().clientWidth;
  const height = map.getContainer().clientHeight;
  const inset = routeInsets(map);
  const availableWidth = Math.max(1, width - inset.left - inset.right);
  const availableHeight = Math.max(1, height - inset.top - inset.bottom);
  const sw = bounds.getSouthWest();
  const ne = bounds.getNorthEast();
  const spanX = Math.max(1e-9, (ne.lng - sw.lng) / 360);
  const spanY = Math.max(
    1e-9,
    (mercatorY(ne.lat) - mercatorY(sw.lat)) / (2 * Math.PI),
  );
  const zoom = Math.min(
    Math.log2((availableWidth * 0.9) / (512 * spanX)),
    Math.log2((availableHeight * 0.9) / (512 * spanY)),
    maxZoom,
  );
  const centerLng = (sw.lng + ne.lng) / 2;
  const centerLat = inverseMercatorY(
    (mercatorY(sw.lat) + mercatorY(ne.lat)) / 2,
  );
  const worldSize = 512 * 2 ** zoom;
  const centerX = centerLng / 360 + 0.5;
  const centerY = 0.5 - mercatorY(centerLat) / (2 * Math.PI);
  const dx = inset.left + availableWidth / 2 - width / 2;
  const dy = inset.top + availableHeight / 2 - height / 2;
  const targetX = centerX - dx / worldSize;
  const targetY = centerY - dy / worldSize;
  const targetLng = (targetX - 0.5) * 360;
  const targetLat = inverseMercatorY((0.5 - targetY) * 2 * Math.PI);
  return { center: [targetLng, targetLat] as [number, number], zoom };
}

function fitRouteBounds(
  map: MapLibreMap,
  bounds: maplibregl.LngLatBounds,
  duration: number,
) {
  const camera = routeCamera(map, bounds);
  devLog("fit", { camera, duration });
  map.easeTo({
    center: camera.center,
    zoom: camera.zoom,
    bearing: 0,
    pitch: 0,
    duration,
  });
}

function globeTarget(
  map: MapLibreMap,
  bounds: maplibregl.LngLatBounds | null,
  point: Point,
) {
  if (bounds) return routeCamera(map, bounds);
  return { center: [point.lon, point.lat] as [number, number], zoom: 10.7 };
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

type TimerRef = { current: number | null };
type FlagRef = { current: boolean };

function devLog(message: string, data?: unknown) {
  if (!import.meta.env.DEV) return;
  const debugWindow = window as Window & {
    __soberLog?: Array<{ t: number; message: string; data?: unknown }>;
  };
  if (!debugWindow.__soberLog) debugWindow.__soberLog = [];
  debugWindow.__soberLog.push({
    t: Math.round(performance.now()),
    message,
    data,
  });
  if (debugWindow.__soberLog.length > 200) debugWindow.__soberLog.shift();
}

/**
 * Pull the camera out to a full globe, hold for a beat, then dive into the
 * route. The initial load already starts at globe zoom, so the jump is
 * invisible there; clicking the replay control performs the same reset from
 * street level.
 *
 * The globe is framed on the terminator rather than on a fixed longitude, so
 * the hero shot always shows a lit hemisphere and a night hemisphere at the
 * hour the dock is displaying. A fixed centre would go black at night.
 */
function playGlobeIntro(
  map: MapLibreMap,
  bounds: maplibregl.LngLatBounds | null,
  point: Point,
  timerRef: TimerRef,
  runningRef: FlagRef,
  pendingRef: { current: maplibregl.LngLatBounds | null },
  sunTime: number,
  motionRef: { current: number },
) {
  if (timerRef.current !== null) {
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }
  const token = ++motionRef.current;
  map.stop();
  runningRef.current = true;
  const target = globeTarget(map, bounds, point);
  devLog("intro:start", { target, currentZoom: map.getZoom() });

  // On a phone the planner sheet covers most of the map, so a globe held
  // behind it would be invisible. Go straight to the framed route instead.
  if (prefersReducedMotion() || window.innerWidth <= 600) {
    map.jumpTo({ ...target, bearing: 0, pitch: 0 });
    runningRef.current = false;
    return;
  }

  map.jumpTo({
    center: globeFramingAt(new Date(sunTime)),
    zoom: globeZoom(),
    bearing: 0,
    pitch: 0,
  });
  timerRef.current = window.setTimeout(() => {
    timerRef.current = null;
    if (token !== motionRef.current) return;
    const finish = () => {
      if (token !== motionRef.current) return;
      runningRef.current = false;
      devLog("intro:finish", { zoom: map.getZoom() });
      const pending = pendingRef.current;
      pendingRef.current = null;
      if (pending) fitRouteBounds(map, pending, 700);
    };
    // Two legs keep the camera over the local extract while it zooms in:
    // globe -> regional view centred on the trip -> street-level route.
    map.flyTo({
      center: target.center,
      zoom: Math.min(REGIONAL_ZOOM, target.zoom),
      bearing: 0,
      pitch: 0,
      duration: 1400,
      curve: 1.5,
      essential: true,
    });
    map.once("moveend", () => {
      if (token !== motionRef.current) return;
      devLog("intro:leg2", { zoom: map.getZoom() });
      map.flyTo({
        center: target.center,
        zoom: target.zoom,
        bearing: 0,
        pitch: 0,
        duration: 1500,
        curve: 1.25,
        essential: true,
      });
      map.once("moveend", finish);
    });
  }, GLOBE_HOLD_MS);
}

/**
 * Cinematic reveal for a place picked in the planner.
 *
 * Pull the camera all the way out and rotate the planet until the chosen
 * street faces the viewer, hold for a beat, then descend in two legs: regional
 * first, street last. One long fly from globe to z15 crosses too many zoom
 * levels in a single Mercator interpolation and reads as a lurch; splitting it
 * keeps the ground under the camera for the whole descent.
 */
function playLocationReveal(
  map: MapLibreMap,
  target: RevealTarget,
  timerRef: TimerRef,
  motionRef: { current: number },
) {
  if (timerRef.current !== null) {
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }
  const token = ++motionRef.current;
  map.stop();

  const center: [number, number] = [target.lon, target.lat];
  const streetZoom = target.zoom ?? 15.05;
  const current = map.getCenter();
  const alreadyThere =
    map.getZoom() > 12.5 &&
    Math.abs(current.lng - target.lon) < 0.002 &&
    Math.abs(current.lat - target.lat) < 0.002;

  if (prefersReducedMotion()) {
    map.jumpTo({ center, zoom: streetZoom, bearing: 0, pitch: 0 });
    return;
  }
  if (alreadyThere) return;

  // Close by (same city or region): no trip out to the globe, just go there.
  const near =
    map.getZoom() >= 4 &&
    Math.hypot(current.lng - target.lon, current.lat - target.lat) < 6;
  if (near) {
    map.flyTo({ center, zoom: streetZoom, bearing: 0, pitch: 0, duration: 1400, curve: 1.3, essential: true });
    return;
  }

  // Leg 1: out to the globe, rotating to the target's meridian. `flyTo` keeps
  // the surface moving under the camera, which is what reads as the earth
  // turning rather than a cross-fade.
  map.flyTo({
    center,
    zoom: globeZoom(),
    bearing: 0,
    pitch: 0,
    duration: 900,
    curve: 1.7,
    essential: true,
  });

  timerRef.current = window.setTimeout(() => {
    timerRef.current = null;
    if (token !== motionRef.current) return;
    // Leg 2: regional, so the coastline and street grid resolve before the
    // final descent.
    map.flyTo({
      center,
      zoom: 9.4,
      bearing: 0,
      pitch: 0,
      duration: 900,
      curve: 1.55,
      essential: true,
    });
    map.once("moveend", () => {
      if (token !== motionRef.current) return;
      // Leg 3: down to the street, slowly, with almost no curve so the last
      // few zoom levels feel like a descent instead of a swoop.
      map.flyTo({
        center,
        zoom: streetZoom,
        bearing: 0,
        pitch: 0,
        duration: 1100,
        curve: 1.08,
        essential: true,
      });
    });
  }, 900 + 150);
}

/** Below this zoom the map is a whole-planet view and a click means "go there". */
const GLOBE_CLICK_ZOOM = 6;

/**
 * Click on the zoomed-out globe: turn the planet until the clicked spot faces
 * the viewer, then descend to the neighbourhood. Two legs, not one `flyTo`, so
 * the rotation reads as the earth turning before any zoom begins.
 */
function playGlobeClickZoom(
  map: MapLibreMap,
  lngLat: { lng: number; lat: number },
  timerRef: TimerRef,
  motionRef: { current: number },
) {
  if (timerRef.current !== null) {
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }
  const token = ++motionRef.current;
  map.stop();
  const center: [number, number] = [lngLat.lng, lngLat.lat];
  const arrivalZoom = 11;

  if (prefersReducedMotion()) {
    map.jumpTo({ center, zoom: arrivalZoom, bearing: 0, pitch: 0 });
    return;
  }

  // Leg 1: rotate only. Centre-anchored, since MapLibre does not support
  // easing around a point on the globe projection.
  map.easeTo({
    center,
    bearing: 0,
    pitch: 0,
    duration: 1000,
    easing: (t) => 1 - Math.pow(1 - t, 3),
    essential: true,
  });
  map.once("moveend", () => {
    if (token !== motionRef.current) return;
    // Leg 2: descend. A short beat first lets the eye register the spot.
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (token !== motionRef.current) return;
      map.flyTo({
        center,
        zoom: arrivalZoom,
        bearing: 0,
        pitch: 0,
        duration: 2200,
        curve: 1.3,
        essential: true,
      });
    }, 180);
  });
}

export function MapCanvas({
  point,
  cells,
  hotspots,
  routes,
  selectedRoute,
  selectedSegment,
  selectedCell,
  onSelectCell,
  onSelectSegment,
  onMapClick,
  fitRoutesKey,
  expectRoute,
  sunTime,
  reveal,
  exploreMode,
  onSelectRoute,
  stops,
  selectedStopKey,
  onSelectStop,
  compareTarget,
  picking,
}: MapCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const chipMarkersRef = useRef<maplibregl.Marker[]>([]);
  const compareMarkersRef = useRef<maplibregl.Marker[]>([]);
  const [ready, setReady] = useState(false);
  const sunTimeRef = useRef(sunTime);
  sunTimeRef.current = sunTime;
  const callbacksRef = useRef({
    onSelectCell,
    onSelectSegment,
    onMapClick,
    onSelectRoute,
    onSelectStop,
  });
  callbacksRef.current = {
    onSelectCell,
    onSelectSegment,
    onMapClick,
    onSelectRoute,
    onSelectStop,
  };
  const pickingRef = useRef(picking);
  pickingRef.current = picking;
  /** Explore owns the hover labels; Plan dims the surface under its routes. */
  const exploreModeRef = useRef(exploreMode);
  exploreModeRef.current = exploreMode;
  const pointMarkerRef = useRef<maplibregl.Marker | null>(null);
  const tipRef = useRef<maplibregl.Popup | null>(null);
  /** The last place a reveal flew to, so the explore pan does not fight it. */
  const revealRef = useRef<RevealTarget | null>(null);
  /** The palette on screen; the map is constructed in the day palette. */
  const themeRef = useRef<MapTheme>("light");
  const syncThemeRef = useRef<(() => void) | null>(null);
  const nightCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const introPlayedRef = useRef(false);
  const introRunningRef = useRef(false);
  const introTimerRef = useRef<number | null>(null);
  const pendingFitRef = useRef<maplibregl.LngLatBounds | null>(null);
  /** Monotonic id for the current camera animation; stale legs bail out. */
  const motionRef = useRef(0);
  const dataRef = useRef({ point, routes, selectedRoute, fitRoutesKey });
  dataRef.current = { point, routes, selectedRoute, fitRoutesKey };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    ensurePmtilesProtocol();
    const map = new maplibregl.Map({
      container: containerRef.current,
      // Start in the day palette: the opening shot is the globe, where the
      // night side is drawn by the shadow layer rather than by the palette.
      style: themedStyle("light"),
      center: globeFramingAt(new Date(sunTimeRef.current)),
      zoom: globeZoom(),
      minZoom: 0.3,
      maxZoom: 16,
      attributionControl: false,
      pitchWithRotate: false,
      dragRotate: false,
      // Cap the drawing buffer on high-DPI screens; a 4K projector would
      // otherwise render four times the pixels for no visible gain.
      pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
      refreshExpiredTiles: false,
    });
    mapRef.current = map;
    devLog("map:create", {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    if (import.meta.env.DEV) {
      const debugWindow = window as Window & {
        __soberMap?: MapLibreMap;
        __soberGlobe?: () => void;
      };
      debugWindow.__soberMap = map;
      debugWindow.__soberGlobe = () => {
        if (introTimerRef.current !== null) {
          window.clearTimeout(introTimerRef.current);
          introTimerRef.current = null;
        }
        motionRef.current += 1;
        map.stop();
        map.jumpTo({
          center: globeFramingAt(new Date(sunTimeRef.current)),
          zoom: globeZoom(),
          bearing: 0,
          pitch: 0,
        });
      };
    }

    const replayContainer = document.createElement("div");
    replayContainer.className = "maplibregl-ctrl maplibregl-ctrl-group";
    const replayButton = document.createElement("button");
    replayButton.type = "button";
    replayButton.className = "globe-replay-btn";
    replayButton.title = "Replay globe intro";
    replayButton.setAttribute("aria-label", "Replay globe intro");
    replayButton.innerHTML =
      '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M3 12h18"></path><path d="M12 3c2.6 2.7 4 5.7 4 9s-1.4 6.3-4 9c-2.6-2.7-4-5.7-4-9s1.4-6.3 4-9z"></path></svg>';
    replayContainer.appendChild(replayButton);
    const handleReplay = () => {
      devLog("replay:click");
      const data = dataRef.current;
      const bounds = data.fitRoutesKey
        ? routeBounds(data.routes, data.selectedRoute)
        : null;
      playGlobeIntro(
        map,
        bounds,
        data.point,
        introTimerRef,
        introRunningRef,
        pendingFitRef,
        sunTimeRef.current,
        motionRef,
      );
    };
    replayButton.addEventListener("click", handleReplay);
    map.addControl(
      {
        onAdd: () => replayContainer,
        onRemove: () => replayContainer.remove(),
      },
      "bottom-right",
    );

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial", maxWidth: 90 }), "bottom-right");
    map.addControl(
      // Each source declares its own attribution, so the control collects the
      // local extract and the global planet credits without duplicating either.
      new maplibregl.AttributionControl({ compact: true }),
      // Bottom-left, clear of the zoom controls and the legend on the right.
      "bottom-left",
    );

    map.on("load", () => {
      map.setProjection({ type: "globe" });
      // Only touch the marker DOM when the visibility flips, not every frame
      // of a zoom animation.
      let markersVisible: boolean | null = null;
      // Set the starfield's own opacity. A custom property on .map-stage would be
      // inherited by every element under it and restyle the whole map each step.
      const starfield =
        containerRef.current?.parentElement?.querySelector<HTMLElement>(".starfield") ?? null;
      let lastStars = -1;
      const syncStars = () => {
        // Stars belong to space: full at globe zoom, gone by the time streets appear.
        const stars = Math.min(1, Math.max(0, (4.6 - map.getZoom()) / 1.6));
        const rounded = Math.round(stars * 20) / 20;
        if (rounded === lastStars || !starfield) return;
        lastStars = rounded;
        starfield.style.opacity = String(rounded);
      };
      syncStars();
      map.on("zoom", () => {
        syncStars();
        const visible = map.getZoom() >= 7.4;
        if (visible === markersVisible) return;
        markersVisible = visible;
        markersRef.current.forEach((marker) => {
          marker.setOpacity(visible ? 1 : 0);
        });
      });
      map.addSource(RISK_SOURCE, { type: "geojson", data: emptyCollection() });
      map.addSource(HOTSPOT_SOURCE, { type: "geojson", data: emptyCollection() });
      map.addSource(SEGMENT_SOURCE, { type: "geojson", data: emptyCollection() });
      map.addSource(SELECTED_SOURCE, { type: "geojson", data: emptyCollection() });
      map.addSource(STOP_SOURCE, { type: "geojson", data: emptyCollection() });

      // Night side: a world texture of the earth's shadow, above the basemap
      // and below every data layer added after it. It fades out by street zoom,
      // where the whole view is one time of day and the palette takes over.
      const nightCanvas = document.createElement("canvas");
      paintNightTexture(nightCanvas, subsolarPoint(new Date(sunTimeRef.current)));
      nightCanvasRef.current = nightCanvas;
      map.addSource(NIGHT_SOURCE, {
        type: "image",
        url: nightCanvas.toDataURL("image/png"),
        coordinates: WORLD_COORDINATES,
      });
      map.addLayer({
        id: NIGHT_LAYER,
        type: "raster",
        source: NIGHT_SOURCE,
        paint: {
          "raster-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            THEME_SWITCH_ZOOM - 0.6,
            1,
            THEME_SWITCH_ZOOM + 1,
            0,
          ],
          "raster-resampling": "linear",
          "raster-fade-duration": 0,
        },
      });

      map.addLayer({
        id: RISK_FILL,
        type: "fill",
        source: RISK_SOURCE,
        paint: {
          "fill-color": fillColorExpression() as never,
          "fill-opacity": [
            "case",
            ["==", ["get", "coverage"], "rule_based_only"],
            0.08,
            0.28,
          ],
        },
      });
      map.addLayer({
        id: RISK_OUTLINE,
        type: "line",
        source: RISK_SOURCE,
        paint: {
          "line-color": fillColorExpression() as never,
          "line-width": 0.7,
          "line-opacity": 0.48,
        },
      });
      map.addLayer({
        id: RISK_SELECTED,
        type: "line",
        source: RISK_SOURCE,
        filter: ["==", ["get", "cell"], ""],
        paint: { "line-color": "#f4f0e6", "line-width": 2.2 },
      });
      // Unselected routes first so the selected one is drawn on top of them.
      map.addLayer({
        id: ALT_CASING,
        type: "line",
        source: SEGMENT_SOURCE,
        filter: ["!=", ["get", "routeIndex"], selectedRoute],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#020407", "line-width": 6, "line-opacity": 0.55 },
      });
      map.addLayer({
        id: ALT_LINE,
        type: "line",
        source: SEGMENT_SOURCE,
        filter: ["!=", ["get", "routeIndex"], selectedRoute],
        layout: { "line-cap": "butt", "line-join": "round" },
        paint: {
          // Every route shows its own green-to-red severity; the alternatives
          // are dashed and lighter so the selected one still leads.
          "line-color": lineColorExpression() as never,
          "line-width": 3.5,
          "line-opacity": 0.75,
          "line-dasharray": [1.6, 1.2],
        },
      });
      map.addLayer({
        id: SEGMENT_CASING,
        type: "line",
        source: SEGMENT_SOURCE,
        filter: ["==", ["get", "routeIndex"], selectedRoute],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#020407", "line-width": 10, "line-opacity": 0.78 },
      });
      map.addLayer({
        id: SEGMENT_LINE,
        type: "line",
        source: SEGMENT_SOURCE,
        filter: ["==", ["get", "routeIndex"], selectedRoute],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": lineColorExpression() as never,
          "line-width": 5,
          "line-opacity": 1,
        },
      });
      map.addLayer({
        id: SELECTED_LINE,
        type: "line",
        source: SELECTED_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#ffffff",
          "line-width": 8,
          "line-opacity": 0.95,
        },
      });
      map.addLayer({
        id: HOTSPOT_LAYER,
        type: "circle",
        source: HOTSPOT_SOURCE,
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.2, 13, 5.5],
          "circle-color": "#ff5268",
          "circle-opacity": 0.18,
          "circle-stroke-color": "#ff5268",
          "circle-stroke-width": 1,
          "circle-stroke-opacity": 0.55,
        },
      });

      map.addLayer({
        id: STOP_LAYER,
        type: "circle",
        source: STOP_SOURCE,
        paint: {
          "circle-radius": ["case", ["boolean", ["get", "selected"], false], 9, 5.5],
          "circle-color": ["get", "color"] as never,
          "circle-opacity": 0.95,
          "circle-stroke-color": [
            "case",
            ["boolean", ["get", "selected"], false],
            "#f4f0e6",
            "#05070c",
          ] as never,
          "circle-stroke-width": ["case", ["boolean", ["get", "selected"], false], 2.4, 1.6],
        },
      });

      // While choosing place B, every click is "place B here", whatever it lands on.
      map.on("click", RISK_FILL, (event: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (pickingRef.current) return;
        const cell = event.features?.[0]?.properties?.cell;
        if (typeof cell === "string" && cell) callbacksRef.current.onSelectCell(cell);
      });
      map.on("click", STOP_LAYER, (event: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (pickingRef.current) return;
        const key = event.features?.[0]?.properties?.key;
        if (typeof key === "string") callbacksRef.current.onSelectStop(key);
      });
      map.on("click", ALT_LINE, (event: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (pickingRef.current) return;
        const routeIndex = Number(event.features?.[0]?.properties?.routeIndex);
        if (Number.isFinite(routeIndex)) callbacksRef.current.onSelectRoute(routeIndex);
      });
      map.on("click", SEGMENT_LINE, (event: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (pickingRef.current) return;
        const feature = event.features?.[0];
        const routeIndex = Number(feature?.properties?.routeIndex);
        const segmentIndex = Number(feature?.properties?.segmentIndex);
        if (Number.isFinite(routeIndex) && Number.isFinite(segmentIndex)) {
          callbacksRef.current.onSelectSegment(routeIndex, segmentIndex);
        }
      });
      map.on("click", HOTSPOT_LAYER, (event: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (pickingRef.current) return;
        const feature = event.features?.[0];
        const coordinates = (feature?.geometry as { coordinates?: number[] } | undefined)
          ?.coordinates;
        if (coordinates && coordinates.length >= 2) {
          callbacksRef.current.onMapClick({
            lat: coordinates[1]!,
            lon: coordinates[0]!,
          });
        }
      });
      map.on("click", (event: MapMouseEvent) => {
        if (pickingRef.current) {
          callbacksRef.current.onMapClick({
            lat: event.lngLat.lat,
            lon: event.lngLat.lng,
          });
          return;
        }
        const hits = map.queryRenderedFeatures(event.point, {
          layers: [RISK_FILL, SEGMENT_LINE, ALT_LINE, STOP_LAYER, HOTSPOT_LAYER],
        });
        if (hits.length === 0) {
          callbacksRef.current.onMapClick({
            lat: event.lngLat.lat,
            lon: event.lngLat.lng,
          });
          if (map.getZoom() < GLOBE_CLICK_ZOOM) {
            introRunningRef.current = false;
            pendingFitRef.current = null;
            playGlobeClickZoom(map, event.lngLat, introTimerRef, motionRef);
          }
        }
      });
      for (const layer of [RISK_FILL, SEGMENT_LINE, ALT_LINE, STOP_LAYER, HOTSPOT_LAYER]) {
        map.on("mouseenter", layer, () => {
          map.getCanvas().style.cursor = pickingRef.current ? "crosshair" : "pointer";
        });
        map.on("mouseleave", layer, () => {
          map.getCanvas().style.cursor = pickingRef.current ? "crosshair" : "";
        });
      }

      // Hover any hex in Explore to read its neighbourhood and score without
      // clicking: every cell of the surface is a place that can be inspected.
      const tip = new maplibregl.Popup({
        closeButton: false,
        closeOnClick: false,
        offset: 14,
        className: "hex-tip",
        maxWidth: "260px",
      });
      tipRef.current = tip;
      let tipCell: string | null = null;
      map.on("mousemove", RISK_FILL, (event: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (!exploreModeRef.current || pickingRef.current) {
          tip.remove();
          tipCell = null;
          return;
        }
        const props = event.features?.[0]?.properties;
        if (!props) return;
        tip.setLngLat(event.lngLat);
        if (props.cell !== tipCell) {
          tipCell = String(props.cell);
          const named = typeof props.name === "string" && props.name && props.name !== "null";
          const level = String(props.level) as RiskLevel;
          const score = Number(props.score);
          const body = document.createElement("div");
          body.className = "hex-tip-body";
          const title = document.createElement("strong");
          title.textContent = named
            ? String(props.name)
            : props.coverage === "context"
              ? "Global estimate (sampled)"
              : "Model cell";
          const line = document.createElement("span");
          line.textContent = Number.isFinite(score) ? `${level} · ${Math.round(score)} / 100` : level;
          line.style.color = RISK_COLORS[level] ?? "";
          const hint = document.createElement("em");
          hint.textContent = "Click to see why";
          body.append(title, line, hint);
          tip.setDOMContent(body);
        }
        if (!tip.isOpen()) tip.addTo(map);
      });
      map.on("mouseleave", RISK_FILL, () => {
        tipCell = null;
        tip.remove();
      });
      // Start with the attribution folded behind its (i) button; it opens on click.
      const attribution = map
        .getContainer()
        .querySelector<HTMLDetailsElement>("details.maplibregl-ctrl-attrib");
      if (attribution) attribution.open = false;

      // Light or dark, by the sun over the place in view. Zoomed out, the day
      // palette stays and the shadow layer shows night; zoomed in, the palette
      // itself follows the sun at the centre, with a little hysteresis around
      // the horizon so dusk does not flicker between themes.
      applyDataTheme(map, themeRef.current, !exploreModeRef.current);
      const stage = containerRef.current?.closest<HTMLElement>(".map-stage") ?? null;
      stage?.setAttribute("data-map-theme", themeRef.current);
      const syncTheme = () => {
        let next: MapTheme = "light";
        if (map.getZoom() >= THEME_SWITCH_ZOOM) {
          const centre = map.getCenter();
          const elevation = sunElevation(
            centre.lat,
            centre.lng,
            subsolarPoint(new Date(sunTimeRef.current)),
          );
          next = elevation < -2 ? "dark" : elevation > 2 ? "light" : themeRef.current;
        }
        if (next === themeRef.current) return;
        themeRef.current = next;
        applyBasemapTheme(map, next);
        applyDataTheme(map, next, !exploreModeRef.current);
        stage?.setAttribute("data-map-theme", next);
      };
      syncThemeRef.current = syncTheme;
      map.on("zoom", syncTheme);
      map.on("moveend", syncTheme);
      syncTheme();
      setReady(true);
    });

    return () => {
      replayButton.removeEventListener("click", handleReplay);
      if (introTimerRef.current !== null) {
        window.clearTimeout(introTimerRef.current);
        introTimerRef.current = null;
      }
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
      if (import.meta.env.DEV) {
        const debugWindow = window as Window & {
          __soberMap?: MapLibreMap;
          __soberGlobe?: () => void;
        };
        delete debugWindow.__soberMap;
        delete debugWindow.__soberGlobe;
      }
      map.remove();
      mapRef.current = null;
    };
    // The map instance is intentionally created once. Data and selection are
    // pushed into it by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(RISK_SOURCE) as GeoJSONSource | undefined;
    source?.setData(hexCollection(cells) as never);
  }, [cells, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(HOTSPOT_SOURCE) as GeoJSONSource | undefined;
    source?.setData(hotspotCollection(hotspots) as never);
  }, [hotspots, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(SEGMENT_SOURCE) as GeoJSONSource | undefined;
    source?.setData(segmentCollection(routes, selectedRoute) as never);
    // The selected route is drawn in full colour; every other route is a quiet
    // dashed line. Selection moves by swapping which layer a route belongs to.
    for (const id of [SEGMENT_CASING, SEGMENT_LINE]) {
      if (map.getLayer(id)) map.setFilter(id, ["==", ["get", "routeIndex"], selectedRoute]);
    }
    for (const id of [ALT_CASING, ALT_LINE]) {
      if (map.getLayer(id)) map.setFilter(id, ["!=", ["get", "routeIndex"], selectedRoute]);
    }
  }, [routes, selectedRoute, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(STOP_SOURCE) as GeoJSONSource | undefined;
    source?.setData(stopCollection(stops, selectedStopKey) as never);
  }, [stops, selectedStopKey, ready]);

  useEffect(() => {
    // The sun moved (the live clock ticked, or the dock hour changed): repaint
    // the night side and let the palette follow it.
    const map = mapRef.current;
    if (!map || !ready) return;
    const canvas = nightCanvasRef.current ?? document.createElement("canvas");
    nightCanvasRef.current = canvas;
    paintNightTexture(canvas, subsolarPoint(new Date(sunTime)));
    const source = map.getSource(NIGHT_SOURCE) as maplibregl.ImageSource | undefined;
    source?.updateImage({ url: canvas.toDataURL("image/png"), coordinates: WORLD_COORDINATES });
    syncThemeRef.current?.();
  }, [sunTime, ready]);

  useEffect(() => {
    // Idle spin. Zoomed out and left alone, the planet turns eastward, the way
    // it really does, while the sun and the night side stay put in space.
    // Any touch, drag or wheel stops it; it resumes after a few idle seconds.
    const map = mapRef.current;
    if (!map || !ready || prefersReducedMotion()) return;
    const surface = map.getCanvasContainer();
    let lastInput = performance.now();
    const onInput = () => {
      lastInput = performance.now();
    };
    surface.addEventListener("pointerdown", onInput);
    surface.addEventListener("wheel", onInput, { passive: true });
    surface.addEventListener("touchstart", onInput, { passive: true });
    let frame = 0;
    let last = performance.now();
    // The spin is decoration, and it keeps the globe rendering every frame. On a
    // device that cannot hold a steady frame rate (battery saver, a weak GPU) a
    // stuttering spin is worse than a still planet and wastes power, so it backs
    // off: two windows in a row whose median frame time is over the limit and it
    // stays off for the rest of the session. A median, because one slow tile
    // load should not count.
    const intervals: number[] = [];
    let windowStart = 0;
    let badWindows = 0;
    let givenUp = false;
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const gap = now - last;
      const dt = Math.min(64, gap);
      last = now;
      const idle =
        !givenUp &&
        !document.hidden &&
        now - lastInput >= SPIN_IDLE_MS &&
        map.getZoom() <= SPIN_MAX_ZOOM &&
        !map.isMoving() &&
        !introRunningRef.current &&
        introTimerRef.current === null &&
        !pickingRef.current;
      if (!idle) {
        intervals.length = 0;
        windowStart = 0;
        return;
      }
      if (windowStart === 0) windowStart = now;
      // A gap over a second is a hidden tab coming back, not a slow frame.
      if (gap < 1000) intervals.push(gap);
      // A window closes after enough frames, or after a few seconds on a device
      // so slow that the frames would take a long time to arrive.
      const full = intervals.length >= SPIN_WINDOW_FRAMES;
      const stale = now - windowStart >= SPIN_WINDOW_MS && intervals.length >= 6;
      if (full || stale) {
        intervals.sort((a, b) => a - b);
        const median = intervals[Math.floor(intervals.length / 2)]!;
        badWindows = median > SPIN_MAX_FRAME_MS ? badWindows + 1 : 0;
        if (badWindows >= 2) givenUp = true;
        intervals.length = 0;
        windowStart = 0;
      }
      const centre = map.getCenter();
      map.jumpTo({ center: [centre.lng - (SPIN_DEG_PER_S * dt) / 1000, centre.lat] });
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      surface.removeEventListener("pointerdown", onInput);
      surface.removeEventListener("wheel", onInput);
      surface.removeEventListener("touchstart", onInput);
    };
  }, [ready]);

  useEffect(() => {
    // Choosing a stop in the list brings it into view.
    const map = mapRef.current;
    if (!map || !ready || !selectedStopKey) return;
    const stop = stops.find((item) => stopKey(item) === selectedStopKey);
    if (!stop) return;
    map.easeTo({
      center: [stop.lon, stop.lat],
      zoom: Math.max(map.getZoom(), 14.2),
      duration: prefersReducedMotion() ? 0 : 850,
      essential: true,
    });
    // Only when the choice changes, not each time the list refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStopKey, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.getCanvas().style.cursor = picking ? "crosshair" : "";
  }, [picking, ready]);

  useEffect(() => {
    // Compare tool: put an A and a B on the map so the two scores have places.
    const map = mapRef.current;
    if (!map || !ready) return;
    compareMarkersRef.current.forEach((marker) => marker.remove());
    compareMarkersRef.current = [];
    if (!compareTarget) return;
    const place = (tag: string, at: Point, tone: string) => {
      const el = document.createElement("div");
      el.className = "compare-marker";
      el.dataset.tag = tag;
      el.style.setProperty("--tone", tone);
      el.textContent = tag;
      compareMarkersRef.current.push(
        new maplibregl.Marker({ element: el, anchor: "center" })
          .setLngLat([at.lon, at.lat])
          .addTo(map),
      );
    };
    place("A", point, "#7cf0c0");
    place("B", compareTarget, "#7fb6ff");
    return () => {
      compareMarkersRef.current.forEach((marker) => marker.remove());
      compareMarkersRef.current = [];
    };
  }, [compareTarget, point, ready]);

  useEffect(() => {
    // One chip per route, on the map, so five alternatives are not five
    // anonymous lines. Clicking a chip selects that route.
    const map = mapRef.current;
    if (!map || !ready) return;
    const clear = () => {
      chipMarkersRef.current.forEach((marker) => marker.remove());
      chipMarkersRef.current = [];
    };
    clear();
    if (routes.length < 2) return;

    // Routes share roads, so chips placed at the same fraction of each route
    // land on top of one another. Place the selected route first, then give each
    // other route the position (in screen space) that clears the chips already
    // down. Screen space depends on zoom, so lay out again when it changes.
    const CHIP_W = 132;
    const CHIP_H = 32;
    const fractions = [0.5, 0.38, 0.62, 0.28, 0.72, 0.18, 0.82, 0.45, 0.55];
    const layout = () => {
      clear();
      const placed: Array<{ x: number; y: number }> = [];
      const order = routes
        .map((_, index) => index)
        .sort((a, b) => (a === selectedRoute ? -1 : b === selectedRoute ? 1 : a - b));
      for (const index of order) {
        const route = routes[index]!;
        let best: { at: [number, number]; x: number; y: number; clearance: number } | null = null;
        for (const fraction of fractions) {
          const at =
            route.geometry[
              Math.min(route.geometry.length - 1, Math.floor(route.geometry.length * fraction))
            ];
          if (!at) continue;
          const p = map.project([at[1], at[0]]);
          const clearance = placed.reduce(
            (least, other) =>
              Math.min(
                least,
                Math.max(Math.abs(p.x - other.x) / CHIP_W, Math.abs(p.y - other.y) / CHIP_H),
              ),
            Number.POSITIVE_INFINITY,
          );
          if (!best || clearance > best.clearance) {
            best = { at: [at[0], at[1]], x: p.x, y: p.y, clearance };
          }
          if (clearance >= 1) break; // fits without touching another chip
        }
        if (!best) continue;
        placed.push({ x: best.x, y: best.y });

        const el = document.createElement("button");
        el.type = "button";
        el.className = "route-chip";
        el.dataset.selected = String(index === selectedRoute);
        el.style.setProperty("--tone", RISK_COLORS[route.risk_level]);
        el.setAttribute(
          "aria-label",
          `Show ${routeLabel(route)} route, ${formatDuration(route.duration_min)}`,
        );
        el.innerHTML = `<span class="route-chip-dot"></span><span class="route-chip-name"></span><span class="route-chip-time"></span>`;
        el.querySelector(".route-chip-name")!.textContent = routeLabel(route);
        el.querySelector(".route-chip-time")!.textContent = formatDuration(route.duration_min);
        el.addEventListener("click", (event) => {
          event.stopPropagation();
          callbacksRef.current.onSelectRoute(index);
        });
        chipMarkersRef.current.push(
          new maplibregl.Marker({ element: el, anchor: "center", offset: [0, -14] })
            .setLngLat([best.at[1], best.at[0]])
            .addTo(map),
        );
      }
    };
    layout();
    map.on("zoomend", layout);
    return () => {
      map.off("zoomend", layout);
      clear();
    };
  }, [routes, selectedRoute, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource(SELECTED_SOURCE) as GeoJSONSource | undefined;
    source?.setData(selectedSegmentCollection(routes, selectedSegment) as never);
  }, [routes, selectedSegment, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (map.getLayer(RISK_SELECTED)) {
      map.setFilter(RISK_SELECTED, ["==", ["get", "cell"], selectedCell ?? ""]);
    }
  }, [selectedCell, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = [];
    const route = routes[selectedRoute] ?? routes[0];
    if (!route) return;
    const addMarker = (lat: number, lon: number, label: string, tone: string) => {
      const el = document.createElement("div");
      el.style.cssText = `width:26px;height:26px;border-radius:99px;display:grid;place-items:center;font:600 10px/1 "IBM Plex Mono",monospace;color:#05070c;background:${tone};border:2px solid #05070c;box-shadow:0 0 0 1px ${tone};`;
      el.textContent = label;
      const marker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([lon, lat])
        .addTo(map);
      markersRef.current.push(marker);
    };
    const first = route.geometry[0];
    const last = route.geometry[route.geometry.length - 1];
    if (first) addMarker(first[0], first[1], "A", "#7cf0c0");
    if (last) addMarker(last[0], last[1], "B", "#f4f0e6");
    const visible = map.getZoom() >= 7.4;
    markersRef.current.forEach((marker) => {
      marker.getElement().style.transition = "opacity 180ms ease";
      marker.setOpacity(visible ? 1 : 0);
    });
  }, [routes, selectedRoute, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const bounds = fitRoutesKey ? routeBounds(routes, selectedRoute) : null;
    devLog("effect", {
      fitRoutesKey,
      routes: routes.length,
      bounds: Boolean(bounds),
      introPlayed: introPlayedRef.current,
      introRunning: introRunningRef.current,
    });

    if (!introPlayedRef.current) {
      if (bounds) {
        introPlayedRef.current = true;
        playGlobeIntro(
          map,
          bounds,
          dataRef.current.point,
          introTimerRef,
          introRunningRef,
          pendingFitRef,
          sunTimeRef.current,
          motionRef,
        );
        return;
      }
      // The route query may still be in flight. When the URL asks for a route,
      // wait longer so the globe dive lands on the actual route bounds.
      if (introTimerRef.current === null) {
        introTimerRef.current = window.setTimeout(
          () => {
            introTimerRef.current = null;
            if (introPlayedRef.current) return;
            introPlayedRef.current = true;
            const data = dataRef.current;
            const fallbackBounds = data.fitRoutesKey
              ? routeBounds(data.routes, data.selectedRoute)
              : null;
            playGlobeIntro(
              map,
              fallbackBounds,
              data.point,
              introTimerRef,
              introRunningRef,
              pendingFitRef,
              sunTimeRef.current,
              motionRef,
            );
          },
          expectRoute ? 3500 : 900,
        );
      }
      return;
    }

    if (!bounds) return;
    if (introRunningRef.current) {
      pendingFitRef.current = bounds;
      return;
    }
    // A route fit wins over an in-flight reveal or intro.
    motionRef.current += 1;
    if (introTimerRef.current !== null) {
      window.clearTimeout(introTimerRef.current);
      introTimerRef.current = null;
    }
    map.stop();
    fitRouteBounds(map, bounds, 850);
  }, [fitRoutesKey, ready, routes, selectedRoute, expectRoute]);

  useEffect(() => {
    // Choosing a place in the planner pulls the camera out to the globe,
    // rotates the target into view, then descends to the street. The intro
    // and any pending route fit yield to it.
    const map = mapRef.current;
    if (!map || !ready || !reveal) return;
    if (introTimerRef.current !== null) {
      window.clearTimeout(introTimerRef.current);
      introTimerRef.current = null;
    }
    introRunningRef.current = false;
    // A place chosen during the intro replaces it; the intro must not restart
    // later and fly away from the place.
    introPlayedRef.current = true;
    pendingFitRef.current = null;
    revealRef.current = reveal;
    playLocationReveal(map, reveal, introTimerRef, motionRef);
  }, [reveal, ready]);

  useEffect(() => {
    // Explore selections can come from the hotspot list, not the map itself.
    // Bring the chosen point into view when it is hidden behind a panel or off
    // screen. A click on the map is already in view, so the camera stays put
    // instead of lurching to re-centre on every click. Plan mode owns the
    // camera through the route fit and the reveal, so clearing the route key
    // must not snap back to the explore point.
    const map = mapRef.current;
    if (!map || !ready || !exploreMode || fitRoutesKey) return;
    if (!introPlayedRef.current || introRunningRef.current) return;
    // A reveal is already flying to this point (search or a shortcut).
    const pending = revealRef.current;
    if (pending && Math.abs(pending.lat - point.lat) < 1e-4 && Math.abs(pending.lon - point.lon) < 1e-4) {
      return;
    }
    // A globe click is already flying there; do not cut that animation short.
    if (map.isMoving()) return;
    const container = map.getContainer();
    const inset = routeInsets(map);
    const at = map.project([point.lon, point.lat]);
    const inView =
      at.x >= inset.left &&
      at.x <= container.clientWidth - inset.right &&
      at.y >= inset.top &&
      at.y <= container.clientHeight - inset.bottom;
    if (inView) return;
    map.easeTo({
      center: [point.lon, point.lat],
      duration: prefersReducedMotion() ? 0 : 700,
    });
  }, [point.lat, point.lon, ready, fitRoutesKey, exploreMode]);

  useEffect(() => {
    // The surface is the subject in Explore and the backdrop in Plan.
    const map = mapRef.current;
    if (!map || !ready) return;
    applyDataTheme(map, themeRef.current, !exploreMode);
    if (!exploreMode) tipRef.current?.remove();
  }, [exploreMode, ready]);

  useEffect(() => {
    // Explore: a pin on the point being scored, so the panel always has a
    // place on the map. The compare tool draws its own A and B instead.
    const map = mapRef.current;
    if (!map || !ready) return;
    if (!exploreMode || compareTarget) {
      pointMarkerRef.current?.remove();
      pointMarkerRef.current = null;
      return;
    }
    if (!pointMarkerRef.current) {
      const pin = document.createElement("div");
      pin.className = "explore-pin";
      pin.setAttribute("aria-hidden", "true");
      pointMarkerRef.current = new maplibregl.Marker({ element: pin, anchor: "center" });
    }
    pointMarkerRef.current.setLngLat([point.lon, point.lat]).addTo(map);
  }, [point.lat, point.lon, exploreMode, compareTarget, ready]);

  const selectedRouteIndex = selectedSegment?.routeIndex ?? null;
  const selectedSegmentIndex = selectedSegment?.segmentIndex ?? null;
  useEffect(() => {
    // "Select a row to find it on the map": when the chosen stretch is hidden
    // (behind the planner, under the present rail, or off screen), move the
    // camera just enough to show it. One already in view leaves it alone.
    const map = mapRef.current;
    if (!map || !ready || selectedRouteIndex === null || selectedSegmentIndex === null) return;
    if (!introPlayedRef.current || introRunningRef.current) return;
    const path = selectedSegmentPath(routes, {
      routeIndex: selectedRouteIndex,
      segmentIndex: selectedSegmentIndex,
    });
    if (!path || path.length < 2) return;
    const frame = requestAnimationFrame(() => {
      // A route fit in flight will show the whole route, this stretch included.
      if (map.isMoving()) return;
      const container = map.getContainer();
      const inset = routeInsets(map);
      const right = container.clientWidth - inset.right;
      const bottom = container.clientHeight - inset.bottom;
      const inView = path.every(([lon, lat]) => {
        const at = map.project([lon, lat]);
        return at.x >= inset.left && at.x <= right && at.y >= inset.top && at.y <= bottom;
      });
      if (inView) return;
      const bounds = new maplibregl.LngLatBounds();
      for (const position of path) bounds.extend(position);
      const camera = routeCamera(map, bounds, Math.max(map.getZoom(), 13));
      map.easeTo({
        center: camera.center,
        zoom: camera.zoom,
        duration: prefersReducedMotion() ? 0 : 750,
        essential: true,
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedRouteIndex, selectedSegmentIndex, routes, ready]);

  return (
    <div className="relative h-full w-full">
      <div className="starfield" aria-hidden="true" />
      <div
        ref={containerRef}
        className="h-full w-full"
        role="application"
        aria-label="New York City risk map. Risk hexes, crash hotspots and planned routes."
      />
      <SunLighting map={ready ? mapRef.current : null} sunTime={sunTime} />
    </div>
  );
}
