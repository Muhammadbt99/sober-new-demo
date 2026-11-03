import { MapPin } from "lucide-react";

import { LoadingBlock } from "@/components/DataState";
import type { NearestSafeResponse, SafeStop } from "@/lib/contract";
import { cn } from "@/lib/utils";
import { formatStopDistance, rankStops, stopKey, stopMeta } from "@/lib/stops";

const VISIBLE = 6;

/**
 * Every safe stop the API found, not just the single nearest. Selecting one
 * flies the map to it; all of them are drawn on the map while the section is
 * open.
 */
export function NearbyStops({
  data,
  loading,
  failed,
  selectedKey,
  onSelect,
}: {
  data: NearestSafeResponse | undefined;
  loading: boolean;
  failed: boolean;
  selectedKey: string | null;
  onSelect: (stop: SafeStop | null) => void;
}) {
  if (loading && !data) return <LoadingBlock lines={3} />;
  if (failed || !data) {
    return <p className="section-note">Safe stops are unavailable in this build.</p>;
  }

  const ranked = rankStops(data.stops).slice(0, VISIBLE);
  if (ranked.length === 0) {
    return (
      <p className="section-note">
        No stops within {(data.search_radius_m / 1000).toFixed(1)} km of this point.
      </p>
    );
  }

  return (
    <div className="stop-list">
      <p className="section-note">
        {data.total_found} within {(data.search_radius_m / 1000).toFixed(1)} km. Police and
        hospitals first, then the closest. Select one to see it on the map.
      </p>
      {ranked.map((stop) => {
        const meta = stopMeta(stop.type);
        const key = stopKey(stop);
        const active = key === selectedKey;
        return (
          <button
            key={key}
            type="button"
            className={cn("stop-row", active && "is-active")}
            aria-pressed={active}
            onClick={() => onSelect(active ? null : stop)}
          >
            <span className="stop-dot" style={{ background: meta.color }} aria-hidden="true" />
            <span className="stop-main">
              <strong>{stop.name}</strong>
              <span>{meta.label}</span>
            </span>
            <span className="stop-distance mono">
              <MapPin size={10} aria-hidden="true" />
              {formatStopDistance(stop)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
