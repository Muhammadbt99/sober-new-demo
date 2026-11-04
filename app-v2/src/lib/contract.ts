import { z } from "zod";

/**
 * The API contract. Every response enters the app through one of these
 * schemas; a parse failure is a visible contract error, never `undefined`.
 */

export const RiskLevel = z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]);
export type RiskLevel = z.infer<typeof RiskLevel>;

/** `context`: outside NYC, an estimate from road, time, weather and country data. */
export const Coverage = z.enum(["model", "mixed", "rule_based_only", "context"]);
export type Coverage = z.infer<typeof Coverage>;

export const SegmentBucket = z.enum([
  "low",
  "medium",
  "high",
  "extreme",
  "uncovered",
]);
export type SegmentBucket = z.infer<typeof SegmentBucket>;

export const DataQuality = z
  .object({
    model: z.string().optional(),
    weather: z.string().optional(),
    bars: z.string().optional(),
    coverage_ratio: z.number().optional(),
    temperature: z.number().nullable().optional(),
    rain: z.number().nullable().optional(),
    wind_speed: z.number().nullable().optional(),
  })
  .passthrough();
export type DataQuality = z.infer<typeof DataQuality>;

const LocalTime = z.object({
  hour: z.number().int().min(0).max(23),
  day_of_week: z.number().int().min(0).max(6),
  is_weekend: z.boolean(),
});

const Coordinate = z.tuple([z.number(), z.number()]);

/**
 * A human name for a scored point: the 2020 neighbourhood inside the NYC model,
 * the road and country outside it, or the nearest neighbourhood for a river or
 * park beside the city. Null when nothing nearby has a name.
 */
export const PlaceName = z
  .object({
    name: z.string(),
    area: z.string().nullable().optional(),
    source: z.string().optional(),
  })
  .nullable()
  .optional();
export type PlaceName = z.infer<typeof PlaceName>;

/**
 * One danger group (impaired, drowsy, speeding, failure to yield, distracted)
 * at a place and band of the week. Shares are of injury crashes whose recorded
 * factors include the group; `rate_ratio` compares the expected rate of that
 * kind of crash here and now with a typical NYC cell-hour.
 */
export const DangerGroup = z.object({
  id: z.string(),
  label: z.string(),
  short: z.string().optional(),
  status: z.enum(["ok", "too_thin", "not_validated"]),
  share: z.number().optional(),
  share_low: z.number().optional(),
  share_high: z.number().optional(),
  city_share: z.number().optional(),
  city_band_share: z.number().optional(),
  share_ratio: z.number().optional(),
  /** Share here against the city's in the same band of the week. */
  band_share_ratio: z.number().optional(),
  rate_ratio: z.number().optional(),
  evidence: z.enum(["neighbourhood", "borough", "citywide"]).optional(),
  evidence_crashes: z.number().optional(),
  reason: z.string().optional(),
});
export type DangerGroup = z.infer<typeof DangerGroup>;

export const DangerProfile = z.object({
  version: z.string(),
  band: z.object({ id: z.string(), label: z.string() }),
  area: z.object({
    neighbourhood: z.string().nullable(),
    borough: z.string().nullable(),
  }),
  basis: z.string(),
  note: z.string(),
  groups: z.array(DangerGroup),
});
export type DangerProfile = z.infer<typeof DangerProfile>;

/** The per-segment form: ratios and status only. */
export const DangerCompact = z.object({ band: z.string(), groups: z.array(DangerGroup) });
export type DangerCompact = z.infer<typeof DangerCompact>;

const DangerTest = z
  .object({
    log_loss: z.number(),
    top_decile_lift: z.number().nullable(),
    events: z.number(),
    crashes: z.number(),
  })
  .passthrough();

