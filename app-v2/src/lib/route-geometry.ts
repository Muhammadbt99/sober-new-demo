import type { Route } from "@/lib/contract";

/** `[lon, lat]`, the order GeoJSON and MapLibre use. */
export type LonLat = [number, number];

const EARTH_RADIUS_M = 6_371_000;
const RAD = Math.PI / 180;

function haversineM(a: [number, number], b: [number, number]): number {
  const dLat = (b[0] - a[0]) * RAD;
  const dLon = (b[1] - a[1]) * RAD;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a[0] * RAD) * Math.cos(b[0] * RAD) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Index `k` with `cumulative[k] <= distance < cumulative[k + 1]`, clamped to the line. */
function locate(cumulative: number[], distance: number): number {
  const last = cumulative.length - 1;
  if (distance <= 0) return 0;
  if (distance >= cumulative[last]!) return last - 1;
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cumulative[mid]! <= distance) lo = mid;
    else hi = mid;
  }
  return lo;
}

function pointAt(
  points: Array<[number, number]>,
  cumulative: number[],
  index: number,
  distance: number,
): LonLat {
  const a = points[index]!;
  const b = points[index + 1] ?? a;
  const span = cumulative[index + 1]! - cumulative[index]!;
  const t = span > 0 ? Math.min(1, Math.max(0, (distance - cumulative[index]!) / span)) : 0;
  return [a[1] + (b[1] - a[1]) * t, a[0] + (b[0] - a[0]) * t];
}

function chords(route: Route): LonLat[][] {
  return route.segments.map((segment) => [
    [segment.start[1], segment.start[0]],
    [segment.end[1], segment.end[0]],
  ]);
}

const cache = new WeakMap<Route, LonLat[][]>();

/**
 * Each scored stretch of a route as a line that follows the road.
 *
 * The API scores a route in stretches (one per H3 cell it crosses) and gives
 * each stretch only its entry and exit point. Joining those two points with a
 * straight line cut corners: at street zoom a route across the Brooklyn Bridge
 * ran over open water. The stretches tile the route in order, and their
 * `length_m` values add up to its length, so this cuts the route's own
 * geometry at the same distances and keeps every bend in between.
 */
export function segmentPaths(route: Route): LonLat[][] {
  const cached = cache.get(route);
  if (cached) return cached;

  const points = route.geometry;
  const segments = route.segments;
  let paths: LonLat[][];
  if (points.length < 2 || segments.length === 0) {
    paths = chords(route);
  } else {
    const cumulative = [0];
    for (let i = 1; i < points.length; i += 1) {
      cumulative.push(cumulative[i - 1]! + haversineM(points[i - 1]!, points[i]!));
    }
    const lineLength = cumulative[cumulative.length - 1]!;
    const scoredLength = segments.reduce((sum, segment) => sum + segment.length_m, 0);
    if (!(lineLength > 0) || !(scoredLength > 0)) {
      paths = chords(route);
    } else {
      // The two lengths agree to well under a metre; scaling absorbs rounding.
      const scale = lineLength / scoredLength;
      let along = 0;
      paths = segments.map((segment) => {
        const from = along * scale;
        along += segment.length_m;
        const to = along * scale;
        const first = locate(cumulative, from);
        const last = locate(cumulative, to);
        const path: LonLat[] = [pointAt(points, cumulative, first, from)];
        for (let vertex = first + 1; vertex <= last; vertex += 1) {
          const [lat, lon] = points[vertex]!;
          path.push([lon, lat]);
        }
        path.push(pointAt(points, cumulative, last, to));
        return path;
      });
    }
  }
  cache.set(route, paths);
  return paths;
}
