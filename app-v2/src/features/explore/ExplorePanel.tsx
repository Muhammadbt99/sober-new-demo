import { useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  FlaskConical,
  GitCompareArrows,
  LifeBuoy,
  ShieldCheck,
} from "lucide-react";

import { Disclosure } from "@/components/Disclosure";
import { ErrorBlock, LoadingBlock, Notice } from "@/components/DataState";
import { TrendStrip } from "@/components/TrendStrip";
import { ComparePanel } from "@/features/explore/ComparePanel";
import { DangerProfileCard } from "@/features/explore/DangerProfileCard";
import { ResponseGapCard } from "@/features/explore/ResponseGapCard";
import type { ResponseGap } from "@/lib/contract";
import { NearbyStops } from "@/features/explore/NearbyStops";
import { SeverityLab } from "@/features/explore/SeverityLab";
import { PlaceSearch } from "@/features/plan/PlaceSearch";
import type {
  HealthResponse,
  HeatmapResponse,
  HotspotsResponse,
  LocationAlertsResponse,
  NearestSafeResponse,
  RiskCompareResponse,
  RiskResponse,
  RiskTrendResponse,
  SafeStop,
  SurfaceResponse,
  GlobalSurfaceResponse,
} from "@/lib/contract";
import type { Place, Point } from "@/lib/api";
import type { DangerLayer } from "@/lib/danger";
import { SHORTCUT_GROUPS, inNycBox, samePoint } from "@/lib/places";
import {
  formatDateFor,
  RISK_COLORS,
  formatHour,
  formatPercent,
  weatherLabel,
} from "@/lib/risk";
import type { Panel } from "@/lib/use-url-state";

function formatMetres(metres: number): string {
  return metres < 1000 ? `${Math.round(metres / 10) * 10} m` : `${(metres / 1000).toFixed(1)} km`;
}

/**
 * "1 injury crash every 38 h": the cell-hour rate in a form people can picture
 * (how often one would happen here if every hour were like this one).
 */
function onceEvery(rate: number | null | undefined): string | null {
  if (rate === null || rate === undefined || !(rate > 0)) return null;
  const hours = 1 / rate;
  if (hours < 1.5) return "1 injury crash an hour";
  if (hours < 72) return `1 injury crash every ${Math.round(hours)} h`;
  const days = hours / 24;
  if (days < 60) return `1 injury crash every ${Math.round(days)} days`;
  return `1 injury crash every ${Math.round(days / 30)} months`;
}

/**
 * Warnings the panel already shows elsewhere: coverage (the notice at the
 * top), nightlife (the bar's caption) and weather (the data line).
 */
const SHOWN_ELSEWHERE = [
  /^Outside the NYC crash model/,
  /^No learned crash surface/,
  /^Nightlife data unavailable/,
  /^Live weather unavailable/,
  /^Weather refresh failed/,
];

function extraWarnings(warnings: string[]): string[] {
  return warnings.filter((warning) => !SHOWN_ELSEWHERE.some((pattern) => pattern.test(warning)));
}