/** `/danger_types`: definitions, gate and held-out metrics, straight from the artifact. */
export const DangerTypesCard = z
  .object({
    schema_version: z.string(),
    version: z.string(),
    created_at: z.string(),
    target: z.string(),
    inputs: z.array(z.string()),
    data: z
      .object({
        window: z.array(z.string()),
        injury_crashes: z.number(),
        split: z.object({ train: z.number(), validation: z.number(), test: z.number() }).passthrough(),
      })
      .passthrough(),
    bands: z.record(z.string(), z.string()),
    definitions: z.record(z.string(), z.array(z.string())),
    city_share: z.record(z.string(), z.number()),
    groups: z.record(
      z.string(),
      z
        .object({
          status: z.enum(["validated", "not_validated"]),
          level: z.enum(["time", "borough", "neighbourhood"]),
          gate: z.record(z.string(), z.boolean()),
          test: z.object({
            city: DangerTest,
            time: DangerTest,
            borough: DangerTest,
            neighbourhood: DangerTest,
          }),
        })
        .passthrough(),
    ),
  })
  .passthrough();
export type DangerTypesCard = z.infer<typeof DangerTypesCard>;

const ModelSplit = z
  .object({
    poisson_deviance: z.number(),
    baseline_deviance: z.number(),
    top_decile_lift: z.number(),
    baseline_top_decile_lift: z.number().nullable(),
    monotonic_deciles: z.boolean(),
    deciles: z.array(
      z.object({
        decile: z.number(),
        predicted_rate: z.number(),
        observed_rate: z.number(),
        lift: z.number(),
      }),
    ),
  })
  .passthrough();

/** `/model_card`: the crash model's validation numbers, read from its metadata. */
export const ModelCard = z
  .object({
    schema_version: z.string(),
    version: z.string(),
    created_at: z.string(),
    target: z.string(),
    gate: z.record(z.string(), z.boolean()),
    training_window: z.object({
      start: z.string(),
      end: z.string(),
      crashes: z.number(),
    }),
    holdout_borough: z.string(),
    temporal_test: ModelSplit,
    holdout: ModelSplit,
    cells: z.number(),
    prior_rate: z.number(),
  })
  .passthrough();
export type ModelCard = z.infer<typeof ModelCard>;

export const RiskBreakdown = z.object({
  model: z.number(),
  nightlife: z.number(),
  total: z.number(),
  model_max: z.number(),
  nightlife_max: z.number(),
  percentile: z.number().nullable(),
  expected_rate: z.number().nullable(),
  expected_rate_per_km2_hour: z.number().nullable(),
});
export type RiskBreakdown = z.infer<typeof RiskBreakdown>;

export const RiskResponse = z.object({
  schema_version: z.string(),
  risk_score: z.number(),
  risk_level: RiskLevel,
  reasons: z.array(z.string()),
  action: z.string(),
  risk_breakdown: RiskBreakdown,
  expected_rate: z.number().nullable(),
  expected_rate_per_km2_hour: z.number().nullable(),
  percentile: z.number().nullable(),
  model_points: z.number(),
  nightlife_points: z.number(),
  coverage: Coverage,
  coverage_reason: z.string().nullable().optional(),
  /** Present when coverage is "context": the factors behind the estimate. */
  context: z
    .object({
      country: z.string().nullable().optional(),
      country_name: z.string().nullable().optional(),
      deaths_per_100k: z.number().nullable().optional(),
      road_class: z.string().nullable().optional(),
      road_name: z.string().nullable().optional(),
      index: z.number().optional(),
      /** "global_model" where the global crash model scored the point (the UAE). */
      method: z.string().optional(),
      region: z.string().optional(),
      region_name: z.string().optional(),
      /** The global model's rate; 1.0 = a training city's average cell-hour. */
      relative_rate: z.number().optional(),
      /** This hour against the place's average hour of the week. */
      time_ratio: z.number().optional(),
      trained_on: z.string().optional(),
    })
    .passthrough()
    .nullable()
    .optional(),
  model_version: z.string().nullable(),
  cell: z.string().nullable(),
  place: PlaceName,
  /** What kind of injury crash is over-represented here now (model cells only). */
  danger_types: DangerProfile.nullable().optional(),
  local_time: LocalTime,
  data_quality: DataQuality,
  warnings: z.array(z.string()),
  /** The model's inputs at this point; `bars` is the venue count within 1 km. */
  features: z
    .object({ bars: z.number().optional() })
    .passthrough()
    .optional(),
});
export type RiskResponse = z.infer<typeof RiskResponse>;

