import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Info } from "lucide-react";

import { TopBar } from "@/components/TopBar";
import { TimeDock } from "@/components/TimeDock";
import { ExplorePanel } from "@/features/explore/ExplorePanel";
import { MethodScreen } from "@/features/method/MethodScreen";
import { PlanPanel } from "@/features/plan/PlanPanel";
import { PRESENT_STEPS, PresentBar } from "@/features/present/PresentBar";
import type { Place } from "@/lib/api";
import {
  useCompare,
  useHealth,
  useHeatmap,
  useHotspots,
  useLocationAlerts,
  useNearestSafe,
  useRisk,
  useSafeRoute,
  useSurface,
  useTrend,
} from "@/lib/queries";
import { inNycBox } from "@/lib/places";
import { RISK_COLORS } from "@/lib/risk";
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
  // The whole NYC model surface (1,120 cells) for the hour. Outside New York,
  // or against an API without /surface, the sampled grid draws the estimate.
  const surface = useSurface(time);
  const sampled = !inNyc || surface.isError;
  const heatmap = useHeatmap(point, time, sampled);
  const cells = useMemo<MapCell[]>(() => {
    const city = surface.data?.cells ?? [];
    const grid = sampled ? heatmap.data?.heatmap ?? [] : [];
    if (city.length === 0 && grid.length === 0) return NO_CELLS;
    if (grid.length === 0) return city;
    if (city.length === 0) return grid;
    return [...city, ...grid];
  }, [surface.data, heatmap.data, sampled]);
  // Crash clusters near the point being explored (none outside New York).
  const hotspots = useHotspots(point);
  const trend = useTrend(point, state.day);
  const health = useHealth();
  const exploring = state.panel === "explore";
  const nearest = useNearestSafe(point, exploring);
  const locationAlerts = useLocationAlerts(point, exploring);
  const compare = useCompare(point, state.compare ?? point, time, exploring && Boolean(state.compare));
  const route = useSafeRoute(
    state.origin,
    state.destination,
    time,
    state.run && state.panel === "plan",
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
          : "S.O.B.E.R. — safer routing for New York City";
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
    <div className="app-shell" data-present={state.present || undefined}>
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
          reveal={reveal}
          exploreMode={state.panel === "explore"}
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

      {state.panel === "explore" && (
        <ExplorePanel
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
        </>
      )}
    </div>
  );
}

export default App;
