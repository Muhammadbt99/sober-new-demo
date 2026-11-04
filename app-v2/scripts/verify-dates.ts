/**
 * Checks the weekday-to-real-date helpers against fixed clocks.
 *
 * Run with:  npx tsx scripts/verify-dates.ts
 */
import { formatDateFor, nextDateFor, upcomingDays } from "../src/lib/risk";

// Wednesday 4 November 2026, 14:00 local.
const NOW = new Date(2026, 10, 4, 14);
const WED = 2;
const THU = 3;
const SAT = 5;
const TUE = 1;

const checks: [string, string, string][] = [
  ["later today", formatDateFor(WED, 18, NOW), "Today, Wed 4 Nov"],
  ["this hour counts as today", formatDateFor(WED, 14, NOW), "Today, Wed 4 Nov"],
  ["today's hour already gone", formatDateFor(WED, 9, NOW), "Wed 11 Nov"],
  ["tomorrow", formatDateFor(THU, 8, NOW), "Tomorrow, Thu 5 Nov"],
  ["later this week", formatDateFor(SAT, 8, NOW), "Sat 7 Nov"],
  ["the far end of the week", formatDateFor(TUE, 8, NOW), "Tue 10 Nov"],
  ["across a month end", formatDateFor(1, 8, new Date(2026, 10, 30, 14)), "Tomorrow, Tue 1 Dec"],
  ["across a year end", formatDateFor(4, 8, new Date(2026, 11, 31, 14)), "Tomorrow, Fri 1 Jan"],
  ["date is at the hour", String(nextDateFor(SAT, 8, NOW).getHours()), "8"],
  ["picker starts today", String(upcomingDays(NOW)[0]?.date), "4"],
  ["picker's today is flagged", String(upcomingDays(NOW)[0]?.isToday), "true"],
  ["picker has a week", String(new Set(upcomingDays(NOW).map((item) => item.day)).size), "7"],
];

let failed = 0;
for (const [name, actual, expected] of checks) {
  const ok = actual === expected;
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${actual}${ok ? "" : ` (wanted ${expected})`}`);
}
console.log(`\n${checks.length} checks, ${failed} failed`);
process.exit(failed ? 1 : 0);