export const HeatmapCell = z.object({
  lat: z.number(),
  lon: z.number(),
  cell: z.string().nullable(),
  expected_rate: z.number().nullable(),
  expected_rate_per_km2_hour: z.number().nullable(),
  percentile: z.number().nullable(),
  model_points: z.number(),
  nightlife_points: z.number(),
  risk_score: z.number().nullable(),
  risk_level: RiskLevel,
  coverage: Coverage,
  data_quality: DataQuality,
  error: z.boolean(),
});
export type HeatmapCell = z.infer<typeof HeatmapCell>;

/** One cell of the whole-city model surface (`/surface`). */
export const SurfaceCell = z.object({
  cell: z.string(),
  lat: z.number(),
  lon: z.number(),
  name: z.string().nullable(),
  borough: z.string().nullable(),
  expected_rate: z.number(),
  percentile: z.number(),
  model_points: z.number(),
  nightlife_points: z.number(),
  bars: z.number(),
  risk_score: z.number(),
  risk_level: RiskLevel,
  coverage: Coverage,
  /** With `?danger=`: this cell's expected rate of that kind of crash vs a typical cell-hour. */
  danger_rate_ratio: z.number().nullable().optional(),
  danger_status: z.string().optional(),
});
export type SurfaceCell = z.infer<typeof SurfaceCell>;

export const SurfaceResponse = z.object({
  schema_version: z.string(),
  danger_layer: z
    .object({ group: z.string(), status: z.string() })
    .nullable()
    .optional(),
  hour: z.number(),
  day_of_week: z.number(),
  is_weekend: z.boolean(),
  coverage: Coverage,
  model_version: z.string().nullable(),
  cell_count: z.number(),
  data_quality: DataQuality,
  warnings: z.array(z.string()),
  cells: z.array(SurfaceCell),
});
export type SurfaceResponse = z.infer<typeof SurfaceResponse>;

/** `/surface/global`: the global crash model's cells around a point (the UAE). */
export const GlobalSurfaceResponse = SurfaceResponse.extend({
  region: z.string(),
  region_name: z.string(),
  centre: z.object({ lat: z.number(), lon: z.number() }),
  radius_km: z.number(),
});
export type GlobalSurfaceResponse = z.infer<typeof GlobalSurfaceResponse>;

const GlobalHeldOut = z
  .object({
    deviance: z.number(),
    top_decile_lift: z.number(),
    cell_spearman: z.number().nullable(),
    top10_cells_capture: z.number(),
    crashes: z.number(),
    cells: z.number(),
  })
  .passthrough();

/** `/global_model`: training cities, protocol and leave-one-city-out results. */
export const GlobalModelCard = z
  .object({
    schema_version: z.string(),
    version: z.string(),
    created_at: z.string(),
    target: z.string(),
    feature_set: z.string(),
    best_on_training_cities: z.string().nullable().optional(),
    static_features: z.array(z.string()),
    mapping_check: z
      .object({
        region: z.string().nullable().optional(),
        rule: z.string().nullable().optional(),
        features: z.record(
          z.string(),
          z.object({ share_of_least_mapped_city: z.number().nullable(), usable: z.boolean() }),
        ),
      })
      .optional(),
    training: z.record(
      z.string(),
      z.object({ name: z.string(), country: z.string(), cells: z.number(), crashes: z.number() }).passthrough(),
    ),
    protocol: z.object({ selection: z.string(), gate: z.string(), baseline: z.string() }).passthrough(),
    gate: z.object({
      summary: z.record(z.string(), z.number()),
      checks: z.record(z.string(), z.boolean()),
      held_out: z.record(z.string(), GlobalHeldOut),
      baseline: z.record(z.string(), GlobalHeldOut),
      time_only: z.record(z.string(), GlobalHeldOut).optional(),
      best: z.record(z.string(), GlobalHeldOut).nullable().optional(),
      best_summary: z.record(z.string(), z.number()).nullable().optional(),
    }),
    dubai_validation: GlobalHeldOut.extend({
      baseline_top_decile_lift: z.number().optional(),
      retrain: z
        .object({
          adopted: z.boolean(),
          unseen_districts: z.object({ after: GlobalHeldOut }),
          untouched_2026: z.object({ after: GlobalHeldOut }),
        })
        .passthrough()
        .nullable()
        .optional(),
    })
      .nullable()
      .optional(),
    regions: z.record(
      z.string(),
      z.object({ name: z.string(), cells: z.number(), reference_cells: z.number().optional() }),
    ),
  })
  .passthrough();
