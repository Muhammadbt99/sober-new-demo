import { useEffect, useRef, useState } from "react";
import {
  ArrowLeftRight,
  ArrowRight,
  BookOpen,
  Info,
  MapPin,
  Play,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";

import { ErrorBlock, LoadingBlock, Notice } from "@/components/DataState";
import { RiskPill } from "@/components/RiskPill";
import { PlaceSearch } from "@/features/plan/PlaceSearch";
import { RouteCard } from "@/features/plan/RouteCard";
import type { Place } from "@/lib/api";
import {
  BUCKET_COLORS,
  BUCKET_LABELS,
  DAY_SHORT,
  formatCrashes,
  formatDistance,
  formatHour,
  formatPercent,
  formatRate,
} from "@/lib/risk";
import { fastestRoute } from "@/lib/routes";
import type { SafeRouteResponse, RouteSegment } from "@/lib/contract";
import { DEMO_TRIPS, type Panel } from "@/lib/use-url-state";

type SegmentSelection = { routeIndex: number; segmentIndex: number } | null;

type PlanPanelProps = {
  origin: Place | null;
  destination: Place | null;
  hour: number;
  day: number;
  run: boolean;
  route: SafeRouteResponse | undefined;
  routeLoading: boolean;
  routeError: unknown;
  onRetry: () => void;
  onRun: () => void;
  onOrigin: (place: Place | null) => void;
  onDestination: (place: Place | null) => void;
  onSwap: (origin: Place | null, destination: Place | null) => void;
  /** Load a demo trip: a given one, or the first (New York) when omitted. */
  onScenario: (tripId?: string) => void;
  /** The demo trip on screen, if any; the sparkle moves to the next one. */
  scenario: string | null;
  onPanel: (panel: Panel) => void;
  selectedRoute: number;
  selectedSegment: SegmentSelection;
  onSelectRoute: (index: number) => void;
  onSelectSegment: (selection: SegmentSelection) => void;
};

export function PlanPanel({
  origin,
  destination,
  hour,
  day,
  run,
  route,
  routeLoading,
  routeError,
  onRetry,
  onRun,
  onOrigin,
  onDestination,
  onSwap,
  onScenario,
  scenario,
  onPanel,
  selectedRoute,
  selectedSegment,
  onSelectRoute,
  onSelectSegment,
}: PlanPanelProps) {
  const [segmentOrder, setSegmentOrder] = useState<"worst" | "route">("worst");
  const [editingTrip, setEditingTrip] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);
  const canRun = Boolean(origin && destination);
  const showForm = !route || routeLoading || Boolean(routeError) || editingTrip;
  const selectedSegmentKey = selectedSegment
    ? `${selectedSegment.routeIndex}:${selectedSegment.segmentIndex}`
    : null;

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      bodyRef.current?.scrollTo({ top: 0 });
    });
    return () => cancelAnimationFrame(frame);
  }, [route, editingTrip]);

  useEffect(() => {
    if (!selectedSegmentKey) return;
    const frame = requestAnimationFrame(() => {
      // Scroll only the panel body; scrollIntoView would also scroll ancestors.
      const body = bodyRef.current;
      const detail = detailRef.current;
      if (!body || !detail) return;
      const top =
        detail.getBoundingClientRect().top -
        body.getBoundingClientRect().top +
        body.scrollTop;
      body.scrollTo({ top, behavior: "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedSegmentKey]);
  const selected = route?.routes[selectedRoute] ?? route?.routes[0];
  const fastest = fastestRoute(route?.routes ?? []);
  const maxExpected = Math.max(0, ...(route?.routes ?? []).map((item) => item.expected_crashes_total));

  return (
    <aside className="planner-panel panel" aria-label="Route planner">
      <div className="panel-head">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="eyebrow">Route planner / exposure</div>
            <h1 className="panel-title">Fastest is not the same as safest.</h1>
            {!route && (
              <p className="panel-subtitle">
                Compare routes on exposure, not on average score.
              </p>
            )}
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => {
              setEditingTrip(false);
              const at = DEMO_TRIPS.findIndex((trip) => trip.id === scenario);
              onScenario(DEMO_TRIPS[(at + 1) % DEMO_TRIPS.length]!.id);
            }}
            title={`Next demo trip: ${DEMO_TRIPS.map((trip) => trip.city).join(", ")}`}
            aria-label="Load the next demo trip"
          >
            <Sparkles size={14} />
          </button>
        </div>
      </div>

      <div className="panel-body panel-scroll" ref={bodyRef}>
        {showForm ? (
        <form
          className="planner-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (canRun) {
              setEditingTrip(false);
              onRun();
            }
          }}
        >
          <div className="place-fields">
            <PlaceSearch
              id="origin"
              label="Origin"
              placeholder="Times Square"
              value={origin}
              onSelect={onOrigin}
            />
            <button
              type="button"
              className="swap-button"
              aria-label="Swap origin and destination"
              onClick={() => onSwap(origin, destination)}
              disabled={!origin && !destination}
            >
              <ArrowLeftRight size={13} />
            </button>
            <PlaceSearch
              id="destination"
              label="Destination"
              placeholder="Barclays Center"
              value={destination}
              onSelect={onDestination}
            />
          </div>

          <div className="planner-form-foot">
            <span className="planner-time">
              {DAY_SHORT[day]} {formatHour(hour)} is set in the scrubber below
              <span className="mono">
                {hour.toString().padStart(2, "0")}:00 / {DAY_SHORT[day]}
              </span>
            </span>
            <button type="submit" className="solid-button" disabled={!canRun || routeLoading}>
              {routeLoading ? <LoadingBlock lines={1} className="button-loading" /> : <Play size={12} />}
              {run ? "Refresh comparison" : "Compare routes"}
              <ArrowRight size={12} />
            </button>
          </div>
        </form>
        ) : (
          <div className="trip-summary">
            <div className="trip-summary-route">
              <span className="eyebrow">Trip</span>
              <strong>
                {origin?.name ?? "Origin"} <span aria-hidden="true">→</span>{" "}
                {destination?.name ?? "Destination"}
              </strong>
              <span className="mono">
                {hour.toString().padStart(2, "0")}:00 / {DAY_SHORT[day]}
              </span>
            </div>
            <button
              type="button"
              className="ghost-button"
              onClick={() => setEditingTrip(true)}
            >
              Edit trip
            </button>
          </div>
        )}

        {!route && !routeLoading && !routeError && (
          <div className="plan-empty">
            <div className="plan-empty-mark">
              <MapPin size={17} />
            </div>
            <strong>Two addresses. One honest comparison.</strong>
            <p>
              Set an origin and destination, or load the Saturday 23:00 scenario.
              Every route is scored on the same model surface.
            </p>
            <button type="button" className="ghost-button" onClick={() => onScenario()}>
              <Sparkles size={12} />
              Load demo scenario
            </button>
            <div className="demo-cities" role="group" aria-label="Demo trips in other cities">
              {DEMO_TRIPS.map((trip) => (
                <button
                  key={trip.id}
                  type="button"
                  className="chip demo-city"
                  onClick={() => onScenario(trip.id)}
                >
                  {trip.city}
                </button>
              ))}
            </div>
          </div>
        )}

        {routeLoading && (
          <div className="space-y-3 pt-4">
            <LoadingBlock lines={2} />
            <div className="grid grid-cols-2 gap-3">
              <LoadingBlock lines={4} />
              <LoadingBlock lines={4} />
            </div>
            <LoadingBlock lines={5} />
          </div>
        )}

        {Boolean(routeError) && !routeLoading && (
          <ErrorBlock
            error={routeError}
            onRetry={onRetry}
            title="Route comparison unavailable"
            className="mt-4"
          />
        )}

        {run && !canRun && (
          <Notice tone="warn" className="mt-4">
            Add both an origin and a destination before comparing routes.
          </Notice>
        )}

        {route && !routeLoading && (
          <div className="space-y-3 pt-4">
            <div className="recommendation-card">
              <div className="recommendation-top">
                <span className="eyebrow">Recommendation</span>
                <span className="chip" title={`Routes came from ${route.routing_source}`}>
                  {route.total_routes} route{route.total_routes === 1 ? "" : "s"} scored
                </span>
              </div>
              <p>{route.recommendation.text}</p>
            </div>

            <div>
              <div className="section-heading">
                <span>Routes</span>
                <span className="mono">
                  {route.coverage === "context" ? "lowest estimated risk first" : "lowest expected crashes first"}
                </span>
              </div>
              <div className="route-list">
                {route.routes.map((item, index) => (
                  <RouteCard
                    key={`${item.rank}-${item.route_type}-${index}`}
                    route={item}
                    selected={index === selectedRoute}
                    fastest={fastest}
                    maxExpected={maxExpected}
                    onSelect={() => {
                      onSelectRoute(index);
                      onSelectSegment(null);
                    }}
                  />
                ))}
              </div>
            </div>

            {selected && (
              <div>
                <div className="section-heading">
                  <span>
                    {segmentOrder === "worst" ? "Riskiest stretches" : "Stretches in driving order"}
                    <span className="section-subtle"> · {selected.route_type}</span>
                  </span>
                  <div className="mini-toggle" role="group" aria-label="Segment order">
                    <button
                      type="button"
                      data-active={segmentOrder === "worst"}
                      onClick={() => setSegmentOrder("worst")}
                    >
                      Riskiest
                    </button>
                    <button
                      type="button"
                      data-active={segmentOrder === "route"}
                      onClick={() => setSegmentOrder("route")}
                    >
                      In order
                    </button>
                  </div>
                </div>
                <p className="section-note">
                  The route is cut wherever it enters a new risk area (cells of about
                  0.7 km²). Each row is one stretch: its street, where it is on the trip,
                  and how risky it is at this hour. Green is low, red is extreme, and the
                  line on the map uses the same colours. Select a row to find it on the map.
                </p>
                {selectedSegment && (
                  <div ref={detailRef}>
                    <SegmentDetail
                      segment={selected.segments[selectedSegment.segmentIndex]}
                      index={selectedSegment.segmentIndex}
                      onClose={() => onSelectSegment(null)}
                    />
                  </div>
                )}
                <SegmentList
                  route={selected}
                  estimated={selected.coverage === "context"}
                  order={segmentOrder}
                  selectedSegment={
                    selectedSegment?.routeIndex === selectedRoute
                      ? selectedSegment.segmentIndex
                      : null
                  }
                  onSelect={(segmentIndex) =>
                    onSelectSegment({ routeIndex: selectedRoute, segmentIndex })
                  }
                />
              </div>
            )}

            {route.warnings.map((warning) => (
              <Notice key={warning} tone="warn">
                {warning}
              </Notice>
            ))}

            <div className="planner-data-line">
              <span className="chip">
                <ShieldCheck size={9} />
                {route.coverage.replaceAll("_", " ")}
              </span>
              <span className="chip">{route.recommendation.basis.replaceAll("_", " ")}</span>
              {route.via_alternatives?.attempted && (
                <span
                  className="chip"
                  title={`${route.via_alternatives.candidate_count} waypoints tried, ${route.via_alternatives.added} kept. Dropped: ${route.via_alternatives.rejected.duplicate} duplicate, ${route.via_alternatives.rejected.backtrack} doubled back, ${route.via_alternatives.rejected.too_long} too long.`}
                >
                  <Info size={9} />
                  {route.via_alternatives.added} of {route.via_alternatives.candidate_count} waypoint
                  alternatives kept
                </span>
              )}
              {route.detour_synthesis.attempted && (
                <span className="chip">
                  <Info size={9} />
                  detour synthesis {route.detour_synthesis.improved ? "improved" : "no gain"}
                </span>
              )}
              {route.model_version && <span className="mono">model v{route.model_version}</span>}
            </div>
          </div>
        )}
      </div>

      <div className="panel-foot panel-foot-inline">
        <button
          type="button"
          className="ghost-button"
          onClick={() => {
            setEditingTrip(false);
            if (canRun) onRun();
            else onScenario();
          }}
        >
          <Play size={11} />
          {canRun ? "Run again" : "Load scenario"}
          {!canRun && <ArrowRight size={11} />}
        </button>
        <button type="button" className="text-link" onClick={() => onPanel("method")}>
          <BookOpen size={11} />
          Measured gate
        </button>
      </div>
    </aside>
  );
}

function SegmentList({
  route,
  estimated,
  order,
  selectedSegment,
  onSelect,
}: {
  route: SafeRouteResponse["routes"][number];
  estimated: boolean;
  order: "worst" | "route";
  selectedSegment: number | null;
  onSelect: (index: number) => void;
}) {
  const entries = route.segments.map((segment, index) => ({ segment, index }));
  const sorted =
    order === "worst"
      ? [...entries].sort(
          (a, b) =>
            b.segment.expected_crashes - a.segment.expected_crashes ||
            (b.segment.percentile ?? -1) - (a.segment.percentile ?? -1),
        )
      : entries;
  const visible = order === "worst" ? sorted.slice(0, 12) : sorted;

  return (
    <div className="segment-list">
      {visible.map(({ segment, index }) => (
        <button
          key={`${segment.cell}-${index}`}
          type="button"
          className="segment-row"
          data-active={selectedSegment === index}
          onClick={() => onSelect(index)}
        >
          <span
            className="segment-dot"
            style={{ background: BUCKET_COLORS[segment.bucket] }}
          />
          <span className="segment-main">
            <span title={segment.cell ?? undefined}>{segment.street || `Stretch ${index + 1}`}</span>
            <span className="segment-sub">
              {segment.from_km !== undefined && segment.to_km !== undefined
                ? `km ${segment.from_km.toFixed(1)}–${segment.to_km.toFixed(1)} · `
                : `${formatDistance(segment.length_m / 1000)} · `}
              {BUCKET_LABELS[segment.bucket]}
              {!estimated && ` · ${formatPercent(segment.percentile, 0)} percentile`}
            </span>
          </span>
          <span className="segment-value mono">
            {formatCrashes(segment.expected_crashes)}
            <span>{estimated ? "risk" : "exp."}</span>
          </span>
        </button>
      ))}
      {visible.length === 0 && (
        <p className="section-note">No route segments were scored.</p>
      )}
    </div>
  );
}

function SegmentDetail({
  segment,
  index,
  onClose,
}: {
  segment: RouteSegment | undefined;
  index: number;
  onClose: () => void;
}) {
  if (!segment) return null;
  // Outside NYC the stretch is scored by the global estimate, not the crash
  // model: its numbers are relative units, and the reasons that differ from one
  // stretch to the next (speed, junctions, turns) matter more than the country
  // line every stretch shares, which the estimate lists first.
  const estimated = segment.coverage === "context";
  const reasons = segment.reasons ?? [];
  const shown = estimated ? [...reasons.slice(1), ...reasons.slice(0, 1)].slice(0, 6) : reasons.slice(0, 3);
  return (
    <div className="segment-detail" role="region" aria-label={`Segment ${index + 1} detail`}>
      <div className="segment-detail-head">
        <div>
          <span className="eyebrow">
            {segment.street || `Stretch ${index + 1}`}
            {segment.from_km !== undefined && segment.to_km !== undefined
              ? ` · km ${segment.from_km.toFixed(1)}–${segment.to_km.toFixed(1)}`
              : ""}
          </span>
          <strong style={{ color: BUCKET_COLORS[segment.bucket] }}>
            {BUCKET_LABELS[segment.bucket]} exposure
          </strong>
        </div>
        <RiskPill level={segment.risk_level} />
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close segment detail">
          <X size={13} />
        </button>
      </div>

      {segment.action && <p className="segment-action">{segment.action}</p>}

      {estimated ? (
        <div className="segment-detail-grid">
          <div>
            <span className="eyebrow">Global scale</span>
            <strong className="mono">{Math.round(segment.percentile ?? 0)} / 100</strong>
            <span className="metric-note">50 = a typical road</span>
          </div>
          <div>
            <span className="eyebrow">This stretch</span>
            <strong className="mono">{formatCrashes(segment.expected_crashes)}</strong>
            <span className="metric-note">relative risk / pass</span>
          </div>
          <div>
            <span className="eyebrow">Length</span>
            <strong className="mono">{(segment.length_m / 1000).toFixed(2)} km</strong>
            <span className="metric-note">inside this map cell</span>
          </div>
        </div>
      ) : (
        <div className="segment-detail-grid">
          <div>
            <span className="eyebrow">Expected rate</span>
            <strong className="mono">{formatRate(segment.expected_rate)}</strong>
            <span className="metric-note">crashes / cell-hour</span>
          </div>
          <div>
            <span className="eyebrow">This segment</span>
            <strong className="mono">{formatCrashes(segment.expected_crashes)}</strong>
            <span className="metric-note">expected crashes / pass</span>
          </div>
          <div>
            <span className="eyebrow">Percentile</span>
            <strong className="mono">{formatPercent(segment.percentile, 1)}</strong>
            <span className="metric-note">against cell-hour reference</span>
          </div>
        </div>
      )}

      <div className="segment-detail-bars">
        <div>
          <span className="breakdown-label">
            <span>{estimated ? "Road, time & weather" : "Crash model"}</span>
            <span className="mono">{Math.round(segment.model_points ?? 0)} / 60</span>
          </span>
          <div className="risk-bar">
            <span
              style={{
                width: `${Math.min(100, ((segment.model_points ?? 0) / 60) * 100)}%`,
                background: BUCKET_COLORS[segment.bucket],
              }}
            />
          </div>
        </div>
        <div>
          <span className="breakdown-label">
            <span>Nightlife</span>
            <span className="mono">{Math.round(segment.nightlife_points ?? 0)} / 40</span>
          </span>
          <div className="risk-bar">
            <span
              style={{
                width: `${Math.min(100, ((segment.nightlife_points ?? 0) / 40) * 100)}%`,
                background: "#f3c15d",
              }}
            />
          </div>
        </div>
      </div>

      <ul className="reason-list segment-reasons">
        {shown.map((reason) => (
          <li key={reason}>
            <span className="reason-mark" />
            {reason}
          </li>
        ))}
        {(!segment.reasons || segment.reasons.length === 0) && (
          <li>
            <span className="reason-mark" />
            Expected rate is in the {formatPercent(segment.percentile, 1)} percentile
            for this hour.
          </li>
        )}
      </ul>

      <p className="segment-coord mono">
        H3 {segment.cell ?? "unavailable"} · {segment.midpoint[0].toFixed(5)},{" "}
        {segment.midpoint[1].toFixed(5)}
      </p>
    </div>
  );
}
