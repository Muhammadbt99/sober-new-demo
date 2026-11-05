import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ChevronDown, ChevronUp, Info } from "lucide-react";

import { TopBar } from "@/components/TopBar";
import { TimeDock } from "@/components/TimeDock";
import { ExplorePanel } from "@/features/explore/ExplorePanel";
import { MethodScreen } from "@/features/method/MethodScreen";
import type { DriveTrip } from "@/features/drive/DriveMode";
import { PlanPanel } from "@/features/plan/PlanPanel";
import { PRESENT_STEPS, PresentBar } from "@/features/present/PresentBar";
import type { Place } from "@/lib/api";
import {
  useCompare,
  useGlobalSurface,
  useHealth,
  useHeatmap,
  useHotspots,
  useLocationAlerts,
  useNearestSafe,
  useRisk,
  useResponseGap,
  useSafeRoute,
  useSurface,
  useTrend,
} from "@/lib/queries";
import { inNycBox, inUaeBox } from "@/lib/places";
import { DANGER_META, GAP_COLORS, GAP_LEGEND, LAYER_STEPS } from "@/lib/danger";
import { RISK_COLORS, formatDateFor, formatHour } from "@/lib/risk";
import { EMPTY_CHECK_IN, activeGroups, type CheckIn } from "@/lib/checkin";
import { routeKey, trafficNote } from "@/lib/routes";
import { stopKey } from "@/lib/stops";
import { scenarioInstant } from "@/map/sun";
import { formatPointParam, useUrlState, type Panel } from "@/lib/use-url-state";
import {
  MapCanvas,
  type MapCell,
  type RevealTarget,
  type SegmentSelection,
} from "@/map/MapCanvas";
import type { Hotspot, Route as PlannedRoute, SafeStop } from "@/lib/contract";

/** Explore lands at neighbourhood zoom, where the hexes and streets both read. */
const EXPLORE_REVEAL_ZOOM = 13.4;

// Stable empties. A fresh `[]` on every render looks like new data to the map,
// which then reloads the layer each time the page re-renders.
const NO_CELLS: MapCell[] = [];
const NO_HOTSPOTS: Hotspot[] = [];
const NO_ROUTES: PlannedRoute[] = [];
const NO_STOPS: SafeStop[] = [];

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/explore" replace />} />
      <Route path="/explore" element={<AppFrame />} />
      <Route path="/plan" element={<AppFrame />} />
      <Route path="/method" element={<AppFrame />} />
      <Route path="*" element={<Navigate to="/explore" replace />} />
    </Routes>
  );
}