export type GlobalModelCard = z.infer<typeof GlobalModelCard>;

export const HeatmapResponse = z.object({
  schema_version: z.string(),
  grid_size: z.number(),
  total_points: z.number(),
  model_cells: z.number(),
  failed_points: z.number(),
  coverage: Coverage,
  model_version: z.string().nullable(),
  bounds: z.object({
    lat1: z.number(),
    lon1: z.number(),
    lat2: z.number(),
    lon2: z.number(),
  }),
  heatmap: z.array(HeatmapCell),
  warnings: z.array(z.string()),
});
export type HeatmapResponse = z.infer<typeof HeatmapResponse>;

const TrendEntry = z.object({
  hour: z.number(),
  hour_label: z.string(),
  expected_rate: z.number().nullable(),
  expected_rate_per_km2_hour: z.number().nullable(),
  percentile: z.number().nullable(),
  model_points: z.number(),
  nightlife_points: z.number(),
  risk_score: z.number(),
  risk_level: RiskLevel,
  coverage: Coverage,
  reasons: z.array(z.string()),
  action: z.string(),
});
export type TrendEntry = z.infer<typeof TrendEntry>;

export const RiskTrendResponse = z.object({
  schema_version: z.string(),
  location: z.object({ lat: z.number(), lon: z.number() }),
  day: z.string(),
  is_weekend: z.boolean(),
  coverage: Coverage,
  model_version: z.string().nullable(),
  data_quality: DataQuality,
  warnings: z.array(z.string()),
  trend: z.array(TrendEntry),
  peak_risk: z.object({
    hour: z.number(),
    hour_label: z.string(),
    risk_score: z.number(),
    risk_level: RiskLevel,
    percentile: z.number().nullable(),
  }),
  safest_time: z.object({
    hour: z.number(),
    hour_label: z.string(),
    risk_score: z.number(),
    risk_level: RiskLevel,
    percentile: z.number().nullable(),
  }),
  average_risk: z.number(),
});
export type RiskTrendResponse = z.infer<typeof RiskTrendResponse>;

const CompareLocation = z.object({
  lat: z.number(),
  lon: z.number(),
  risk_score: z.number(),
  risk_level: RiskLevel,
  reasons: z.array(z.string()),
  action: z.string(),
  risk_breakdown: RiskBreakdown,
  expected_rate: z.number().nullable(),
  expected_rate_per_km2_hour: z.number().nullable(),
  percentile: z.number().nullable(),
  model_points: z.number(),
  nightlife_points: z.number(),
  model_version: z.string().nullable(),
  cell: z.string().nullable(),
  place: PlaceName,
  coverage: Coverage,
  data_quality: DataQuality,
  warnings: z.array(z.string()),
});
export type CompareLocation = z.infer<typeof CompareLocation>;

export const RiskCompareResponse = z.object({
  schema_version: z.string(),
  location_a: CompareLocation,
  location_b: CompareLocation,
  safer_location: z.enum(["A", "B", "EQUAL"]),
  risk_difference: z.number(),
  recommendation: z.string(),
});
export type RiskCompareResponse = z.infer<typeof RiskCompareResponse>;