type ExplorePanelProps = {
  point: Point;
  hour: number;
  day: number;
  selectedCell: string | null;
  risk: RiskResponse | undefined;
  riskLoading: boolean;
  riskError: unknown;
  onRetryRisk: () => void;
  heatmap: HeatmapResponse | undefined;
  /** The whole-city model surface, when the map is drawing it. */
  surface: SurfaceResponse | undefined;
  /** The global crash model's cells around the point (the UAE), when drawn. */
  regionSurface?: GlobalSurfaceResponse;
  /** Map layer: one kind of crash, or null for overall risk. */
  dangerLayer: DangerLayer | null;
  onDangerLayer: (layer: DangerLayer | null) => void;
  /** Name of the point when it came from search or a shortcut. */
  placeName: string | null;
  /** Go to a searched or shortcut place. */
  onExplorePlace: (place: Place) => void;
  /** Kinds of crash the driver's check-in points to. */
  flaggedDangers: string[];
  responseGap: ResponseGap | undefined;
  responseGapLoading: boolean;
  responseGapOn: boolean;
  onResponseGap: (on: boolean) => void;
  /** Open the planner with this place as the origin. */
  onPlanFromHere: (name: string) => void;
  trend: RiskTrendResponse | undefined;
  hotspots: HotspotsResponse | undefined;
  health: HealthResponse | undefined;
  onHour: (hour: number) => void;
  onSelectHotspot: (point: Point) => void;
  onPanel: (panel: Panel) => void;
  /** Safe stops around the point (loaded by the page so the map can draw them too). */
  nearest: { data?: NearestSafeResponse; isLoading: boolean; isError: boolean };
  locationAlerts: LocationAlertsResponse | undefined;
  stopKey: string | null;
  onSelectStop: (stop: SafeStop | null) => void;
  onStopsOpenChange: (open: boolean) => void;
  /** Compare tool: place B, whether the next map click chooses it, and the result. */
  compareTarget: Point | null;
  picking: boolean;
  compare: {
    data?: RiskCompareResponse;
    isLoading: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  onPickCompare: () => void;
  onCancelPick: () => void;
  onClearCompare: () => void;
  onSwapCompare: () => void;
};

export function ExplorePanel({
  flaggedDangers,
  responseGap,
  responseGapLoading,
  responseGapOn,
  onResponseGap,
  point,
  hour,
  day,
  selectedCell,
  risk,
  riskLoading,
  riskError,
  onRetryRisk,
  heatmap,
  surface,
  regionSurface,
  dangerLayer,
  onDangerLayer,
  placeName,
  onExplorePlace,
  onPlanFromHere,
  trend,
  hotspots,
  health,
  onHour,
  onSelectHotspot,
  onPanel,
  nearest,
  locationAlerts,
  stopKey,
  onSelectStop,
  onStopsOpenChange,
  compareTarget,
  picking,
  compare,
  onPickCompare,
  onCancelPick,
  onClearCompare,
  onSwapCompare,
}: ExplorePanelProps) {
  const alerts = { data: locationAlerts };
  const [showAllHotspots, setShowAllHotspots] = useState(false);
  const inNyc = inNycBox(point);
  const apiPlace = risk?.place ?? null;
  const coords = `${point.lat.toFixed(4)}, ${point.lon.toFixed(4)}`;
  // A searched name wins; otherwise the API's neighbourhood or road; else coordinates.
  const title = placeName ?? apiPlace?.name ?? (risk || !riskLoading ? coords : "Scoring…");
  const area = placeName
    ? [apiPlace?.name !== placeName ? apiPlace?.name : null, apiPlace?.area].filter(Boolean).join(" · ")
    : apiPlace?.area ?? "";
  const searchValue: Place | null = placeName
    ? { id: `named-${point.lat.toFixed(5)},${point.lon.toFixed(5)}`, name: placeName, detail: "", lat: point.lat, lon: point.lon }
    : null;
  const bars = risk?.features?.bars;
  const dangerCheck = (health?.checks as { danger_types?: { validated?: string[] } } | undefined)
    ?.danger_types;
  const validatedDanger = dangerCheck?.validated ?? null;
  const model = health?.checks.crash_risk_model;
  // Outside the trained area with nothing else to go on there is no signal at
  // all, and a green "LOW 0" would read as a confident all-clear.
  // Outside NYC the API answers with a labelled global estimate, not the model.
  const estimated = risk?.coverage === "context";
  // The global crash model (the UAE): learned from six cities' crash records,
  // as against the hand-set estimate used everywhere else outside New York.
  const learned = risk?.context?.method === "global_model";
  const noSignal =
    risk?.coverage === "rule_based_only" &&
    risk.model_points === 0 &&
    risk.nightlife_points === 0;

  return (
    <aside className="explore-panel panel" aria-label="Selected location risk">
      <div className="panel-head">
        <div className="explore-search">
          <PlaceSearch
            id="explore-place"
            label="Score any place"
            placeholder="Search a street, place or city"
            value={searchValue}
            onSelect={(place) => {
              if (place) onExplorePlace(place);
            }}
          />
          {SHORTCUT_GROUPS.map((group) => (
            <div key={group.label} className="explore-shortcut-group">
              <span className="explore-shortcut-label" title={group.title}>
                {group.label}
              </span>
              <div className="explore-shortcuts" role="group" aria-label={`Jump to a place: ${group.title}`}>
                {group.places.map((place) => (
                  <button
                    key={place.id}
                    type="button"
                    className="chip demo-city"
                    data-active={samePoint(place, point) || undefined}
                    title={`${place.name}, ${place.detail}`}
                    onClick={() => onExplorePlace(place)}
                  >
                    {group.short(place)}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <p className="explore-hint">Or click anywhere on the map. Hover a hex to read it.</p>
        </div>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="eyebrow">
              {risk?.coverage === "context"
                ? learned
                  ? "Global crash model"
                  : "Global estimate"
                : risk?.coverage === "model"
                  ? "NYC crash model"
                  : risk
                    ? "Outside model coverage"
                    : "Selected location"}
            </div>
            <h1 className="panel-title">{title}</h1>
            <p className="panel-subtitle">
              {area && (
                <>
                  <span>{area}</span>
                  <span className="dot-separator">·</span>
                </>
              )}
              <span className="mono" title={selectedCell ?? risk?.cell ?? undefined}>{coords}</span>
              <span className="dot-separator">·</span>
              <span className="mono">
                {formatHour(hour)} / {formatDateFor(day, hour)}
              </span>
            </p>
          </div>
        </div>

        {risk && noSignal && (
          <div className="score-line">
            <div className="risk-number" style={{ color: "var(--color-ink-dim, #8a97a8)" }}>
              —
            </div>
            <div className="score-meta">
              <span className="eyebrow">Score / 100</span>
              <span className="score-band" style={{ color: "var(--color-ink-dim, #8a97a8)" }}>
                No crash data here
              </span>
            </div>
          </div>
        )}

        {risk && !noSignal && (
          <div className="score-line">
            <div className="risk-number" style={{ color: RISK_COLORS[risk.risk_level] }}>
              {Math.round(risk.risk_score)}
            </div>
            <div className="score-meta">
              <span className="eyebrow">Score / 100</span>
              <span className="score-band" style={{ color: RISK_COLORS[risk.risk_level] }}>
                {risk.risk_level}
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="panel-body panel-scroll">
        {riskLoading && !risk && (
          <div className="space-y-4">
            <LoadingBlock lines={2} />
            <LoadingBlock lines={4} />
            <LoadingBlock lines={3} />
          </div>
        )}

        {Boolean(riskError) && !risk && (
          <ErrorBlock
            error={riskError}
            onRetry={onRetryRisk}
            title="Risk score unavailable"
          />
        )}

        {risk && (
          <div className="space-y-5">
            {estimated && !learned && (
              <Notice tone="info">
                A global estimate, not a trained crash model: it ranks places and hours, it does
                not count crashes.
              </Notice>
            )}
            {learned && (
              <Notice tone="info">
                Learned from {risk.context?.trained_on ?? "six cities' crash records"}. It ranks
                places and hours here; it does not count crashes.
              </Notice>
            )}

            {alerts.data && alerts.data.total_alerts > 0 && (
              <Notice tone="warn">
                {alerts.data.alerts[0]?.message ??
                  `${alerts.data.total_alerts} active weather alert${alerts.data.total_alerts === 1 ? "" : "s"}`}
                {alerts.data.total_alerts > 1 ? ` (+${alerts.data.total_alerts - 1} more)` : ""}
              </Notice>
            )}

            {risk.coverage === "rule_based_only" && (
              <Notice tone="warn">
                {noSignal
                  ? "Outside the NYC model surface. S.O.B.E.R. has no crash data here, so it will not give a risk band. Try somewhere in New York City."
                  : "Outside the NYC model surface. No crash points are shown; only the nightlife component contributes to this score."}
              </Notice>
            )}

            {!noSignal && (
            <div>
              <div className="section-heading">
                <span>What contributes</span>
              </div>
              <div className="space-y-3">
                <BreakdownBar
                  label={learned ? "Crash model (global)" : estimated ? "Road, time & weather" : "Crash model"}
                  detail={
                    learned
                      ? `${formatPercent(risk.percentile, 1)} percentile of places and hours in Dubai's city area`
                      : estimated
                      ? `estimate · ${Math.round(risk.percentile ?? 0)} / 100 on the global scale`
                      : [
                          `${formatPercent(risk.percentile, 1)} percentile`,
                          onceEvery(risk.expected_rate)
                            ? `${onceEvery(risk.expected_rate)} in this hex at this rate`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")
                  }
                  points={risk.model_points}
                  max={60}
                  color={RISK_COLORS[risk.risk_level]}
                />
                <BreakdownBar
                  label="Nightlife"
                  detail={
                    risk.data_quality.bars === "fallback"
                      ? "nightlife data unavailable right now"
                      : bars !== undefined
                        ? `${bars} bar${bars === 1 ? "" : "s"}, pubs or clubs within 1 km`
                        : "bars, pubs and clubs within 1 km"
                  }
                  points={risk.nightlife_points}
                  max={40}
                  color="#f3c15d"
                />
              </div>
            </div>
            )}

            <DangerProfileCard
              profile={risk.danger_types}
              coverage={risk.coverage}
              layer={dangerLayer}
              onLayer={onDangerLayer}
              validated={validatedDanger}
              flagged={flaggedDangers}
            />

            <ResponseGapCard
              data={responseGap}
              loading={responseGapLoading}
              on={responseGapOn}
              onToggle={onResponseGap}
              onPlace={onExplorePlace}
            />

            {(estimated || !noSignal) && (
              <div className="reason-block">
                {/* The crash model's reasons restate the numbers above; the
                    global estimate's are its inputs, so only those are listed. */}
                {estimated && (
                  <>
                    <div className="section-heading">
                      <span>{learned ? "Why here, now" : "What the estimate uses"}</span>
                    </div>
                    <ul className="reason-list">
                      {risk.reasons.slice(0, 6).map((reason) => (
                        <li key={reason}>
                          <span className="reason-mark" />
                          {reason}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {!noSignal && <p className="action-line">{risk.action}</p>}
              </div>
            )}

            <div className="divider" />

            <TrendStrip data={trend} hour={hour} onHour={onHour} />

            <div className="data-line">
              <span className="eyebrow">Data quality</span>
              <span>{weatherLabel(risk.data_quality.weather)}</span>
              <span className="dot-separator">·</span>
              <span>{risk.data_quality.bars === "offline" ? "Bars: offline index" : `Bars: ${risk.data_quality.bars}`}</span>
              {model?.version && (
                <>
                  <span className="dot-separator">·</span>
                  <span className="mono">v{model.version}</span>
                </>
              )}
            </div>

            {inNyc && <div className="divider" />}

            {inNyc && (
            <div>
              <div className="section-heading">
                <span>Crash clusters near here</span>
                <span className="mono">within 2 km</span>
              </div>
              <p className="section-note">
                Busiest spots in a 50,000-crash sample of NYC records (2012–2025), not the live
                model. Select one to score it.
              </p>
              <div className="hotspot-list">
                {(hotspots?.hotspots ?? []).slice(0, showAllHotspots ? 20 : 5).map((spot) => (
                  <button
                    key={`${spot.rank}-${spot.lat}-${spot.lon}`}
                    type="button"
                    className="hotspot-row"
                    onClick={() => onSelectHotspot({ lat: spot.lat, lon: spot.lon })}
                  >
                    <span className="hotspot-rank mono">{String(spot.rank).padStart(2, "0")}</span>
                    <span className="hotspot-name">
                      {spot.distance_m !== undefined
                        ? `${formatMetres(spot.distance_m)} away`
                        : `${spot.lat.toFixed(3)}, ${spot.lon.toFixed(3)}`}
                    </span>
                    <span className="hotspot-count mono">{spot.crash_count} crashes</span>
                    <span
                      className="hotspot-level mono"
                      style={{ color: RISK_COLORS[spot.risk_level] }}
                    >
                      {spot.risk_level}
                    </span>
                  </button>
                ))}
                {!hotspots && <LoadingBlock lines={3} />}
                {hotspots && hotspots.hotspots.length === 0 && (
                  <p className="section-note">No clusters within 2 km in the crash sample.</p>
                )}
              </div>
              {hotspots && hotspots.hotspots.length > 5 && (
                <button
                  type="button"
                  className="text-link hotspot-more"
                  onClick={() => setShowAllHotspots((current) => !current)}
                >
                  {showAllHotspots
                    ? "Show fewer"
                    : `Show all ${Math.min(20, hotspots.hotspots.length)}`}
                </button>
              )}
            </div>
            )}

            <div className="divider" />

            <div className="divider" />

            <div className="tool-stack">
              <Disclosure
                title="Nearby safe stops"
                icon={<LifeBuoy size={13} aria-hidden="true" />}
                meta={nearest.data ? `${nearest.data.total_found} found` : undefined}
                defaultOpen
                onOpenChange={onStopsOpenChange}
              >
                <NearbyStops
                  data={nearest.data}
                  loading={nearest.isLoading}
                  failed={nearest.isError}
                  selectedKey={stopKey}
                  onSelect={onSelectStop}
                />
              </Disclosure>

              <Disclosure
                title="Compare two places"
                icon={<GitCompareArrows size={13} aria-hidden="true" />}
                meta={compareTarget ? "A vs B" : "pick B"}
                defaultOpen={Boolean(compareTarget) || picking}
              >
                <ComparePanel
                  a={point}
                  b={compareTarget}
                  picking={picking}
                  data={compare.data}
                  loading={compare.isLoading}
                  error={compare.error}
                  onPick={onPickCompare}
                  onCancelPick={onCancelPick}
                  onClear={onClearCompare}
                  onSwap={onSwapCompare}
                  onRetry={() => void compare.refetch()}
                />
              </Disclosure>

              {inNyc && (
                <Disclosure
                  title="If a crash happened here"
                  icon={<FlaskConical size={13} aria-hidden="true" />}
                  meta="research only"
                >
                  <SeverityLab point={point} time={{ hour, day }} />
                </Disclosure>
              )}
            </div>

            {extraWarnings(risk.warnings).length > 0 && (
              <div className="warning-list">
                {extraWarnings(risk.warnings).map((warning) => (
                  <Notice key={warning} tone="warn">
                    {warning}
                  </Notice>
                ))}
              </div>
            )}
          </div>
        )}

        {learned && regionSurface && (
          <p className="surface-footnote">
            Map: {regionSurface.cell_count.toLocaleString()} cells of the global crash model within{" "}
            {regionSurface.radius_km} km, at {formatHour(regionSurface.hour)}
          </p>
        )}
        {surface && !learned && (
          <p className="surface-footnote">
            Map: all {surface.cell_count.toLocaleString()} modelled NYC cells at{" "}
            {formatHour(surface.hour)}
            {heatmap ? " · elsewhere a global estimate sampled every ~2.5 km" : ""}
          </p>
        )}
        {!surface && heatmap && (
          <p className="surface-footnote">
            Map: {heatmap.total_points} sampled cells around this point
          </p>
        )}
      </div>

      <div className="panel-foot">
        <button type="button" className="solid-button w-full" onClick={() => onPlanFromHere(title)}>
          <ShieldCheck size={14} />
          Plan a safer route from here
          <ArrowUpRight size={13} className="ml-auto" />
        </button>
        <button type="button" className="text-link" onClick={() => onPanel("method")}>
          <BookOpen size={12} />
          How the score is built
        </button>
      </div>
    </aside>
  );
}

function BreakdownBar({
  label,
  detail,
  points,
  max,
  color,
}: {
  label: string;
  detail: string;
  points: number;
  max: number;
  color: string;
}) {
  const width = Math.min(100, Math.max(0, (points / max) * 100));
  return (
    <div>
      <div className="breakdown-label">
        <span>{label}</span>
        <span className="mono">
          {Math.round(points)} / {max}
        </span>
      </div>
      <div className="risk-bar">
        <span style={{ width: `${width}%`, background: color }} />
      </div>
      <span className="breakdown-detail">{detail}</span>
    </div>
  );
}
