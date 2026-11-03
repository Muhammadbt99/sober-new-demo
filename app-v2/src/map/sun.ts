/**
 * Real-time solar geometry for the globe.
 *
 * MapLibre has no sun. Its `light` style property only shades *extruded*
 * geometries (fill-extrusion), and the engine contains no terminator or
 * subsolar code at all, so the day/night look has to be drawn as an overlay.
 * This module supplies the astronomy; `SunLighting` turns it into pixels.
 *
 * The subsolar point is derived from the NOAA fractional-year approximation.
 * It is good to about half a degree (the declination is up to ~0.4 degrees off
 * around the equinoxes), roughly one pixel on the globe at its usual zoom, which
 * is plenty for a day/night picture.
 */

const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;

export type LngLat = { lat: number; lon: number };

/** Normalise a longitude into [-180, 180). */
export function normalizeLon(lon: number): number {
  let value = ((lon + 180) % 360 + 360) % 360 - 180;
  if (Object.is(value, -180)) value = 180;
  return value;
}

/**
 * The point on earth where the sun is directly overhead right now.
 * Longitude advances 15 degrees per hour, which is what makes the terminator
 * drift across the globe in real time.
 */
export function subsolarPoint(date: Date): LngLat {
  const start = Date.UTC(date.getUTCFullYear(), 0, 0);
  const dayOfYear = Math.floor((date.getTime() - start) / 86_400_000);
  const utcHours =
    date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600;

  // Fractional year, in radians.
  const gamma = ((2 * Math.PI) / 365) * (dayOfYear - 1 + (utcHours - 12) / 24);

  // Equation of time, in minutes.
  const eqTime =
    229.18 *
    (0.000075 +
      0.001868 * Math.cos(gamma) -
      0.032077 * Math.sin(gamma) -
      0.014615 * Math.cos(2 * gamma) -
      0.040849 * Math.sin(2 * gamma));

  // Solar declination, in radians.
  const decl =
    0.006918 -
    0.399912 * Math.cos(gamma) +
    0.070257 * Math.sin(gamma) -
    0.006758 * Math.cos(2 * gamma) +
    0.000907 * Math.sin(2 * gamma) -
    0.002697 * Math.cos(3 * gamma) +
    0.00148 * Math.sin(3 * gamma);

  const lon = -15 * (utcHours + eqTime / 60 - 12);
  return { lat: decl * DEG, lon: normalizeLon(lon) };
}

// Constructing an Intl.DateTimeFormat costs ~0.3 ms; the sun overlay used to do
// it several times per animation frame. One formatter per zone is enough.
const formatters = new Map<string, Intl.DateTimeFormat>();

function zonedFormatter(timeZone: string) {
  let formatter = formatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    formatters.set(timeZone, formatter);
  }
  return formatter;
}

/** Wall-clock fields of an instant, as seen in a specific IANA time zone. */
function zonedFields(date: Date, timeZone: string) {
  const parts = zonedFormatter(timeZone).formatToParts(date);
  const pick = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value ?? "0");
  return {
    year: pick("year"),
    month: pick("month"),
    day: pick("day"),
    // Some ICU builds render midnight as 24 rather than 00.
    hour: pick("hour") % 24,
    minute: pick("minute"),
    second: pick("second"),
  };
}

/** Calendar fields of an instant, as seen in a specific IANA time zone. */
function zonedParts(date: Date, timeZone: string) {
  const { year, month, day } = zonedFields(date, timeZone);
  return { year, month, day };
}

/** UTC offset of `timeZone` at `instant`, in milliseconds, east positive. */
function zoneOffsetMs(instant: Date, timeZone: string): number {
  const f = zonedFields(instant, timeZone);
  const asUtc = Date.UTC(f.year, f.month - 1, f.day, f.hour, f.minute, f.second);
  return asUtc - Math.floor(instant.getTime() / 1000) * 1000;
}

/**
 * The instant at which the clock in `timeZone` reads `hour:00`.
 *
 * The demo's time dock is New York wall-clock time ("Saturday, 11 PM"), so the
 * sun has to be evaluated at the same instant the risk model scores. Reading
 * the host clock instead is what lets a 23:00 scenario render as mid-morning.
 */