export const RouteSegment = z.object({
  cell: z.string().nullable(),
  /** The street this stretch is on, from the router's steps (when known). */
  street: z.string().nullable().optional(),
  /** Where the stretch starts and ends, in km from the trip's start. */
  from_km: z.number().optional(),
  to_km: z.number().optional(),
  start: Coordinate,
  end: Coordinate,
  midpoint: Coordinate,
  length_m: z.number(),
  expected_rate: z.number().nullable(),
  expected_rate_per_km2_hour: z.number().nullable(),
  expected_crashes: z.number(),
  percentile: z.number().nullable(),
  bucket: SegmentBucket,
  risk_score: z.number().nullable(),
  risk_level: RiskLevel,
  coverage: Coverage,
  reasons: z.array(z.string()).optional(),
  action: z.string().optional(),
  model_points: z.number().optional(),
  nightlife_points: z.number().optional(),
  danger_types: DangerCompact.optional(),
  /** Which model scored a stretch outside NYC ("global_model" in the UAE). */
  method: z.string().optional(),
});
export type RouteSegment = z.infer<typeof RouteSegment>;

export const RouteWaypoint = z.object({
  lat: z.number(),
  lon: z.number(),
  cell: z.string().nullable(),
  risk_score: z.number().nullable(),
  risk_level: RiskLevel,
  expected_rate: z.number().nullable(),
  percentile: z.number().nullable(),
  bucket: SegmentBucket,
});
export type RouteWaypoint = z.infer<typeof RouteWaypoint>;

export const Route = z.object({
  /** Minutes lost to traffic against empty roads. */
  traffic_min_added: z.number().optional(),
  /** A road TomTom chose for the traffic, not one of the routers'. */
  live_reroute: z.boolean().optional(),
  rank: z.number(),
  route_type: z.string(),
  synthetic: z.boolean(),
  is_safest: z.boolean(),
  is_fastest: z.boolean(),
  is_balanced: z.boolean(),
  expected_crashes_total: z.number(),
  expected_crashes_per_10km: z.number(),
  average_risk: z.number(),
  risk_level: RiskLevel,
  average_percentile: z.number().nullable(),
  max_percentile: z.number().nullable(),
  max_waypoint_risk: z.number(),
  distance_km: z.number(),
  duration_min: z.number().nullable(),
  geometry: z.array(Coordinate),
  segments: z.array(RouteSegment),
  waypoints: z.array(RouteWaypoint),
  coverage: Coverage,
  covered_length_m: z.number(),
  uncovered_length_m: z.number(),
  data_quality: DataQuality,
  model_version: z.string().nullable(),
  provider: z.string().optional(),
  provider_distance_km: z.number().optional(),
  /** `[lat, lon]` of the waypoint this alternative was forced through. */
  via: z.array(z.number()).optional(),
  detour_of: z.string().optional(),
  detour_center_cell: z.string().optional(),
  bars: z.string().optional(),
  highest_risk_segment: RouteSegment.nullable().optional(),
});
export type Route = z.infer<typeof Route>;

export const DetourSynthesis = z.object({
  attempted: z.boolean(),
  reason: z.string().nullable(),
  candidate_count: z.number(),
  scored_count: z.number(),
  improved: z.boolean(),
  fastest_expected_crashes: z.number().nullable(),
  best_detour_expected_crashes: z.number().nullable(),
  highest_risk_cell: z.string().optional(),
  highest_risk_percentile: z.number().optional(),
});
export type DetourSynthesis = z.infer<typeof DetourSynthesis>;

/** How the extra, waypoint-forced alternatives were found (see route_engine.py). */
export const ViaAlternatives = z.object({
  enabled: z.boolean(),
  attempted: z.boolean(),
  reason: z.string().nullable(),
  candidate_count: z.number(),
  returned: z.number(),
  added: z.number(),
  rejected: z.object({
    duplicate: z.number(),
    too_long: z.number(),
    backtrack: z.number(),
    unscorable: z.number(),
  }),
});
export type ViaAlternatives = z.infer<typeof ViaAlternatives>;

/** Asked for with `live=1` or a date. `available: false` carries only `reason`. */
export const RouteTraffic = z.object({
  available: z.boolean(),
  reason: z.string().optional(),
  mode: z.enum(["live", "typical"]).optional(),
  source: z.string().optional(),
  depart: z.string().optional(),
  checked_at: z.string().optional(),
});
export type RouteTraffic = z.infer<typeof RouteTraffic>;

