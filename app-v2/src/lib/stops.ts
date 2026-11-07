import type { SafeStop } from "@/lib/contract";

/** How each kind of "safe stop" is named and coloured, in the list and on the map. */
export const STOP_META: Record<string, { label: string; color: string }> = {
  police: { label: "Police", color: "#7fb6ff" },
  hospital: { label: "Hospital", color: "#ff5268" },
  clinic: { label: "Clinic", color: "#f58fb0" },
  rest_area: { label: "Rest area", color: "#f3c15d" },
  parking: { label: "Parking", color: "#8190a2" },
};

export const STOP_FALLBACK = { label: "Stop", color: "#8190a2" };

export function stopMeta(type: string) {
  return STOP_META[type] ?? STOP_FALLBACK;
}

/** A stable identity for a stop; the API gives none. */
export function stopKey(stop: Pick<SafeStop, "lat" | "lon" | "name">): string {
  return `${stop.lat.toFixed(5)},${stop.lon.toFixed(5)},${stop.name}`;
}

export function formatStopDistance(stop: Pick<SafeStop, "distance_m">): string {
  return stop.distance_m < 1000
    ? `${Math.round(stop.distance_m)} m`
    : `${(stop.distance_m / 1000).toFixed(1)} km`;
}

/**
 * Help first, convenience second: police and hospitals outrank a car park even
 * when the car park is closer. Ties fall back to distance.
 */
export function rankStops(stops: SafeStop[]): SafeStop[] {
  return [...stops].sort(
    (a, b) => a.safety_priority - b.safety_priority || a.distance_m - b.distance_m,
  );
}

/**
 * The short list shown in the explore panel: the nearest two hospitals and the
 * nearest two police stations (where they exist) always make it, and the rest
 * of the places are filled from `rankStops`. The result is in `rankStops` order.
 */
export function shortlistStops(stops: SafeStop[], count: number): SafeStop[] {
  const byDistance = (a: SafeStop, b: SafeStop) => a.distance_m - b.distance_m;
  const chosen: SafeStop[] = [];
  const add = (stop: SafeStop) => {
    if (chosen.length < count && !chosen.includes(stop)) chosen.push(stop);
  };

  for (const kind of ["hospital", "police"]) {
    stops.filter((stop) => stop.type === kind).sort(byDistance).slice(0, 2).forEach(add);
  }
  rankStops(stops).forEach(add);

  return rankStops(chosen);
}
