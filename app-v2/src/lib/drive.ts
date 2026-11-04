import type { GuidanceStep, RouteSegment } from "@/lib/contract";

/**
 * The arithmetic of a simulated drive: where the car is, which way it faces,
 * what the next turn is and what risk lies ahead. Everything is a function of
 * metres along the route, so the camera and the HUD cannot disagree.
 */

const RAD = Math.PI / 180;
const EARTH_RADIUS_M = 6_371_000;

export type DrivePath = {
  points: [number, number][];
  /** Metres from the start to each point. */
  along: number[];
  total: number;
};

function metresBetween(a: [number, number], b: [number, number]): number {
  const dLat = (b[0] - a[0]) * RAD;
  const dLon = (b[1] - a[1]) * RAD;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a[0] * RAD) * Math.cos(b[0] * RAD) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function buildPath(geometry: number[][]): DrivePath {
  const points = geometry.map((point) => [point[0]!, point[1]!] as [number, number]);
  const along = [0];
  for (let index = 1; index < points.length; index += 1) {
    along.push(along[index - 1]! + metresBetween(points[index - 1]!, points[index]!));
  }
  return { points, along, total: along[along.length - 1] ?? 0 };
}

/** The point ``metres`` along the path, clamped to its ends. */
export function pointAt(path: DrivePath, metres: number): [number, number] {
  const distance = Math.min(path.total, Math.max(0, metres));
  let low = 0;
  let high = path.along.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (path.along[middle]! <= distance) low = middle;
    else high = middle;
  }
  const start = path.along[low]!;
  const span = path.along[high]! - start;
  const share = span > 0 ? (distance - start) / span : 0;
  const a = path.points[low]!;
  const b = path.points[high]!;
  return [a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share];
}

/** Compass bearing from one point to another, 0-360. */
export function bearingBetween(a: [number, number], b: [number, number]): number {
  const phi1 = a[0] * RAD;
  const phi2 = b[0] * RAD;
  const dLon = (b[1] - a[1]) * RAD;
  const y = Math.sin(dLon) * Math.cos(phi2);
  const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon);
  return (Math.atan2(y, x) / RAD + 360) % 360;
}

/** The way to face at ``metres``, looking a little down the road. */
export function headingAt(path: DrivePath, metres: number, lookAhead: number): number {
  const here = Math.min(metres, Math.max(0, path.total - 1));
  return bearingBetween(pointAt(path, here), pointAt(path, here + lookAhead));
}

/** Turn ``from`` towards ``to`` by ``share``, the short way round. */
export function turnTowards(from: number, to: number, share: number): number {
  const difference = ((to - from + 540) % 360) - 180;
  return (from + difference * share + 360) % 360;
}

export type RiskZone = {
  from_m: number;
  to_m: number;
  level: "HIGH" | "CRITICAL";
  reason: string | null;
};

/** Runs of high-risk stretches, in metres along the route. */
export function riskZones(segments: RouteSegment[], total: number): RiskZone[] {
  const length = segments.reduce((sum, segment) => sum + segment.length_m, 0);
  const scale = length > 0 ? total / length : 1;
  const zones: RiskZone[] = [];
  let at = 0;
  for (const segment of segments) {
    const next = at + segment.length_m * scale;
    if (segment.risk_level === "HIGH" || segment.risk_level === "CRITICAL") {
      const last = zones[zones.length - 1];
      if (last && last.to_m >= at - 1) {
        last.to_m = next;
        if (segment.risk_level === "CRITICAL") last.level = "CRITICAL";
      } else {
        zones.push({
          from_m: at,
          to_m: next,
          level: segment.risk_level,
          reason: segment.reasons?.[0] ?? null,
        });
      }
    }
    at = next;
  }
  return zones;
}

export type RiskNotice =
  | { kind: "inside"; zone: RiskZone; distance_m: number }
  | { kind: "ahead"; zone: RiskZone; distance_m: number };

/** The risk to mention now: the stretch you are in, or the next one in range. */
export function riskNotice(zones: RiskZone[], metres: number, range: number): RiskNotice | null {
  for (const zone of zones) {
    if (metres >= zone.from_m && metres < zone.to_m) {
      return { kind: "inside", zone, distance_m: zone.to_m - metres };
    }
    if (zone.from_m > metres) {
      const distance_m = zone.from_m - metres;
      return distance_m <= range ? { kind: "ahead", zone, distance_m } : null;
    }
  }
  return null;
}

/** The next instruction ahead, with offsets stretched to fit this path. */
export function nextStep(
  steps: GuidanceStep[],
  metres: number,
  scale: number,
): { step: GuidanceStep; distance_m: number } | null {
  for (const step of steps) {
    const at = step.offset_m * scale;
    if (at > metres + 5) return { step, distance_m: at - metres };
  }
  return null;
}

/** "400 m", rounded the way a voice would say it, or "1.2 km". */
export function spokenDistance(metres: number): string {
  if (metres >= 1000) return `${(metres / 1000).toFixed(1)} km`;
  return `${Math.max(10, Math.round(metres / (metres >= 200 ? 50 : 10)) * (metres >= 200 ? 50 : 10))} m`;
}

export type TurnShape = "left" | "right" | "straight" | "uturn" | "roundabout" | "arrive";

/** Which arrow an instruction gets, from TomTom's manoeuvre name. */
export function turnShape(maneuver: string): TurnShape {
  if (maneuver.includes("ARRIVE")) return "arrive";
  if (maneuver.includes("ROUNDABOUT")) return "roundabout";
  if (maneuver.includes("UTURN") || maneuver.includes("TRY_MAKE_UTURN")) return "uturn";
  if (maneuver.includes("LEFT")) return "left";
  if (maneuver.includes("RIGHT")) return "right";
  return "straight";
}