export const NightStep = z.object({
  kind: z.enum(["now", "danger", "best", "when", "care", "drive", "stop", "arrive", "pickup"]),
  /** On a drive step: the worst risk on that part of the road. */
  level: z.string().nullable().optional(),
  title: z.string(),
  detail: z.string(),
});
export type NightStep = z.infer<typeof NightStep>;

export const NightOption = z.object({
  route_index: z.number(),
  label: z.string(),
  is_best: z.boolean(),
  minutes: z.number(),
  km: z.number(),
  risk: z.number(),
  critical_km: z.number(),
  high_km: z.number(),
  why: z.string(),
});
export type NightOption = z.infer<typeof NightOption>;

export const NightPlanResponse = z.object({
  schema_version: z.string(),
  applies: z.boolean(),
  best_index: z.number().optional(),
  scanned: z.number().optional(),
  options: z.array(NightOption),
  steps: z.array(NightStep),
  pickup: z
    .object({
      walk_m: z.number(),
      direction: z.string(),
      lat: z.number(),
      lon: z.number(),
      percentile: z.number(),
      from_percentile: z.number(),
    })
    .nullable()
    .optional(),
});
export type NightPlanResponse = z.infer<typeof NightPlanResponse>;

export const GuidanceStep = z.object({
  /** Metres from the start of the route, as TomTom measured it. */
  offset_m: z.number(),
  message: z.string(),
  maneuver: z.string(),
  street: z.string().nullable().optional(),
});
export type GuidanceStep = z.infer<typeof GuidanceStep>;

/** `available: false` carries only `reason`: the drive still runs, without turns. */
export const GuidanceResponse = z.object({
  schema_version: z.string(),
  available: z.boolean(),
  reason: z.string().optional(),
  source: z.string().optional(),
  length_m: z.number().optional(),
  instructions: z.array(GuidanceStep).optional(),
});
export type GuidanceResponse = z.infer<typeof GuidanceResponse>;

export const SafeRouteResponse = z.object({
  schema_version: z.string(),
  traffic: RouteTraffic.optional(),
  route_id: z.string(),
  origin: z.object({ lat: z.number(), lon: z.number() }),
  destination: z.object({ lat: z.number(), lon: z.number() }),
  recommended_route: z.number(),
  total_routes: z.number(),
  provider_routes: z.number(),
  routing_source: z.string(),
  provider_source: z.string(),
  model_version: z.string().nullable(),
  local_time: LocalTime,
  coverage: Coverage,
  detour_synthesis: DetourSynthesis,
  via_alternatives: ViaAlternatives.optional(),
  warnings: z.array(z.string()),
  recommendation: z.object({
    text: z.string(),
    risk_reduction: z.number(),
    risk_reduction_percent: z.number(),
    detour_improved: z.boolean(),
    basis: z.string(),
  }),
  routes: z.array(Route),
});
export type SafeRouteResponse = z.infer<typeof SafeRouteResponse>;

/** One departure hour for a route: the same stretches, scored at that hour. */
export const DepartureSlot = z.object({
  offset_hours: z.number(),
  hour: z.number(),
  day_of_week: z.number(),
  expected_crashes_total: z.number(),
  change_percent: z.number(),
  is_chosen: z.boolean(),
  is_recommended: z.boolean(),
  small_hours: z.boolean(),
  /** Earlier than now on a live trip: on the chart, never suggested. */
  is_past: z.boolean().optional(),
  city_change_percent: z.number().optional(),
  adjusted_change_percent: z.number().optional(),
});
export type DepartureSlot = z.infer<typeof DepartureSlot>;

/** `available: false` carries only `reason`: the trip has no learned time curve. */
export const DeparturesResponse = z.object({
  schema_version: z.string(),
  available: z.boolean(),
  reason: z.string().optional(),
  slots: z.array(DepartureSlot),
  basis: z.string().optional(),
  verdict: z
    .enum(["move", "little_difference", "only_small_hours", "busy_hours_only", "already_best"])
    .optional(),
  text: z.string().optional(),
  recommended: DepartureSlot.nullable().optional(),
  reduction_percent: z.number().optional(),
  adjusted_reduction_percent: z.number().nullable().optional(),
  threshold_percent: z.number().optional(),
  note: z.string().optional(),
  route_index: z.number().optional(),
  model_version: z.string().nullable().optional(),
});
export type DeparturesResponse = z.infer<typeof DeparturesResponse>;

