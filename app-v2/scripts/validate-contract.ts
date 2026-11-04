/**
 * Validates every captured live API response against the zod contract.
 *
 * Run with:  npx tsx scripts/validate-contract.ts
 * (or via `npm run validate:contract`)
 *
 * The fixtures in `src/lib/contract-fixtures/` are REAL responses captured from
 * the Flask API with `API/MAPS/scripts/capture_contract.py`. They are the
 * ground truth. Anything this script reports is a contract bug, not a fixture
 * problem.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  AlertsResponse,
  DeparturesResponse,
  ErrorResponse,
  GuidanceResponse,
  NightPlanResponse,
  HealthResponse,
  HeatmapResponse,
  HotspotsResponse,
  LocationAlertsResponse,
  NearestSafeResponse,
  RiskCompareResponse,
  RiskResponse,
  RiskTrendResponse,
  SafeRouteResponse,
  StatsResponse,
  SurfaceResponse,
  DangerTypesCard,
  ModelCard,
  GlobalModelCard,
  GlobalSurfaceResponse,
} from "../src/lib/contract";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const FIXTURES = join(HERE, "..", "src", "lib", "contract-fixtures");

const SCHEMAS: Record<
  string,
  { schema: { safeParse: (v: unknown) => { success: boolean; error?: unknown } }; label: string }
> = {
  health: { schema: HealthResponse, label: "HealthResponse" },
  health_deep: { schema: HealthResponse, label: "HealthResponse" },
  risk_times_square: { schema: RiskResponse, label: "RiskResponse" },
  risk_out_of_area: { schema: RiskResponse, label: "RiskResponse" },
  risk_context: { schema: RiskResponse, label: "RiskResponse" },
  heatmap: { schema: HeatmapResponse, label: "HeatmapResponse" },
  risk_trend: { schema: RiskTrendResponse, label: "RiskTrendResponse" },
  risk_compare: { schema: RiskCompareResponse, label: "RiskCompareResponse" },
  safe_route: { schema: SafeRouteResponse, label: "SafeRouteResponse" },
  safe_route_single: { schema: SafeRouteResponse, label: "SafeRouteResponse" },
  departures: { schema: DeparturesResponse, label: "DeparturesResponse" },
  guidance: { schema: GuidanceResponse, label: "GuidanceResponse" },
  night_plan: { schema: NightPlanResponse, label: "NightPlanResponse" },
  hotspots: { schema: HotspotsResponse, label: "HotspotsResponse" },
  nearest_safe: { schema: NearestSafeResponse, label: "NearestSafeResponse" },
  alerts: { schema: AlertsResponse, label: "AlertsResponse" },
  alerts_check_location: {
    schema: LocationAlertsResponse,
    label: "LocationAlertsResponse",
  },
  stats: { schema: StatsResponse, label: "StatsResponse" },
  surface: { schema: SurfaceResponse, label: "SurfaceResponse" },
  danger_types: { schema: DangerTypesCard, label: "DangerTypesCard" },
  model_card: { schema: ModelCard, label: "ModelCard" },
  global_model: { schema: GlobalModelCard, label: "GlobalModelCard" },
  surface_global: { schema: GlobalSurfaceResponse, label: "GlobalSurfaceResponse" },
};

let failures = 0;
let checked = 0;

for (const name of Object.keys(SCHEMAS).sort()) {
  const file = join(FIXTURES, `${name}.json`);
  let body: unknown;
  try {
    body = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    console.log(`SKIP  ${name} (no fixture)`);
    continue;
  }
  checked += 1;
  const { schema, label } = SCHEMAS[name]!;
  const result = schema.safeParse(body);
  if (result.success) {
    console.log(`  ok  ${name.padEnd(24)} ${label}`);
  } else {
    failures += 1;
    console.log(`FAIL  ${name.padEnd(24)} ${label}`);
    const issue =
      (result.error as { issues?: Array<{ path: PropertyKey[]; message: string }> })
        .issues ?? [];
    for (const entry of issue) {
      console.log(`        ${entry.path.map(String).join(".") || "(root)"}: ${entry.message}`);
    }
  }
}

// Every error path must stay JSON and carry the contract version.
for (const name of readdirSync(FIXTURES).filter((file) => file.startsWith("err_"))) {
  const body = JSON.parse(readFileSync(join(FIXTURES, name), "utf8")) as unknown;
  checked += 1;
  const parsed = ErrorResponse.safeParse(body);
  if (parsed.success && parsed.data.schema_version === "1.0.0") {
    console.log(`  ok  ${name.replace(".json", "").padEnd(24)} error envelope`);
  } else {
    failures += 1;
    console.log(
      `FAIL  ${name.replace(".json", "").padEnd(24)} error envelope missing schema_version`,
    );
  }
}

// Guard against stale fixtures from an earlier contract.
const known = new Set([
  ...Object.keys(SCHEMAS).map((name) => `${name}.json`),
  ...readdirSync(FIXTURES).filter((file) => file.startsWith("err_")),
]);
for (const file of readdirSync(FIXTURES)) {
  if (!known.has(file)) {
    failures += 1;
    console.log(`FAIL  ${file.padEnd(24)} stale fixture with no schema mapping`);
  }
}

// A very long trip drops the notes on calm stretches. What is left must still
// satisfy the contract, or the whole trip is refused in the browser.
{
  const safe = JSON.parse(readFileSync(join(FIXTURES, "safe_route.json"), "utf8"));
  for (const route of safe.routes) {
    for (const segment of route.segments) {
      for (const field of ["reasons", "action", "model_points", "nightlife_points", "danger_types"]) {
        delete segment[field];
      }
    }
  }
  const parsed = SafeRouteResponse.safeParse(safe);
  checked += 1;
  if (!parsed.success) failures += 1;
  console.log(`${parsed.success ? "  ok" : "FAIL"}  slimmed long trip`);
}

console.log(`\n${checked} fixtures checked, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
