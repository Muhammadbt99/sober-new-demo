/**
 * Checks the driver check-in logic against hand-worked cases.
 *
 * Run with:  npx tsx scripts/verify-checkin.ts
 */
import type { Route, RouteSegment, SafeStop } from "../src/lib/contract";
import {
  EMPTY_CHECK_IN,
  activeGroups,
  assess,
  checkInSummary,
  dangerAt,
  elevatedStretches,
  restStops,
} from "../src/lib/checkin";

function segment(length_m: number, drowsy?: number, status = "ok"): RouteSegment {
  return {
    length_m,
    danger_types:
      drowsy === undefined
        ? undefined
        : {
            band: "weekday_late",
            groups: [
              { id: "drowsy", label: "Drowsy", status, rate_ratio: drowsy },
              { id: "impaired", label: "Impaired", status: "ok", rate_ratio: 1 },
            ],
          },
  } as unknown as RouteSegment;
}
const route = (segments: RouteSegment[]) => ({ segments }) as unknown as Route;
const stop = (name: string, type: string, distance_m: number) => ({ name, type, distance_m }) as SafeStop;

const high = route([segment(1000, 3.1), segment(500, 2.0), segment(800, 1.2)]);
const calm = route([segment(1000, 1.1)]);
const noData = route([segment(1000)]);
const tired = { ...EMPTY_CHECK_IN, awake: "17plus" as const };
const shift = { ...EMPTY_CHECK_IN, nightShift: true };
const drinking = { ...EMPTY_CHECK_IN, alcohol: true };
const found = elevatedStretches(high.segments, "drowsy");

const checks: [string, boolean][] = [
  ["nothing answered raises nothing", activeGroups(EMPTY_CHECK_IN).length === 0],
  ["under 12 h raises nothing", activeGroups({ ...EMPTY_CHECK_IN, awake: "under12" }).length === 0],
  ["12-16 h raises nothing", activeGroups({ ...EMPTY_CHECK_IN, awake: "12to16" }).length === 0],
  ["17 h raises drowsy", activeGroups(tired).join() === "drowsy"],
  ["a night shift raises drowsy", activeGroups(shift).join() === "drowsy"],
  ["alcohol raises impaired", activeGroups(drinking).join() === "impaired"],
  ["both raise both", activeGroups({ ...tired, alcohol: true }).length === 2],
  ["a closed header says not filled in", checkInSummary(EMPTY_CHECK_IN) === "not filled in"],
  ["an easy answer says so", checkInSummary({ ...EMPTY_CHECK_IN, awake: "under12" }) === "fine to drive"],
  ["answers are summarised", checkInSummary({ ...tired, alcohol: true }) === "alcohol, awake 17 h+"],
  ["high stretches are counted, 2x included", found.count === 2],
  ["their length adds up", Math.abs(found.km - 1.5) < 1e-9],
  ["the worst ratio is kept", found.worst === 3.1],
  ["a too-thin estimate does not count", elevatedStretches([segment(1000, 9, "too_thin")], "drowsy").count === 0],
  ["no worries means no notices", assess(EMPTY_CHECK_IN, high).length === 0],
  ["a calm hour raises no notice", assess({ ...EMPTY_CHECK_IN, awake: "12to16" }, high).length === 0],
  ["tired on a matching route warns", assess(tired, high)[0]?.tone === "warn"],
  ["the warning quotes the route", assess(tired, high)[0]?.detail.includes("3.1× a typical hour on 1.5 km") === true],
  ["tired on a calm route does not nag", assess(tired, calm)[0]?.tone === "info"],
  ["and says the route is fine", assess(tired, calm)[0]?.detail.includes("No stretch") === true],
  ["without data it says why", assess(tired, noData)[0]?.detail.includes("New York only") === true],
  ["a night shift is named", assess(shift, high)[0]?.title.includes("night shift") === true],
  ["alcohol always stops", assess(drinking, calm)[0]?.tone === "stop"],
  ["alcohol stops without data too", assess(drinking, noData)[0]?.tone === "stop"],
  ["alcohol stops with no route yet", assess(drinking, undefined)[0]?.tone === "stop"],
  ["alcohol comes first", assess({ ...tired, alcohol: true }, high)[0]?.tone === "stop"],
  ["no notice carries a percentage of personal risk", !JSON.stringify(assess({ ...tired, alcohol: true }, high)).includes("%")],
  ["the danger at a point", dangerAt(high.segments, 2300, 400, ["drowsy"])?.ratio === 3.1],
  ["the next stretch", dangerAt(high.segments, 2300, 1200, ["drowsy"])?.ratio === 2.0],
  ["a calm stretch is silent", dangerAt(high.segments, 2300, 2000, ["drowsy"]) === null],
  ["only flagged kinds count", dangerAt(high.segments, 2300, 400, ["impaired"]) === null],
  ["segments are scaled to the path", dangerAt(high.segments, 4600, 2500, ["drowsy"])?.ratio === 2.0],
  [
    "rest areas before car parks",
    restStops([stop("P", "parking", 100), stop("R", "rest_area", 900)])[0]?.name === "R",
  ],
  ["other spelling of rest area", restStops([stop("R", "rest area", 900)]).length === 1],
  ["nearest first", restStops([stop("A", "parking", 900), stop("B", "parking", 100)])[0]?.name === "B"],
  ["the same name is listed once", restStops([stop("Parking", "parking", 1), stop("Parking", "parking", 2)]).length === 1],
  ["hospitals are not rest stops", restStops([stop("H", "hospital", 10)]).length === 0],
  ["two at most", restStops([stop("A", "parking", 1), stop("B", "parking", 2), stop("C", "parking", 3)]).length === 2],
];

let failed = 0;
for (const [name, ok] of checks) {
  if (!ok) {
    failed += 1;
    console.log(`FAIL ${name}`);
  }
}
console.log(`${checks.length} checks, ${failed} failed`);
process.exit(failed ? 1 : 0);
