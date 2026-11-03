import { ArrowLeftRight, Crosshair, X } from "lucide-react";

import { ErrorBlock, LoadingBlock } from "@/components/DataState";
import { RiskPill } from "@/components/RiskPill";
import type { CompareLocation, RiskCompareResponse } from "@/lib/contract";
import type { Point } from "@/lib/api";
import { COVERAGE_LABEL, RISK_COLORS, formatPercent, formatRate } from "@/lib/risk";
import { cn } from "@/lib/utils";

function Side({
  tag,
  place,
  safer,
}: {
  tag: "A" | "B";
  place: CompareLocation;
  safer: boolean;
}) {
  const color = RISK_COLORS[place.risk_level];
  return (
    <div className={cn("compare-side", safer && "is-safer")}>
      <div className="compare-side-head">
        <span className="compare-tag" data-tag={tag}>
          {tag}
        </span>
        {safer && <span className="chip chip-good">Safer</span>}
      </div>
      <div className="compare-score" style={{ color }}>
        {Math.round(place.risk_score)}
      </div>
      <RiskPill level={place.risk_level} />
      <dl className="compare-facts">
        <div>
          <dt>Percentile</dt>
          <dd className="mono">{formatPercent(place.percentile, 1)}</dd>
        </div>
        <div>
          <dt>Rate</dt>
          <dd className="mono">{formatRate(place.expected_rate)}</dd>
        </div>
        <div>
          <dt>Coverage</dt>
          <dd>{COVERAGE_LABEL[place.coverage]}</dd>
        </div>
      </dl>
      {place.place?.name && <strong className="compare-name">{place.place.name}</strong>}
      <span className="compare-coord mono">
        {place.lat.toFixed(4)}, {place.lon.toFixed(4)}
      </span>
    </div>
  );
}

/**
 * Put two places side by side on the same model, at the same hour. Place A is
 * the point already selected; place B is picked by clicking the map.
 */
export function ComparePanel({
  b,
  picking,
  data,
  loading,
  error,
  onPick,
  onCancelPick,
  onClear,
  onSwap,
  onRetry,
}: {
  a: Point;
  b: Point | null;
  picking: boolean;
  data: RiskCompareResponse | undefined;
  loading: boolean;
  error: unknown;
  onPick: () => void;
  onCancelPick: () => void;
  onClear: () => void;
  onSwap: () => void;
  onRetry: () => void;
}) {
  if (!b) {
    return (
      <div className="compare-empty">
        <p className="section-note">
          Score two places at the same hour on the same model. Place A is the point you have
          selected; choose place B on the map.
        </p>
        {picking ? (
          <button type="button" className="ghost-button is-picking" onClick={onCancelPick}>
            <Crosshair size={12} aria-hidden="true" />
            Click the map to place B · Cancel
          </button>
        ) : (
          <button type="button" className="ghost-button" onClick={onPick}>
            <Crosshair size={12} aria-hidden="true" />
            Pick place B on the map
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="compare-body">
      {loading && !data && <LoadingBlock lines={4} />}
      {Boolean(error) && !data && (
        <ErrorBlock error={error} onRetry={onRetry} title="Comparison unavailable" />
      )}
      {data && (
        <>
          <div className="compare-grid">
            <Side tag="A" place={data.location_a} safer={data.safer_location === "A"} />
            <Side tag="B" place={data.location_b} safer={data.safer_location === "B"} />
          </div>
          <p className="compare-verdict">
            {data.safer_location === "EQUAL"
              ? "Both places score the same."
              : `${data.safer_location} scores ${Math.abs(data.risk_difference).toFixed(0)} points lower.`}{" "}
            <span>{data.recommendation}</span>
          </p>
        </>
      )}
      <div className="compare-actions">
        <button type="button" className="ghost-button" onClick={onSwap}>
          <ArrowLeftRight size={12} aria-hidden="true" />
          Swap A and B
        </button>
        <button type="button" className="ghost-button" onClick={onPick}>
          <Crosshair size={12} aria-hidden="true" />
          {picking ? "Click the map…" : "Move B"}
        </button>
        <button type="button" className="ghost-button" onClick={onClear}>
          <X size={12} aria-hidden="true" />
          Clear
        </button>
      </div>
    </div>
  );
}