export const Hotspot = z.object({
  rank: z.number(),
  lat: z.number(),
  lon: z.number(),
  crash_count: z.number(),
  risk_level: RiskLevel,
  /** Present when the list was asked for near a point. */
  distance_m: z.number().optional(),
});
export type Hotspot = z.infer<typeof Hotspot>;

export const HotspotsResponse = z.object({
  schema_version: z.string(),
  source: z.string(),
  total_hotspots: z.number(),
  hotspots: z.array(Hotspot),
  near: z
    .object({ lat: z.number(), lon: z.number(), radius_m: z.number() })
    .optional(),
});
export type HotspotsResponse = z.infer<typeof HotspotsResponse>;

export const SafeStop = z.object({
  name: z.string(),
  type: z.string(),
  lat: z.number(),
  lon: z.number(),
  distance_m: z.number(),
  distance_km: z.number(),
  safety_priority: z.number(),
});
export type SafeStop = z.infer<typeof SafeStop>;

export const NearestSafeResponse = z.object({
  schema_version: z.string(),
  location: z.object({ lat: z.number(), lon: z.number() }),
  nearest_stop: SafeStop,
  total_found: z.number(),
  search_radius_m: z.number(),
  stops: z.array(SafeStop),
  message: z.string(),
});
export type NearestSafeResponse = z.infer<typeof NearestSafeResponse>;

export const WeatherAlert = z.object({
  type: z.string(),
  severity: z.string(),
  message: z.string(),
  location: z.string().optional(),
  lat: z.number().optional(),
  lon: z.number().optional(),
  data_source: z.string().optional(),
  timestamp: z.string().optional(),
  temperature: z.number().optional(),
  rain: z.boolean().optional(),
  wind_speed: z.number().optional(),
});
export type WeatherAlert = z.infer<typeof WeatherAlert>;

export const AlertsResponse = z.object({
  schema_version: z.string(),
  alerts: z.array(WeatherAlert),
  total_alerts: z.number(),
  data_quality: z.string(),
  last_checked: z.string().nullable(),
  next_check: z.string(),
  monitored_locations: z.number(),
  warning: z.string().nullable(),
});
export type AlertsResponse = z.infer<typeof AlertsResponse>;

export const LocationAlertsResponse = z.object({
  schema_version: z.string(),
  alerts: z.array(WeatherAlert),
  total_alerts: z.number(),
  location: z.object({ lat: z.number(), lon: z.number(), name: z.string().optional() }),
  message: z.string(),
  weather_source: z.string(),
});
export type LocationAlertsResponse = z.infer<typeof LocationAlertsResponse>;

export const StatsResponse = z.object({
  schema_version: z.string(),
  average_risk_score: z.number(),
  most_dangerous_hour: z.object({
    hour: z.number().nullable(),
    average_score: z.number().nullable(),
  }),
  risk_level_breakdown: z.record(z.string(), z.number()),
  total_requests: z.object({ risk: z.number(), routes: z.number() }),
  scope: z.string(),
  privacy_note: z.string(),
});
export type StatsResponse = z.infer<typeof StatsResponse>;