export function scenarioInstant(
  hour: number,
  base: Date = new Date(),
  timeZone = "America/New_York",
): Date {
  const { year, month, day } = zonedParts(base, timeZone);
  const guess = Date.UTC(year, month - 1, day, hour, 0, 0);

  // `guess` is the wall-clock stamp read as if it were UTC. Subtracting the
  // zone's offset at that guess is the usual single-pass conversion, but the
  // offset at the guess is not always the offset at the answer. On the
  // fall-back day an 02:00 guess samples EDT and lands on an instant that is
  // already EST, an hour early. So sample the offset again at the candidate
  // and keep whichever candidate reads back as the requested hour. In the
  // spring-forward gap neither does; the first candidate is kept, which
  // shifts forward past the missing hour like Temporal's "compatible" mode.
  const first = guess - zoneOffsetMs(new Date(guess), timeZone);
  if (zonedFields(new Date(first), timeZone).hour === hour) {
    return new Date(first);
  }

  const second = guess - zoneOffsetMs(new Date(first), timeZone);
  if (zonedFields(new Date(second), timeZone).hour === hour) {
    return new Date(second);
  }

  return new Date(first);
}

/** Sub-solar point for the hour shown in the app, not for the host clock. */
export function scenarioSubsolarPoint(hour: number, base: Date = new Date()): LngLat {
  return subsolarPoint(scenarioInstant(hour, base));
}

/** Shortest signed longitude difference `a - b`, in [-180, 180). */
export function lonDelta(a: number, b: number): number {
  return normalizeLon(a - b);
}

/**
 * Camera position for the globe, relative to the sun rather than to a fixed
 * longitude.
 *
 * A fixed centre cannot work: at 23:00 New York the sun is 152 degrees away, so
 * every point within sight of the city is night and the hero shot is a black
 * ball. Anchoring the camera to the sun instead keeps the composition stable at
 * every hour - and because the offset is fixed, scrubbing the dock time spins
 * the earth underneath the light, which is the rotation cue.
 *
 * 72 degrees west of the subsolar meridian leaves the disc about two thirds
 * lit, with the terminator as a crescent on the far side and the sun itself out
 * in open sky rather than over land.
 */
export function globeFraming(hour: number, base: Date = new Date()): [number, number] {
  const { lon: subsolarLon } = scenarioSubsolarPoint(hour, base);
  return [normalizeLon(subsolarLon - 72), 0];
}

/** Great-circle angular distance in degrees between two points. */
export function angularDistance(a: LngLat, b: LngLat): number {
  const φ1 = a.lat * RAD;
  const φ2 = b.lat * RAD;
  const dλ = (b.lon - a.lon) * RAD;
  const cos =
    Math.sin(φ1) * Math.sin(φ2) + Math.cos(φ1) * Math.cos(φ2) * Math.cos(dλ);
  return Math.acos(Math.min(1, Math.max(-1, cos))) * DEG;
}

/** Point reached by travelling `distanceDeg` along `bearingDeg` from `origin`. */
export function destination(
  origin: LngLat,
  bearingDeg: number,
  distanceDeg: number,
): LngLat {
  const δ = distanceDeg * RAD;
  const θ = bearingDeg * RAD;
  const φ1 = origin.lat * RAD;
  const λ1 = origin.lon * RAD;

  const φ2 = Math.asin(
    Math.sin(φ1) * Math.cos(δ) + Math.cos(φ1) * Math.sin(δ) * Math.cos(θ),
  );
  const λ2 =
    λ1 +
    Math.atan2(
      Math.sin(θ) * Math.sin(δ) * Math.cos(φ1),
      Math.cos(δ) - Math.sin(φ1) * Math.sin(φ2),
    );

  return { lat: φ2 * DEG, lon: normalizeLon(λ2 * DEG) };
}

/**
 * True when a point sits on the hemisphere facing the camera. Past the limb the
 * sun is physically behind the earth, so the disc must not be drawn.
 */
export function isVisible(point: LngLat, viewCenter: LngLat, limbDeg = 88): boolean {
  return angularDistance(point, viewCenter) <= limbDeg;
}

/** `globeFraming` for an exact instant (the real clock, or a dock hour's instant). */
export function globeFramingAt(date: Date): [number, number] {
  const { lon: subsolarLon } = subsolarPoint(date);
  return [normalizeLon(subsolarLon - 72), 0];
}