function AppFrame() {
  const {
    state,
    update,
    goTo,
    setPanel,
    setPoint,
    loadScenario,
    setOrigin,
    setDestination,
    swapEndpoints,
  } = useUrlState();
  const [presentStep, setPresentStep] = useState(0);
  // On a phone the panel is a sheet that can be put away to reach the map.
  const [sheetOpen, setSheetOpen] = useState(true);
  const worstSegmentAppliedRef = useRef(false);
  const [reveal, setReveal] = useState<RevealTarget | null>(null);
  const revealKeyRef = useRef(0);
  // The sun is real time by default: the night side and the day/night palettes
  // follow the actual clock, refreshed every 30 seconds. `sun=dock` makes them
  // follow the hour on the time dock instead, for showing a scenario.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  // Memoised so a re-render that changes neither input does not repaint the
  // night side, which costs a texture upload.
  const sunTime = useMemo(
    () => (state.sunMode === "dock" ? scenarioInstant(state.hour).getTime() : now),
    [state.sunMode, state.hour, now],
  );
  // Safe stops are drawn on the map only while their section is open.
  const [stopsVisible, setStopsVisible] = useState(true);
  const [selectedStopKey, setSelectedStopKey] = useState<string | null>(null);

  /**
   * Selecting an endpoint in the planner is a location decision, so give it a
   * camera move: out to the globe, rotate the target into view, descend.
   */
  function revealPlace(place: Place | null) {
    if (!place) return;
    revealKeyRef.current += 1;
    setReveal({ key: revealKeyRef.current, lat: place.lat, lon: place.lon });
  }

  /** Explore: go to a searched or shortcut place, by name, with a camera move. */
  function exploreTo(place: Place) {
    setSelectedStopKey(null);
    setPoint({ lat: place.lat, lon: place.lon }, null, place.name);
    revealKeyRef.current += 1;
    setReveal({
      key: revealKeyRef.current,
      lat: place.lat,
      lon: place.lon,
      zoom: EXPLORE_REVEAL_ZOOM,
    });
  }

  const point = useMemo(() => ({ lat: state.lat, lon: state.lon }), [state.lat, state.lon]);
  const time = useMemo(() => ({ hour: state.hour, day: state.day }), [state.hour, state.day]);
  const inNyc = inNycBox(point);

  const risk = useRisk(point, time);
  // The whole NYC model surface (1,122 cells) for the hour. In the UAE the
  // global crash model's cells around the point; anywhere else, or against an
  // API without these endpoints, the sampled grid draws the estimate.
  // In Explore the map can show one kind of crash (layer=impaired, ...).
  const dangerLayer = state.panel === "explore" ? state.dangerLayer : null;
  const surface = useSurface(time, dangerLayer);
  const inUae = inUaeBox(point);
  const regionSurface = useGlobalSurface(point, time, inUae);
  const regional = inUae && !regionSurface.isError;
  const sampled = (!inNyc && !regional) || surface.isError;
  const heatmap = useHeatmap(point, time, sampled);
  const gapData = useResponseGap(point, time, state.panel === "explore");
  const gapOn = state.panel === "explore" && state.responseGap;
  const baseCells = useMemo<MapCell[]>(() => {
    const city = surface.data?.cells ?? [];
    const region = regional
      ? (regionSurface.data?.cells ?? []).map((cell) => ({ ...cell, source: "global_model" }))
      : [];
    const grid = sampled ? heatmap.data?.heatmap ?? [] : [];
    const all = [...city, ...region, ...grid];
    return all.length === 0 ? NO_CELLS : all;
  }, [surface.data, regionSurface.data, heatmap.data, sampled, regional]);
  const cells = useMemo<MapCell[]>(() => {
    if (!gapOn) return baseCells;
    const byCell = new Map((gapData.data?.cells ?? []).map((item) => [item.cell, item]));
    return baseCells.map((cell) => {
      const found = cell.cell ? byCell.get(cell.cell) : undefined;
      return { ...cell, gap_quadrant: found?.quadrant ?? null, gap_km: found?.km ?? null, gap_hospital: found?.hospital ?? null };
    });
  }, [baseCells, gapData.data, gapOn]);
  // Crash clusters near the point being explored (none outside New York).
  const hotspots = useHotspots(point);
  const trend = useTrend(point, state.day);
  const health = useHealth();
  const exploring = state.panel === "explore";
  const nearest = useNearestSafe(point, exploring);
  const locationAlerts = useLocationAlerts(point, exploring);
  const compare = useCompare(point, state.compare ?? point, time, exploring && Boolean(state.compare));
  // Held in memory on purpose: the check-in is never stored or sent anywhere.
  const [checkIn, setCheckIn] = useState<CheckIn>(EMPTY_CHECK_IN);
  const [drive, setDrive] = useState<DriveTrip | null>(null);
  const endDrive = useCallback(() => setDrive(null), []);
  const route = useSafeRoute(
    state.origin,
    state.destination,
    time,
    state.run && state.panel === "plan",
    drive !== null,
  );

  const routeCount = route.data?.routes.length ?? 1;
  const selectedRoute = Math.min(
    Math.max(state.routeIndex ?? route.data?.recommended_route ?? 0, 0),
    Math.max(0, routeCount - 1),
  );
  const selectedSegment: SegmentSelection =
    state.segmentIndex !== null && route.data
      ? { routeIndex: selectedRoute, segmentIndex: state.segmentIndex }
      : null;

  // A live refresh can reorder the list. The road you picked stays picked.
  const shownRoutes = useRef<{ data: typeof route.data; index: number | null }>({
    data: undefined,
    index: null,
  });
  useEffect(() => {
    const previous = shownRoutes.current;
    shownRoutes.current = { data: route.data, index: state.routeIndex };
    const picked = previous.index === null ? undefined : previous.data?.routes[previous.index];
    if (!picked || !route.data || previous.data === route.data || previous.index !== state.routeIndex) return;
    const moved = route.data.routes.findIndex((item) => routeKey(item) === routeKey(picked));
    if (moved >= 0 && moved !== state.routeIndex) update({ route: moved, seg: null });
  }, [route.data, state.routeIndex, update]);

  const fitRoutesKey =
    state.panel === "plan" && route.data
      ? `${route.data.route_id}:${selectedRoute}:${state.hour}:${state.day}`
      : null;

  useEffect(() => {
    document.title =
      state.panel === "method"
        ? "Method — S.O.B.E.R."
        : state.panel === "plan"
          ? "Plan a safer route — S.O.B.E.R."
          : "S.O.B.E.R. — crash risk and safer routes";
  }, [state.panel]);

  useEffect(() => {
    if (!state.present) return;
    applyPresentStep(presentStep);
    // The presenter advances one step at a time; applying a step must not rerun
    // when a background query resolves with new route data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.present, presentStep]);

  useEffect(() => {
    // Step 3 is applied as soon as the presenter advances, which can be before
    // the route query resolves. Select the worst segment once the data lands.
    if (!state.present || presentStep !== 3) {
      worstSegmentAppliedRef.current = false;
      return;
    }
    if (!route.data || worstSegmentAppliedRef.current) return;
    const segments = route.data.routes[0]?.segments ?? [];
    let worst = 0;
    let max = -1;
    segments.forEach((segment, index) => {
      if (segment.expected_crashes > max) {
        max = segment.expected_crashes;
        worst = index;
      }
    });
    worstSegmentAppliedRef.current = true;
    if (state.segmentIndex !== worst) update({ seg: worst });
  }, [state.present, presentStep, route.data, state.segmentIndex, update]);

  function applyPresentStep(step: number) {
    if (step === 0) {
      goTo("explore", {
        lat: 40.758,
        lon: -73.9855,
        pname: "Times Square",
        hour: 23,
        day: 5,
        cell: null,
        run: null,
        route: null,
        seg: null,
      });
      return;
    }
    if (step === 1) {
      loadScenario(true);
      return;
    }
    if (step === 2) {
      if (!state.origin || !state.destination) loadScenario(true);
      else goTo("plan", { run: "1", route: null, seg: null });
      return;
    }
    if (step === 3) {
      if (!state.origin || !state.destination) loadScenario(true);
      const segments = route.data?.routes[0]?.segments ?? [];
      let worst = 0;
      let max = -1;
      segments.forEach((segment, index) => {
        if (segment.expected_crashes > max) {
          max = segment.expected_crashes;
          worst = index;
        }
      });
      goTo("plan", { run: "1", route: 0, seg: worst });
      return;
    }
    if (step === 4) {
      if (!state.origin || !state.destination) loadScenario(true);
      goTo("plan", { hour: 3, day: 5, route: 0, seg: null, run: "1" });
      return;
    }
    if (step === 5) {
      goTo("method");
      return;
    }
    if (step === 6) {
      goTo("explore", {
        lat: 40.7357,
        lon: -74.1724,
        pname: "Newark, NJ",
        hour: 23,
        day: 5,
        cell: null,
        run: null,
        route: null,
        seg: null,
      });
      return;
    }
    if (step === 8) {
      loadScenario(true);
      return;
    }
    if (step === 7) {
      const dubai = { lat: 25.1972, lon: 55.2744 };
      goTo("explore", {
        lat: dubai.lat,
        lon: dubai.lon,
        pname: "Downtown Dubai",
        hour: 23,
        day: 5,
        cell: null,
        run: null,
        route: null,
        seg: null,
      });
      // Half a world away: fly there rather than wait for the map to follow.
      revealKeyRef.current += 1;
      setReveal({ key: revealKeyRef.current, ...dubai, zoom: EXPLORE_REVEAL_ZOOM });
    }
  }

  function handlePanel(panel: Panel) {
    if (state.present) {
      update({ present: null });
    }
    setPanel(panel);
  }

  function handleScenario(tripId?: string) {
    setPanel("plan");
    loadScenario(true, tripId);
  }

  return (
    <div
      className="app-shell"
      data-present={state.present || undefined}
      data-sheet={sheetOpen ? undefined : "closed"}
      data-driving={drive ? "" : undefined}
    >
      <div className="map-stage">
        <MapCanvas
          point={point}
          cells={cells}
          hotspots={hotspots.data?.hotspots ?? NO_HOTSPOTS}
          routes={state.panel === "plan" ? route.data?.routes ?? NO_ROUTES : NO_ROUTES}
          selectedRoute={selectedRoute}
          selectedSegment={state.panel === "plan" ? selectedSegment : null}
          selectedCell={state.cell}
          onSelectCell={(cell) => {
            const selected = cells.find((item) => item.cell === cell);
            if (selected) {
              setSelectedStopKey(null);
              setPoint({ lat: selected.lat, lon: selected.lon }, cell, null);
            }
          }}
          onSelectSegment={(routeIndex, segmentIndex) =>
            update({ route: routeIndex, seg: segmentIndex })
          }
          onMapClick={(clicked) => {
            if (state.picking) {
              // Place B of the compare tool; the selected point stays where it is.
              update({ cmp: formatPointParam(clicked), pick: null });
              return;
            }
            setSelectedStopKey(null);
            setPoint(clicked, null);
          }}
          onSelectRoute={(index) => update({ route: index, seg: null })}
          stops={exploring && stopsVisible ? nearest.data?.stops ?? NO_STOPS : NO_STOPS}
          selectedStopKey={selectedStopKey}
          onSelectStop={setSelectedStopKey}
          compareTarget={exploring ? state.compare : null}
          picking={exploring && state.picking}
          fitRoutesKey={fitRoutesKey}
          expectRoute={state.panel === "plan" && Boolean(state.run)}
          sunTime={sunTime}
          drive={drive}
          onDriveEnd={endDrive}
          reveal={reveal}
          exploreMode={state.panel === "explore"}
          dangerLayer={gapOn ? null : dangerLayer}
          gapLayer={gapOn}
        />
      </div>

      <TopBar
        panel={state.panel}
        onPanel={handlePanel}
        present={state.present}
        onPresent={() => {
          if (!state.present) setPresentStep(0);
          update({ present: state.present ? null : "1" });
        }}
        health={health}
      />

      {state.present && (
        <PresentBar
          step={presentStep}
          onStep={(step) => setPresentStep(Math.max(0, Math.min(PRESENT_STEPS.length - 1, step)))}
          onExit={() => update({ present: null })}
        />
      )}

      {state.panel !== "method" && (
        <button
          type="button"
          className="sheet-toggle"
          aria-expanded={sheetOpen}
          aria-label={sheetOpen ? "Hide panel" : "Show panel"}
          onClick={() => setSheetOpen((open) => !open)}
        >
          {sheetOpen ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
        </button>
      )}

      {state.panel === "explore" && (
        <ExplorePanel
          flaggedDangers={activeGroups(checkIn)}
          point={point}
          hour={state.hour}
          day={state.day}
          selectedCell={state.cell}
          risk={risk.data}
          riskLoading={risk.isLoading}
          riskError={risk.error}
          onRetryRisk={() => void risk.refetch()}
          heatmap={sampled ? heatmap.data : undefined}
          surface={surface.data}
          regionSurface={regional ? regionSurface.data : undefined}
          dangerLayer={state.dangerLayer}
          onDangerLayer={(layer) => update({ layer, gap: null })}
          responseGap={gapData.data}
          responseGapLoading={gapData.isLoading}
          responseGapOn={gapOn}
          onResponseGap={(on) => update({ gap: on ? "1" : null, layer: null })}
          placeName={state.placeName}
          onExplorePlace={exploreTo}
          onPlanFromHere={(name) => {
            if (state.present) update({ present: null });
            goTo(
              "plan",
              {
                olat: point.lat.toFixed(5),
                olon: point.lon.toFixed(5),
                oname: name,
                odetail: "",
                run: null,
                route: null,
                seg: null,
              },
              { replace: false },
            );
          }}
          trend={trend.data}
          hotspots={hotspots.data}
          health={health.data}
          onHour={(hour) => update({ hour, seg: null })}
          onSelectHotspot={(spot) => {
            setSelectedStopKey(null);
            setPoint(spot, null);
          }}
          onPanel={handlePanel}
          nearest={nearest}
          locationAlerts={locationAlerts.data}
          stopKey={selectedStopKey}
          onSelectStop={(stop) => setSelectedStopKey(stop ? stopKey(stop) : null)}
          onStopsOpenChange={setStopsVisible}
          compareTarget={state.compare}
          picking={state.picking}
          compare={compare}
          onPickCompare={() => update({ pick: 1 })}
          onCancelPick={() => update({ pick: null })}
          onClearCompare={() => update({ cmp: null, pick: null })}
          onSwapCompare={() => {
            if (!state.compare) return;
            // A becomes B and B becomes the selected point.
            update({
              lat: state.compare.lat.toFixed(5),
              lon: state.compare.lon.toFixed(5),
              cell: null,
              cmp: formatPointParam(point),
            });
          }}
        />
      )}

      {state.panel === "plan" && (
        <PlanPanel
          origin={state.origin}
          destination={state.destination}
          hour={state.hour}
          day={state.day}
          run={state.run}
          route={route.data}
          routeLoading={route.isLoading}
          checkIn={checkIn}
          onCheckIn={setCheckIn}
          onStartDrive={(item, index) => {
            if (state.origin && state.destination) {
              setDrive({
                route: item,
                routeIndex: index,
                origin: { lat: state.origin.lat, lon: state.origin.lon },
                destination: { lat: state.destination.lat, lon: state.destination.lon },
                time,
                driverGroups: activeGroups(checkIn),
              });
            }
          }}
          trafficNote={trafficNote(
            route.data?.traffic,
            route.data?.routes ?? NO_ROUTES,
            formatDateFor(state.day, state.hour),
            formatHour(state.hour),
          )}
          routeError={route.error}
          onRetry={() => void route.refetch()}
          onRun={() => update({ run: "1", route: null, seg: null })}
          onOrigin={(place) => {
            setOrigin(place);
            revealPlace(place);
          }}
          onDestination={(place) => {
            setDestination(place);
            revealPlace(place);
          }}
          onSwap={(from, to) => {
            swapEndpoints(from, to);
            revealPlace(to);
          }}
          scenario={state.scenario}
          onScenario={handleScenario}
          onDeparture={(hour, day) => update({ hour, day, seg: null })}
          onPanel={handlePanel}
          selectedRoute={selectedRoute}
          selectedSegment={selectedSegment}
          onSelectRoute={(index) => update({ route: index, seg: null })}
          onSelectSegment={(selection) =>
            // Clearing a segment must leave the chosen route alone. It used to
            // clear both, which is why picking a route card appeared to do nothing.
            update(
              selection
                ? { route: selection.routeIndex, seg: selection.segmentIndex }
                : { seg: null },
            )
          }
        />
      )}

      {state.panel === "method" && (
        <MethodScreen health={health.data} onPanel={handlePanel} />
      )}

      {state.panel !== "method" && (
        <>
          <TimeDock
            hour={state.hour}
            day={state.day}
            onChange={(patch) => update({ ...patch, seg: null })}
            sunMode={state.sunMode}
            onSunMode={(mode) => update({ sun: mode === "dock" ? "dock" : null })}
          />

          {gapOn ? (
            <div className="map-legend" aria-label="Response gap legend">
              <span className="legend-title">Risk and distance to a hospital</span>
              {GAP_LEGEND.map((row) => (
                <span key={row.id} className="legend-row">
                  <span className="legend-swatch" style={{ background: GAP_COLORS[row.id] }} />
                  {row.label}
                </span>
              ))}
              <span className="legend-row legend-note">
                <Info size={9} />
                straight line, not a drive
              </span>
            </div>
          ) : dangerLayer ? (
            <div className="map-legend" aria-label={`${DANGER_META[dangerLayer]?.label ?? "Danger"} legend`}>
              <span className="legend-row legend-title">
                {DANGER_META[dangerLayer]?.short ?? dangerLayer} vs a typical NYC hour
              </span>
              {LAYER_STEPS.map((step) => (
                <span key={step.label} className="legend-row">
                  <span className="legend-swatch" style={{ background: step.color }} />
                  {step.label}
                </span>
              ))}
              <span className="legend-row">
                <span className="legend-swatch" style={{ background: "#4d5a6b" }} />
                NOT ENOUGH DATA
              </span>
              <span className="legend-row legend-note">
                <Info size={9} />
                expected rate of this kind of crash
              </span>
            </div>
          ) : (
            <div className="map-legend" aria-label="Risk legend">
              {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map((level) => (
                <span key={level} className="legend-row">
                  <span
                    className="legend-swatch"
                    style={{ background: RISK_COLORS[level] }}
                  />
                  {level}
                </span>
              ))}
              <span className="legend-row">
                <span
                  className="legend-swatch"
                  style={{ background: "#4d5a6b" }}
                />
                NO MODEL
              </span>
              <span className="legend-row legend-note">
                <Info size={9} />
                hex = modeled cell-hour
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default App;
