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
    })
    .passthrough()
    .nullable()
    .optional(),
  model_version: z.string().nullable(),
  cell: z.string().nullable(),
  place: PlaceName,
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
});
export type SurfaceCell = z.infer<typeof SurfaceCell>;

export const SurfaceResponse = z.object({
  schema_version: z.string(),
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

export const SafeRouteResponse = z.object({
  schema_version: z.string(),
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
