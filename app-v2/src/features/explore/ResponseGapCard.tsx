import { Cross } from "lucide-react";

import type { Place } from "@/lib/api";
import type { ResponseGap } from "@/lib/contract";
import { formatPercent } from "@/lib/risk";

import "./response-gap.css";

/**
 * "If something goes wrong here, how far away is help?" The nearest hospital to
 * the selected place, the map layer, and the worst gaps in the region.
 */
export function ResponseGapCard({
  data,
  loading,
  on,
  onToggle,
  onPlace,
}: {
  data: ResponseGap | undefined;
  loading: boolean;
  on: boolean;
  onToggle: (on: boolean) => void;
  onPlace: (place: Place) => void;
}) {
  if (loading && !data) {
    return <p className="section-note">Finding the nearest hospital…</p>;
  }
  if (!data) return null;

  return (
    <section className="gap-card" aria-label="Response gap">
      <div className="section-heading">
        <span>If something goes wrong here</span>
      </div>
      {!data.available && <p className="section-note">{data.reason}</p>}
      {data.available && (
        <>
          {data.here && (
            <div className="gap-here">
              <Cross size={16} aria-hidden="true" />
              <div>
                <strong>
                  {data.here.km.toFixed(1)} km to {data.here.hospital}
                </strong>
                <span>
                  Straight line
                  {data.here.emergency_tagged ? ", and mapped with emergency care" : ", emergency care not confirmed"}
                </span>
              </div>
            </div>
          )}
          <button
            type="button"
            className="gap-toggle"
            aria-pressed={on}
            onClick={() => onToggle(!on)}
          >
            {on ? "Hide response gaps" : "Show response gaps on the map"}
          </button>
          {on && (
            <>
              <p className="gap-summary">
                {data.counts?.gap
                  ? `${data.counts.gap} high-risk areas in ${data.region_name} are ${data.far_km} km or more from a mapped hospital at this hour.`
                  : `No high-risk area in ${data.region_name} is ${data.far_km} km or more from a mapped hospital at this hour. These are the furthest:`}
              </p>
              {(data.ranked ?? []).length > 0 && (
                <ol className="gap-list">
                  {(data.ranked ?? []).map((row) => (
                    <li key={row.cell}>
                      <button
                        type="button"
                        onClick={() =>
                          onPlace({
                            id: row.cell,
                            name: row.name ?? "Response gap",
                            detail: data.region_name ?? "",
                            lat: row.lat,
                            lon: row.lon,
                          })
                        }
                      >
                        <strong>
                          {row.name ? row.name : `${row.km} km from ${row.hospital}`}
                        </strong>
                        <span>
                          {row.name ? `${row.km} km to ${row.hospital} · ` : ""}
                          {formatPercent(Math.min(row.percentile, 99))} percentile
                        </span>
                      </button>
                    </li>
                  ))}
                </ol>
              )}
              <p className="section-note">
                {data.note} High risk means the {data.high_percentile}th percentile or above; both
                lines are design choices, not response-time standards.
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
