import {
  subsolarPoint,
  angularDistance,
  globeFraming,
  lonDelta,
  scenarioInstant,
  scenarioSubsolarPoint,
} from "../src/map/sun";

const cases: Array<[string, string, string]> = [
  ["2026-06-21T12:00:00Z", "June solstice: dec ~ +23.4, lon ~ 0", "expect dec +23.4"],
  ["2026-03-20T12:00:00Z", "March equinox: dec ~ 0, lon ~ 0", "expect dec ~0"],
  ["2026-12-21T12:00:00Z", "December solstice: dec ~ -23.4", "expect dec -23.4"],
  ["2026-11-01T12:00:00Z", "Nov 1: dec ~ -15", "expect dec -15"],
  ["2026-06-21T00:00:00Z", "June solstice midnight: lon ~ 180", "expect lon ~180"],
];

let fail = 0;
for (const [iso, label] of cases) {
  const p = subsolarPoint(new Date(iso));
  console.log(
    `${iso}  lat=${p.lat.toFixed(2).padStart(7)}  lon=${p.lon.toFixed(2).padStart(8)}   ${label}`,
  );
}

// Sanity bounds.
const jun = subsolarPoint(new Date("2026-06-21T12:00:00Z"));
if (Math.abs(jun.lat - 23.44) > 0.6) { console.log("FAIL: June declination out of range"); fail++; }
if (Math.abs(jun.lon) > 4) { console.log("FAIL: June noon longitude not near 0"); fail++; }

const dec = subsolarPoint(new Date("2026-12-21T12:00:00Z"));
if (Math.abs(dec.lat + 23.44) > 0.6) { console.log("FAIL: December declination out of range"); fail++; }

const equinox = subsolarPoint(new Date("2026-03-20T12:00:00Z"));
if (Math.abs(equinox.lat) > 1.2) { console.log("FAIL: equinox declination not near 0"); fail++; }

// Longitude must advance 15 deg/hour.
const a = subsolarPoint(new Date("2026-05-10T00:00:00Z"));
const b = subsolarPoint(new Date("2026-05-10T06:00:00Z"));
const drift = angularDistance({ lat: 0, lon: a.lon }, { lat: 0, lon: b.lon });
console.log(`6h longitude drift = ${drift.toFixed(2)} deg (expect ~90)`);
if (Math.abs(drift - 90) > 1.5) { console.log("FAIL: longitude drift not 15 deg/hour"); fail++; }

// ---------------------------------------------------------------------------
// Scenario clock + globe framing.
// The dock hour is New York wall-clock time, so 12:00 has to put the sun over
// New York and 23:00 has to put it most of a hemisphere away.
// ---------------------------------------------------------------------------
const base = new Date("2026-11-01T15:00:00Z");
for (const hour of [3, 6, 12, 18, 23]) {
  const sun = scenarioSubsolarPoint(hour, base);
  const nyc = { lat: 40.758, lon: -73.985 };
  const gap = angularDistance(sun, nyc);
  console.log(
    `NY ${String(hour).padStart(2, "0")}:00 -> sun lat=${sun.lat.toFixed(1).padStart(6)} lon=${sun.lon.toFixed(1).padStart(7)}  ${gap.toFixed(1).padStart(5)} deg from NYC`,
  );
}

// At local noon the sun must be on the New York meridian (its zenith is 55
// degrees above the horizon in November, so compare longitude, not distance).
const noonSun = scenarioSubsolarPoint(12, base);
const noonLonGap = Math.abs(lonDelta(noonSun.lon, -73.985));
console.log(`12:00 solar meridian gap = ${noonLonGap.toFixed(2)} deg`);
if (noonLonGap > 12) { console.log("FAIL: 12:00 does not put the sun over New York"); fail++; }

const late = angularDistance(scenarioSubsolarPoint(23, base), { lat: 40.758, lon: -73.985 });
if (late < 90) { console.log("FAIL: 23:00 is not night over New York"); fail++; }

// The globe framing follows the sun, so the disc must always come out mostly
// lit (with a night crescent) at every hour of the day.
for (const hour of [0, 3, 6, 9, 12, 15, 18, 21, 23]) {
  const [lon, lat] = globeFraming(hour, base);
  const view = { lat, lon };
  const sun = scenarioSubsolarPoint(hour, base);
  const gap = angularDistance(view, sun);
  const lit = (1 + Math.cos((gap * Math.PI) / 180)) / 2;
  const ok = gap > 55 && gap < 85;
  console.log(
    `frame ${String(hour).padStart(2, "0")}:00 -> centre ${lon.toFixed(1).padStart(7)},${lat}  sun ${gap.toFixed(1).padStart(5)} deg away  disc ${(lit * 100).toFixed(0).padStart(3)}% lit  ${ok ? "ok" : "BAD EXPOSURE"}`,
  );
  if (!ok) { console.log(`FAIL: framing at ${hour}:00 is not a well-lit disc`); fail++; }
}

// ---------------------------------------------------------------------------
// Clock chain: dock hour -> New York wall clock -> UTC instant.
// The demo runs on the day the daylight-saving switch happens in the US, so
// this is the case most likely to be wrong.
// ---------------------------------------------------------------------------
const zoneParts = (instant: Date) => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).formatToParts(instant);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return {
    stamp: `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}`,
    hour: Number(pick("hour")) % 24,
    minute: Number(pick("minute")),
    zone: pick("timeZoneName"),
  };
};

