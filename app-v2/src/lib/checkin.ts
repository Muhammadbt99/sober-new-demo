import type { Route, RouteSegment, SafeStop } from "@/lib/contract";

/**
 * The driver check-in: what the driver says about themselves, set against what
 * the route's crash records say about that kind of crash.
 *
 * The answers change warnings, never the 0-100 score: there is no data here on
 * how much a given driver's risk rises, so no number is put on it. They stay in
 * the browser's memory and are never sent to the API.
 */

export type AwakeBand = "under12" | "12to16" | "17plus";

export type CheckIn = {
  awake: AwakeBand | null;
  nightShift: boolean;
  alcohol: boolean;
};

export const EMPTY_CHECK_IN: CheckIn = { awake: null, nightShift: false, alcohol: false };

export const AWAKE_OPTIONS: { id: AwakeBand; label: string }[] = [
  { id: "under12", label: "Under 12 h" },
  { id: "12to16", label: "12–16 h" },
  { id: "17plus", label: "17 h or more" },
];

/** A stretch counts as high for a kind of crash at twice a typical hour. A design choice. */
export const ELEVATED_RATIO = 2;

/** The danger groups these answers point to. */
export function activeGroups(checkIn: CheckIn): string[] {
  const groups: string[] = [];
  if (checkIn.alcohol) groups.push("impaired");
  if (checkIn.awake === "17plus" || checkIn.nightShift) groups.push("drowsy");
  return groups;
}

/** A word or two for the closed check-in header. */
export function checkInSummary(checkIn: CheckIn): string {
  const parts: string[] = [];
  if (checkIn.alcohol) parts.push("alcohol");
  if (checkIn.awake === "17plus") parts.push("awake 17 h+");
  if (checkIn.nightShift) parts.push("night shift");
  if (parts.length) return parts.join(", ");
  return checkIn.awake ? "fine to drive" : "not filled in";
}

function ratioIn(segment: RouteSegment, group: string): number | null {
  const found = segment.danger_types?.groups.find((item) => item.id === group);
  return found?.status === "ok" && found.rate_ratio !== undefined ? found.rate_ratio : null;
}

export function hasDangerData(segments: RouteSegment[]): boolean {
  return segments.some((segment) => segment.danger_types !== undefined);
}

/** The stretches of a route where a kind of crash runs high at this hour. */
export function elevatedStretches(segments: RouteSegment[], group: string) {
  let metres = 0;
  let worst = 0;
  let count = 0;
  for (const segment of segments) {
    const ratio = ratioIn(segment, group);
    if (ratio !== null && ratio >= ELEVATED_RATIO) {
      metres += segment.length_m;
      count += 1;
      worst = Math.max(worst, ratio);
    }
  }
  return { count, km: metres / 1000, worst };
}

/** During a drive: the kind of crash, of those flagged, running highest right here. */
export function dangerAt(
  segments: RouteSegment[],
  total: number,
  metres: number,
  groups: string[],
): { group: string; ratio: number } | null {
  const length = segments.reduce((sum, segment) => sum + segment.length_m, 0);
  const scale = length > 0 ? total / length : 1;
  let at = 0;
  for (const segment of segments) {
    at += segment.length_m * scale;
    if (metres < at) {
      let best: { group: string; ratio: number } | null = null;
      for (const group of groups) {
        const ratio = ratioIn(segment, group);
        if (ratio !== null && ratio >= ELEVATED_RATIO && ratio > (best?.ratio ?? 0)) {
          best = { group, ratio };
        }
      }
      return best;
    }
  }
  return null;
}

export type CheckInNotice = {
  tone: "stop" | "warn" | "info";
  title: string;
  detail: string;
};

const NEW_YORK_ONLY = "Crash-type records cover New York only, so this rests on your answers alone.";

function matched(label: string, found: { km: number; worst: number }): string {
  return `${label} crashes run up to ${found.worst.toFixed(1)}× a typical hour on ${found.km.toFixed(1)} km of this route.`;
}

/** What the answers mean for this route. Empty when they raise no concern. */
export function assess(checkIn: CheckIn, route: Route | undefined): CheckInNotice[] {
  const notices: CheckInNotice[] = [];
  const segments = route?.segments ?? [];
  const known = hasDangerData(segments);

  if (checkIn.alcohol) {
    const found = elevatedStretches(segments, "impaired");
    notices.push({
      tone: "stop",
      title: "Please don't drive after drinking",
      detail: `No route makes this safe: take a taxi or ride with someone sober.${
        found.count ? ` ${matched("Impaired-driver", found)}` : ""
      }`,
    });
  }

  const why =
    checkIn.awake === "17plus"
      ? "You have been awake 17 hours or more"
      : checkIn.nightShift
        ? "You have just finished a night shift"
        : null;
  if (why) {
    const found = elevatedStretches(segments, "drowsy");
    if (found.count) {
      notices.push({
        tone: "warn",
        title: `${why}, and this route is a poor match`,
        detail: `${matched("Drowsy-driver", found)} Rest before you leave, or take a break on the way.`,
      });
    } else {
      notices.push({
        tone: "info",
        title: why,
        detail: known
          ? "No stretch of this route runs high for drowsy crashes at this hour, but a break first still helps."
          : `${NEW_YORK_ONLY} A break first helps.`,
      });
    }
  }
  return notices;
}

/** Places to rest, nearest first: rest areas before car parks. */
export function restStops(stops: SafeStop[], limit = 2): SafeStop[] {
  const rank = (stop: SafeStop) => (/rest/.test(stop.type) ? 0 : stop.type === "parking" ? 1 : 2);
  const names = new Set<string>();
  return stops
    .filter((stop) => rank(stop) < 2)
    .sort((a, b) => rank(a) - rank(b) || a.distance_m - b.distance_m)
    .filter((stop) => !names.has(stop.name) && names.add(stop.name))
    .slice(0, limit);
}
