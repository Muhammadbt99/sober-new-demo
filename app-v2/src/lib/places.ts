import type { Place, Point } from "@/lib/api";

/** The box the NYC crash model and its offline indexes cover (features.py). */
export const NYC_BOX = { south: 40.4, north: 41.0, west: -74.5, east: -73.5 } as const;

export function inNycBox(point: Point): boolean {
  return (
    point.lat >= NYC_BOX.south &&
    point.lat <= NYC_BOX.north &&
    point.lon >= NYC_BOX.west &&
    point.lon <= NYC_BOX.east
  );
}

const place = (id: string, name: string, detail: string, lat: number, lon: number): Place => ({
  id,
  name,
  detail,
  lat,
  lon,
});

/**
 * One-tap places for the Explore tab: a spread of New York (the crash model)
 * and a few cities elsewhere (the labelled global estimate).
 */
export const NYC_SHORTCUTS: Place[] = [
  place("q-times-square", "Times Square", "Midtown, Manhattan", 40.758, -73.9855),
  place("q-williamsburg", "Williamsburg", "Brooklyn", 40.7143, -73.9613),
  place("q-harlem", "Harlem", "Manhattan", 40.8116, -73.9465),
  place("q-downtown-bk", "Downtown Brooklyn", "Brooklyn", 40.6928, -73.9901),
  place("q-flushing", "Flushing", "Queens", 40.7596, -73.8303),
  place("q-fordham", "Fordham", "The Bronx", 40.8615, -73.8905),
  place("q-st-george", "St. George", "Staten Island", 40.6437, -74.0736),
];

export const WORLD_SHORTCUTS: Place[] = [
  place("q-london", "Trafalgar Square", "London", 51.508, -0.1281),
  place("q-paris", "Arc de Triomphe", "Paris", 48.8738, 2.295),
  place("q-dubai", "Downtown Dubai", "Dubai", 25.1972, 55.2744),
  place("q-tokyo", "Shibuya Crossing", "Tokyo", 35.6595, 139.7005),
];

/** True when two points are the same place for naming purposes (~10 m). */
export function samePoint(a: Point, b: Point): boolean {
  return Math.abs(a.lat - b.lat) < 1e-4 && Math.abs(a.lon - b.lon) < 1e-4;
}