console.log("\ndock hour -> UTC instant -> New York wall clock");
let clockFail = 0;
for (let hour = 0; hour < 24; hour++) {
  const instant = scenarioInstant(hour, base);
  const back = zoneParts(instant);
  const ok = back.hour === hour && back.minute === 0;
  if (!ok) clockFail++;
  const show = hour <= 5 || hour >= 22;
  if (show || !ok) {
    console.log(
      `  ${String(hour).padStart(2, "0")}:00 -> ${instant.toISOString().slice(0, 16)}Z -> ${back.stamp} ${back.zone}${ok ? "" : "  MISMATCH"}`,
    );
  }
}
if (clockFail > 0) { console.log(`FAIL: ${clockFail} dock hours do not round-trip`); fail++; }

// The same round-trip on an ordinary day (the fix must not double-apply the
// offset) and across the spring-forward gap, where 02:00 does not exist and
// exactly one hour is allowed to resolve to a neighbouring wall time.
for (const [label, iso, allowed] of [
  ["ordinary day", "2026-11-02T15:00:00Z", 0],
  ["spring forward", "2026-03-08T15:00:00Z", 1],
] as const) {
  let bad = 0;
  for (let hour = 0; hour < 24; hour++) {
    if (zoneParts(scenarioInstant(hour, new Date(iso))).hour !== hour) bad++;
  }
  console.log(`  ${label}: ${24 - bad}/24 hours round-trip${bad === allowed ? "" : "  MISMATCH"}`);
  if (bad !== allowed) { console.log(`FAIL: ${label} clock chain is off`); fail++; }
}

// ---------------------------------------------------------------------------
// Sun rise and set over New York, derived from the same subsolar function the
// renderer uses, then compared with the published almanac.
//
// Almanac read from sunrise-sunset.org for 40.758,-73.985 on 2026-11-01
// (https://api.sunrise-sunset.org/json?lat=40.758&lng=-73.985&date=2026-11-01&formatted=0):
//   sunrise 11:26:35Z, sunset 21:52:25Z, solar noon 16:39:30Z.
// Keep the comparison in UTC: re-deriving a local time is exactly the bug the
// clock chain above is testing for.
// ---------------------------------------------------------------------------
const NYC = { lat: 40.758, lon: -73.985 };
const elevation = (instant: Date) =>
  90 - angularDistance(subsolarPoint(instant), NYC);

const ALMANAC = {
  sunrise: Date.parse("2026-11-01T11:26:35Z"),
  sunset: Date.parse("2026-11-01T21:52:25Z"),
};
const TOLERANCE_MIN = 3;

const HORIZON = -0.833; // standard refraction + solar semidiameter
let sunrise: number | null = null;
let sunset: number | null = null;
let noonAltitude = 0;
for (let minute = 0; minute < 24 * 60; minute++) {
  const t0 = new Date(Date.UTC(2026, 10, 1, 0, minute, 0));
  const t1 = new Date(Date.UTC(2026, 10, 1, 0, minute + 1, 0));
  const e0 = elevation(t0) - HORIZON;
  const e1 = elevation(t1) - HORIZON;
  if (e0 < 0 && e1 >= 0 && sunrise === null) sunrise = t0.getTime();
  if (e0 >= 0 && e1 < 0 && sunset === null) sunset = t0.getTime();
  noonAltitude = Math.max(noonAltitude, elevation(t0));
}
const iso = (ms: number | null) =>
  ms === null ? "none" : new Date(ms).toISOString().slice(0, 16) + "Z";
console.log(
  `\nNew York 2026-11-01: sunrise ${iso(sunrise)}, sunset ${iso(sunset)}, peak altitude ${noonAltitude.toFixed(1)} deg`,
);

const minutesOff = (actual: number | null, expected: number) =>
  actual === null ? Infinity : Math.abs(actual - expected) / 60000;

const sunriseDelta = minutesOff(sunrise, ALMANAC.sunrise);
console.log(`  sunrise delta vs almanac = ${sunriseDelta.toFixed(1)} min`);
if (sunriseDelta > TOLERANCE_MIN) {
  console.log(`FAIL: sunrise is off the almanac by more than ${TOLERANCE_MIN} minutes`);
  fail++;
}

const sunsetDelta = minutesOff(sunset, ALMANAC.sunset);
console.log(`  sunset  delta vs almanac = ${sunsetDelta.toFixed(1)} min`);
if (sunsetDelta > TOLERANCE_MIN) {
  console.log(`FAIL: sunset is off the almanac by more than ${TOLERANCE_MIN} minutes`);
  fail++;
}

// Peak altitude is a physical consistency check: at solar noon the sun sits
// 90 - |latitude - declination| above the horizon, about 35 deg here.
if (Math.abs(noonAltitude - 35.0) > 0.7) {
  console.log("FAIL: peak solar altitude is outside the plausible November band");
  fail++;
}

console.log(fail === 0 ? "\nALL SOLAR CHECKS PASSED" : `\n${fail} CHECKS FAILED`);