export const HealthResponse = z.object({
  schema_version: z.string(),
  status: z.string(),
  endpoints: z.array(z.string()),
  endpoint_count: z.number(),
  coverage: z.object({
    bounding_box: z.array(z.number()),
    model_area: z.string(),
  }),
  checks: z.object({
    crash_risk_model: z.object({
      available: z.boolean(),
      cells: z.number(),
      gate_passed: z.boolean(),
      status: z.string(),
      version: z.string().nullable(),
      error: z.string().nullable().optional(),
    }),
    severity_model: z.string(),
    /** The global crash model (the UAE); absent from older APIs. */
    global_model: z
      .object({
        available: z.boolean(),
        error: z.string().nullable().optional(),
        version: z.string().nullable().optional(),
        gate_passed: z.boolean().optional(),
        regions: z.record(z.string(), z.object({ name: z.string(), cells: z.number() })).optional(),
      })
      .optional(),
    indexes: z.object({
      crash: z.string(),
      bars: z.string(),
      safe_stops: z.string(),
    }),
    providers: z.object({
      routing_chain: z.string(),
      weather_api_key: z.string(),
      osrm: z.string().optional(),
      /** Weather providers in the order they are tried. */
      weather_chain: z.string().optional(),
      /** The provider that served the most recent reading, or null before the first. */
      weather_last: z.string().nullable().optional(),
      places: z.string().optional(),
      /** Nightlife count providers in the order they are tried. */
      nightlife: z.string().optional(),
      context_model: z.string().optional(),
      /** `ok`, or `paused Ns` while a provider that keeps failing is skipped. */
      provider_state: z.record(z.string(), z.string()).optional(),
    }).passthrough(),
  }).passthrough(),
});
export type HealthResponse = z.infer<typeof HealthResponse>;

export const ErrorResponse = z.object({
  error: z.string(),
  schema_version: z.string().optional(),
});
export type ErrorResponse = z.infer<typeof ErrorResponse>;

const ClassNumbers = z.record(z.string(), z.number());

/**
 * The severity model card. Every field is optional: the endpoint reports what
 * the trained artifact carries, and an older artifact may lack some of it.
 */
export const MLStatsResponse = z
  .object({
    schema_version: z.string().optional(),
    model: z.string().optional(),
    version: z.string().optional(),
    status: z.string().optional(),
    target: z.string().optional(),
    experimental: z.boolean().optional(),
    used_for_risk_scoring: z.boolean().optional(),
    classes: z.array(z.string()).optional(),
    training_records: z.number().optional(),
    cross_validation_accuracy: z.number().optional(),
    cv_std: z.number().optional(),
    balanced_accuracy: z.number().optional(),
    macro_f1: z.number().optional(),
    roc_auc_macro: z.number().optional(),
    fatal_pr_auc: z.number().optional(),
    brier_score: z.number().optional(),
    baseline_accuracy: z.number().optional(),
    baseline_macro_f1: z.number().optional(),
    per_class_precision: ClassNumbers.optional(),
    per_class_recall: ClassNumbers.optional(),
    confusion_matrix: z.array(z.array(z.number())).optional(),
    feature_importance: ClassNumbers.optional(),
    importance_method: z.string().optional(),
    dataset: z
      .object({
        rows_used: z.number().optional(),
        date_range: z.array(z.string()).optional(),
        source: z.string().optional(),
        class_share: ClassNumbers.optional(),
        class_counts: ClassNumbers.optional(),
      })
      .passthrough()
      .optional(),
    ablation_context_only: z
      .object({
        accuracy: z.number().optional(),
        macro_f1: z.number().optional(),
        roc_auc_macro: z.number().optional(),
        fatal_recall: z.number().optional(),
      })
      .passthrough()
      .optional(),
    calibration: z.object({ note: z.string().optional() }).passthrough().optional(),
    honesty: z.array(z.string()).optional(),
    limitations: z.array(z.string()).optional(),
  })
  .passthrough();
export type MLStatsResponse = z.infer<typeof MLStatsResponse>;

/** One crash scenario scored by the (research-only) severity model. */
export const SeverityResponse = z
  .object({
    schema_version: z.string().optional(),
    predicted: z.string(),
    confidence: z.number(),
    probabilities: ClassNumbers,
    probability_semantics: z.string().optional(),
    experimental: z.boolean(),
    used_for_risk_scoring: z.boolean(),
    model: z.string(),
    note: z.string(),
    input: z
      .object({
        hour: z.number(),
        lat: z.number(),
        lon: z.number(),
        vehicles: z.array(z.string()).optional(),
        factors: z.array(z.string()).optional(),
      })
      .passthrough(),
  })
  .passthrough();
export type SeverityResponse = z.infer<typeof SeverityResponse>;
