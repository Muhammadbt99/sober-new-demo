/**
 * Checks the arithmetic of a simulated drive against hand-worked cases.
 *
 * Run with:  npx tsx scripts/verify-drive.ts
 */
import type { GuidanceStep, RouteSegment } from "../src/lib/contract";
import {
  bearingBetween,
  buildPath,
  headingAt,
  nextStep,
  pointAt,
  riskNotice,
  riskZones,
  spokenDistance,
  turnShape,
  turnTowards,
} from "../src/lib/drive";

// North along a meridian, then east along the equator-ish. 0.01 degrees of
// latitude is about 1,112 m.
const path = buildPath([
  [0, 0],
  [0.01, 0],
  [0.01, 0.01],
]);
const leg = path.along[1]!;

function segment(length_m: number, risk_level: string, reasons?: string[]): RouteSegment {
  return { length_m, risk_level, reasons } as unknown as RouteSegment;
}

const zones = riskZones(
  [
    segment(100, "LOW"),
    segment(100, "HIGH", ["Busy junction"]),
    segment(100, "CRITICAL"),
    segment(100, "MEDIUM"),
    segment(100, "HIGH"),
  ],
  500,
);
const steps = [
  { offset_m: 0, message: "Leave", maneuver: "DEPART" },
  { offset_m: 300, message: "Turn right", maneuver: "TURN_RIGHT" },
  { offset_m: 900, message: "Arrive", maneuver: "ARRIVE" },
] as GuidanceStep[];

const near = (a: number, b: number, tolerance: number) => Math.abs(a - b) <= tolerance;
const checks: [string, boolean][] = [
  ["a leg of 0.01 degrees is about 1,112 m", near(leg, 1112, 2)],
  ["the path's length adds up", near(path.total, leg * 2, 1)],
  ["half way along the first leg", near(pointAt(path, leg / 2)[0], 0.005, 1e-6)],
  ["before the start clamps to it", pointAt(path, -50)[0] === 0],
  ["past the end clamps to it", near(pointAt(path, 1e9)[1], 0.01, 1e-9)],
  ["north is 0", near(bearingBetween([0, 0], [1, 0]), 0, 0.01)],
  ["east is 90", near(bearingBetween([0, 0], [0, 1]), 90, 0.01)],
  ["south is 180", near(bearingBetween([1, 0], [0, 0]), 180, 0.01)],
  ["west is 270", near(bearingBetween([0, 1], [0, 0]), 270, 0.01)],
  ["facing north on the first leg", near(headingAt(path, 100, 40), 0, 0.5)],
  ["facing east on the second leg", near(headingAt(path, leg + 100, 40), 90, 0.5)],
  ["the very end still has a heading", near(headingAt(path, path.total, 40), 90, 1)],
  ["turning the short way across north", near(turnTowards(350, 10, 0.5), 0, 1e-6)],
  ["and back", near(turnTowards(10, 350, 0.5), 0, 1e-6)],
  ["two touching stretches make one zone", zones.length === 2],
  ["a zone starts at its first stretch", near(zones[0]!.from_m, 100, 1e-6)],
  ["a zone ends at its last stretch", near(zones[0]!.to_m, 300, 1e-6)],
  ["a zone takes its worst level", zones[0]!.level === "CRITICAL"],
  ["a zone keeps its reason", zones[0]!.reason === "Busy junction"],
  ["stretches are scaled to the path", near(riskZones([segment(50, "HIGH")], 500)[0]!.to_m, 500, 1e-6)],
  ["a zone in range is ahead", riskNotice(zones, 50, 100)?.kind === "ahead"],
  ["its distance is right", near(riskNotice(zones, 50, 100)?.distance_m ?? -1, 50, 1e-6)],
  ["a zone out of range is not mentioned", riskNotice(zones, 50, 20) === null],
  ["inside a zone says so", riskNotice(zones, 150, 100)?.kind === "inside"],
  ["inside counts down to its end", near(riskNotice(zones, 150, 100)?.distance_m ?? -1, 150, 1e-6)],
  ["past every zone is silent", riskNotice(zones, 600, 100) === null],
  ["the next step skips the start", nextStep(steps, 0, 1)?.step.message === "Turn right"],
  ["the next step counts down", near(nextStep(steps, 100, 1)?.distance_m ?? -1, 200, 1e-6)],
  ["a step just reached is passed", nextStep(steps, 298, 1)?.step.message === "Arrive"],
  ["offsets stretch to the path", near(nextStep(steps, 0, 2)?.distance_m ?? -1, 600, 1e-6)],
  ["no steps left is nothing", nextStep(steps, 5000, 1) === null],
  ["short distances round to 10 m", spokenDistance(143) === "140 m"],
  ["longer ones to 50 m", spokenDistance(430) === "450 m"],
  ["kilometres", spokenDistance(1234) === "1.2 km"],
  ["never zero", spokenDistance(2) === "10 m"],
  ["left", turnShape("TURN_LEFT") === "left"],
  ["keep right", turnShape("KEEP_RIGHT") === "right"],
  ["u-turn", turnShape("MAKE_UTURN") === "uturn"],
  ["roundabout beats its direction", turnShape("ROUNDABOUT_LEFT") === "roundabout"],
  ["arrive", turnShape("ARRIVE_RIGHT") === "arrive"],
  ["anything else is straight", turnShape("STRAIGHT") === "straight"],
];

let failed = 0;
for (const [name, ok] of checks) {
  if (!ok) failed += 1;
  if (!ok) console.log(`FAIL ${name}`);
}
console.log(`${checks.length} checks, ${failed} failed`);
process.exit(failed ? 1 : 0);
