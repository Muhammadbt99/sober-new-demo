import { Check, GitBranch, ShieldCheck, Timer, Waypoints } from "lucide-react";

import { RiskPill } from "@/components/RiskPill";
import type { Route } from "@/lib/contract";
import { RISK_COLORS, formatCrashes, formatDistance, formatDuration } from "@/lib/risk";
import { routeLabel, tradeoff } from "@/lib/routes";
import { cn } from "@/lib/utils";

function signed(value: number, digits: number): string {
  return `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(digits)}`;
}

/**
 * One route in the comparison. The selected route is shown in full; the others
 * fold down to a two-line row so that five alternatives fit in the panel and
 * can be compared by their bars at a glance.
 */
export function RouteCard({
  route,
  selected,
  fastest,
  maxExpected,
  onSelect,
}: {
  route: Route;
  selected: boolean;
  fastest: Route | undefined;
  /** The largest expected-crash total among the routes, so the bars share a scale. */
  maxExpected: number;
  onSelect: () => void;
}) {
  const delta = tradeoff(route, fastest);
  const share = maxExpected > 0 ? Math.max(4, (route.expected_crashes_total / maxExpected) * 100) : 4;
  const color = RISK_COLORS[route.risk_level];
  const kind = (
    <span className={cn("route-kind", route.is_safest && "is-safe")}>
      {route.is_safest ? <ShieldCheck size={12} /> : <Timer size={12} />}
      {routeLabel(route)}
    </span>
  );
  const bar = (
    <div
      className="exposure-bar"
      role="img"
      aria-label={`Exposure ${formatCrashes(route.expected_crashes_total)} against the highest of ${formatCrashes(maxExpected)}`}
    >
      <span style={{ width: `${share}%`, background: color }} />
    </div>
  );
  const crashDelta =
    delta?.crashes !== null && delta?.crashes !== undefined ? (
      <span className={cn("tradeoff-crashes mono", delta.crashes < 0 ? "is-better" : "is-worse")}>
        {signed(delta.crashes, 0)}%
      </span>
    ) : null;

  if (!selected) {
    return (
      <button
        type="button"
        className="route-card is-compact"
        aria-pressed={false}
        onClick={onSelect}
        aria-label={`Show ${routeLabel(route)}: ${formatDuration(route.duration_min)}, ${formatDistance(route.distance_km)}, ${formatCrashes(route.expected_crashes_total)} expected crashes`}
      >
        <div className="route-compact-row">
          {kind}
          <span className="route-compact-meta mono">
            {formatDuration(route.duration_min)} · {formatDistance(route.distance_km)}
          </span>
          <RiskPill level={route.risk_level} />
        </div>
        <div className="route-compact-row">
          {bar}
          <strong className="mono route-compact-crashes">
            {formatCrashes(route.expected_crashes_total)}
          </strong>
          {crashDelta}
        </div>
      </button>
    );
  }

  return (
    <button type="button" className="route-card is-selected" aria-pressed onClick={onSelect}>
      <div className="route-card-top">
        {kind}
        <RiskPill level={route.risk_level} />
      </div>

      <div className="route-card-metrics">
        <div>
          <strong className="route-time">{formatDuration(route.duration_min)}</strong>
          <span className="route-distance">{formatDistance(route.distance_km)}</span>
        </div>
        <div className="route-exposure">
          <strong className="mono">{formatCrashes(route.expected_crashes_total)}</strong>
          <span>{route.coverage === "context" ? "relative risk / trip" : "expected crashes / trip"}</span>
        </div>
      </div>

      {bar}

      <div className="route-card-foot">
        {delta && (
          <span className="tradeoff mono" title="Compared with the fastest route">
            {delta.minutes !== null && <span>{signed(delta.minutes, 1)} min</span>}
            <span>{signed(delta.km, 1)} km</span>
            {delta.crashes !== null && (
              <span className={delta.crashes < 0 ? "is-better" : "is-worse"}>
                {signed(delta.crashes, 0)}% crashes
              </span>
            )}
          </span>
        )}
        {route.via && (
          <span
            className="chip"
            title="Found by routing through a waypoint beside the fastest route"
          >
            <Waypoints size={10} aria-hidden="true" /> via waypoint
          </span>
        )}
        {route.synthetic && (
          <span className="chip chip-warn">
            <GitBranch size={9} /> synthetic detour
          </span>
        )}
        {route.coverage !== "model" && (
          <span className="chip">{route.coverage.replaceAll("_", " ")}</span>
        )}
        <span className="route-selected-mark">
          <Check size={11} /> on map
        </span>
      </div>
    </button>
  );
}
