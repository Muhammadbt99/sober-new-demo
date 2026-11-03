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
import { RiskPill } from "@/components/RiskPill";
import { TrendStrip } from "@/components/TrendStrip";
import { ComparePanel } from "@/features/explore/ComparePanel";
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
} from "@/lib/contract";
import type { Place, Point } from "@/lib/api";
import { NYC_SHORTCUTS, WORLD_SHORTCUTS, inNycBox, samePoint } from "@/lib/places";
import {
  COVERAGE_LABEL,
  DAY_SHORT,
  RISK_COLORS,
  formatHour,
  formatPercent,
  formatRate,
  weatherLabel,
} from "@/lib/risk";
import type { Panel } from "@/lib/use-url-state";

function formatMetres(metres: number): string {
  return metres < 1000 ? `${Math.round(metres / 10) * 10} m` : `${(metres / 1000).toFixed(1)} km`;
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
  /** Name of the point when it came from search or a shortcut. */
  placeName: string | null;
  /** Go to a searched or shortcut place. */
  onExplorePlace: (place: Place) => void;
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
  const model = health?.checks.crash_risk_model;
  // Outside the trained area with nothing else to go on there is no signal at
  // all, and a green "LOW 0" would read as a confident all-clear.
  // Outside NYC the API answers with a labelled global estimate, not the model.
  const estimated = risk?.coverage === "context";
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
          <div className="explore-shortcuts" role="group" aria-label="Jump to a place">
            {[...NYC_SHORTCUTS, ...WORLD_SHORTCUTS].map((place) => (
              <button
                key={place.id}
                type="button"
                className="chip demo-city"
                data-active={samePoint(place, point) || undefined}
                title={`${place.name}, ${place.detail}`}
                onClick={() => onExplorePlace(place)}
              >
                {place.id.startsWith("q-") && WORLD_SHORTCUTS.includes(place) ? place.detail : place.name}
              </button>
            ))}
          </div>
          <p className="explore-hint">Or click anywhere on the map. Hover a hex to read it.</p>
        </div>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="eyebrow">
              {risk?.coverage === "context"
                ? "Global estimate"
                : risk?.coverage === "model"
                  ? "NYC crash model"
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
                {formatHour(hour)} / {DAY_SHORT[day]}
              </span>
            </p>
          </div>
          {risk && !noSignal && <RiskPill level={risk.risk_level} />}
        </div>

        {risk && noSignal && (
          <div className="score-line">
            <div className="risk-number" style={{ color: "var(--color-ink-dim, #8a97a8)" }}>
              —
            </div>
            <div className="score-meta">
              <span className="eyebrow">Composite score / 100</span>
              <span className="score-band" style={{ color: "var(--color-ink-dim, #8a97a8)" }}>
                No model coverage
              </span>
              <span className="score-coverage">{COVERAGE_LABEL[risk.coverage]}</span>
            </div>
          </div>
        )}

        {risk && !noSignal && (
          <div className="score-line">
            <div className="risk-number" style={{ color: RISK_COLORS[risk.risk_level] }}>
              {Math.round(risk.risk_score)}
            </div>
            <div className="score-meta">
              <span className="eyebrow">Composite score / 100</span>
              <span className="score-band" style={{ color: RISK_COLORS[risk.risk_level] }}>
                {risk.risk_level} exposure
              </span>
              <span className="score-coverage">{COVERAGE_LABEL[risk.coverage]}</span>
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
            {estimated && (
              <Notice tone="info">
                New York&apos;s crash model has no data here, so this is a global estimate from
                the country&apos;s road-death rate, the road, the hour, live weather and nightlife.
                It is not a trained crash model.
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
                <span className="mono">{Math.round(risk.risk_score)} / 100</span>
              </div>
              <div className="space-y-3">
                <BreakdownBar
                  label={estimated ? "Road, time & weather" : "Crash model"}
                  detail={
                    estimated
                      ? `estimate · ${Math.round(risk.percentile ?? 0)} / 100 on the global scale`
                      : `${formatPercent(risk.percentile, 1)} percentile`
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

            {estimated && risk.context && (
              <div className="rate-grid">
                <div>
                  <span className="eyebrow">Country baseline</span>
                  <strong className="mono">
                    {risk.context.deaths_per_100k ?? "—"}
                  </strong>
                  <span className="metric-note">
                    road deaths per 100k · {risk.context.country_name ?? "world median"}
                  </span>
                </div>
                <div>
                  <span className="eyebrow">Road here</span>
                  <strong>{(risk.context.road_class ?? "unknown").replaceAll("_", " ")}</strong>
                  <span className="metric-note">{risk.context.road_name ?? "OpenStreetMap"}</span>
                </div>
              </div>
            )}

            {!noSignal && !estimated && (
            <div className="rate-grid">
              <div>
                <span className="eyebrow">Expected rate</span>
                <strong className="mono">{formatRate(risk.expected_rate)}</strong>
                <span className="metric-note">injury / fatal crashes per cell-hour</span>
              </div>
              <div>
                <span className="eyebrow">Area rate</span>
                <strong className="mono">
                  {risk.expected_rate_per_km2_hour === null
                    ? "—"
                    : risk.expected_rate_per_km2_hour.toFixed(4)}
                </strong>
                <span className="metric-note">per km² per hour</span>
              </div>
            </div>
            )}

            <div className="reason-block">
              <div className="section-heading">
                <span>Why this score</span>
                <span className="mono">{risk.reasons.length} signals</span>
              </div>
              <ul className="reason-list">
                {risk.reasons.slice(0, estimated ? 6 : 4).map((reason) => (
                  <li key={reason}>
                    <span className="reason-mark" />
                    {reason}
                  </li>
                ))}
              </ul>
              {!noSignal && <p className="action-line">{risk.action}</p>}
            </div>

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

            <div className="divider" />

            <div>
              <div className="section-heading">
                <span>Crash clusters near here</span>
                <span className="mono">within 2 km</span>
              </div>
              <p className="section-note">
                {inNyc
                  ? "Busiest spots in a 50,000-crash sample of NYC records (2012–2025), not the live model. Select one to score it."
                  : "Crash records cover New York City only, so there are no clusters to show here."}
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
                {inNyc && hotspots && hotspots.hotspots.length === 0 && (
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

            <div className="divider" />

            <div className="support-grid">
              <div className="support-card">
                <span className="eyebrow">Nearest safe stop</span>
                {nearest.data ? (
                  <>
                    <strong>{nearest.data.nearest_stop.name}</strong>
                    <span className="mono">
                      {nearest.data.nearest_stop.type} · {nearest.data.nearest_stop.distance_km} km
                    </span>
                  </>
                ) : nearest.isError ? (
                  <span className="metric-note">Unavailable in this build</span>
                ) : (
                  <span className="metric-note">Searching…</span>
                )}
              </div>
              <div className="support-card">
                <span className="eyebrow">Weather</span>
                {alerts.data ? (
                  <>
                    <strong>
                      {alerts.data.total_alerts > 0
                        ? `${alerts.data.total_alerts} active alert${alerts.data.total_alerts === 1 ? "" : "s"}`
                        : "No active alerts"}
                    </strong>
                    <span className="mono">{weatherLabel(alerts.data.weather_source)}</span>
                    {alerts.data.alerts[0] && (
                      <span className="metric-note">{alerts.data.alerts[0].message}</span>
                    )}
                  </>
                ) : (
                  <span className="metric-note">Weather check unavailable</span>
                )}
              </div>
            </div>

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

              <Disclosure
                title="If a crash happened here"
                icon={<FlaskConical size={13} aria-hidden="true" />}
                meta="research only"
              >
                {inNyc ? (
                  <SeverityLab point={point} time={{ hour, day }} />
                ) : (
                  <p className="section-note">
                    The severity model was trained on New York crashes only, so it has
                    nothing to say about this place.
                  </p>
                )}
              </Disclosure>
            </div>

            {risk.warnings.length > 0 && (
              <div className="warning-list">
                {risk.warnings.map((warning) => (
                  <Notice key={warning} tone="warn">
                    {warning}
                  </Notice>
                ))}
              </div>
            )}
          </div>
        )}

        {surface && (
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
