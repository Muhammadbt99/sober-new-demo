import { isNow, isoDateFor } from "@/lib/risk";
import {
  AlertsResponse,
  DeparturesResponse,
  ErrorResponse,
  HealthResponse,
  HeatmapResponse,
  HotspotsResponse,
  LocationAlertsResponse,
  MLStatsResponse,
  NearestSafeResponse,
  RiskCompareResponse,
  RiskResponse,
  RiskTrendResponse,
  SafeRouteResponse,
  SeverityResponse,
  StatsResponse,
  SurfaceResponse,
  GlobalSurfaceResponse,
  type GlobalSurfaceResponse as GlobalSurfaceResponseType,
  GlobalModelCard,
  type GlobalModelCard as GlobalModelCardType,
  DangerTypesCard,
  type DangerTypesCard as DangerTypesCardType,
  ModelCard,
  type ModelCard as ModelCardType,
  type SurfaceResponse as SurfaceResponseType,
  type AlertsResponse as AlertsResponseType,
  type HealthResponse as HealthResponseType,
  type HeatmapResponse as HeatmapResponseType,
  type HotspotsResponse as HotspotsResponseType,
  type LocationAlertsResponse as LocationAlertsResponseType,
  type MLStatsResponse as MLStatsResponseType,
  type NearestSafeResponse as NearestSafeResponseType,
  type RiskCompareResponse as RiskCompareResponseType,
  type RiskResponse as RiskResponseType,
  type RiskTrendResponse as RiskTrendResponseType,
  type DeparturesResponse as DeparturesResponseType,
  GuidanceResponse,
  type GuidanceResponse as GuidanceResponseType,
  NightPlanResponse,
  type NightPlanResponse as NightPlanResponseType,
  type SafeRouteResponse as SafeRouteResponseType,
  type SeverityResponse as SeverityResponseType,
  type StatsResponse as StatsResponseType,
} from "@/lib/contract";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** Satellite tiles, through the API so the provider's key stays on the server. */
export const SATELLITE_TILES = `${new URL(API_BASE, window.location.origin).href.replace(/\/$/, "")}/tiles/satellite/{z}/{x}/{y}.jpg`;
const GEOCODE_BASE = "/geocode";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export class NetworkError extends Error {
  constructor(message = "The API could not be reached") {
    super(message);
    this.name = "NetworkError";
  }
}

export class ContractError extends Error {
  path: string;
  issues: string[];

  constructor(path: string, issues: string[]) {
    super(`The API response did not match the contract at ${path}`);
    this.name = "ContractError";
    this.path = path;
    this.issues = issues;
  }
}

type Schema<T> = {
  safeParse: (value: unknown) =>
    | { success: true; data: T }
    | { success: false; error: { issues: Array<{ path: PropertyKey[]; message: string }> } };
};

async function request<T>(
  path: string,
  schema: Schema<T>,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new NetworkError();
  }

  const text = await response.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new ContractError(path, ["Response was not valid JSON"]);
  }

  if (!response.ok) {
    const parsed = ErrorResponse.safeParse(body);
    const message = parsed.success ? parsed.data.error : `HTTP ${response.status}`;
    throw new ApiError(message, response.status, body);
  }

  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new ContractError(
      path,
      parsed.error.issues.map(
        (issue) => `${issue.path.map(String).join(".") || "(root)"}: ${issue.message}`,
      ),
    );
  }
  return parsed.data;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Live while the chosen time is now; otherwise typical traffic for that date. */
function trafficParams(time: TimeParams) {
  return isNow(time.day, time.hour)
    ? { live: 1 }
    : { date: isoDateFor(time.day, time.hour) };
}

function coord(value: number): string {
  return value.toFixed(5);
}

export type TimeParams = { hour: number; day: number };
export type Point = { lat: number; lon: number };

export const api = {
  risk(point: Point, time: TimeParams, signal?: AbortSignal): Promise<RiskResponseType> {
    return request(
      `/risk${query({
        lat: coord(point.lat),
        lon: coord(point.lon),
        hour: time.hour,
        day: time.day,
      })}`,
      RiskResponse,
      signal,
    );
  },

  heatmap(
    point: Point,
    time: TimeParams,
    grid = 8,
    span = 0.2,
    signal?: AbortSignal,
  ): Promise<HeatmapResponseType> {
    const lat1 = point.lat - span / 2;
    const lat2 = point.lat + span / 2;
    const lonSpan = span * 1.2;
    const lon1 = point.lon - lonSpan / 2;
    const lon2 = point.lon + lonSpan / 2;
    return request(
      `/heatmap${query({
        lat1: coord(lat1),
        lon1: coord(lon1),
        lat2: coord(lat2),
        lon2: coord(lon2),
        grid,
        hour: time.hour,
        day: time.day,
      })}`,
      HeatmapResponse,
      signal,
    );
  },

  /**
   * Historical crash clusters: the city's busiest, or with `near` the busiest
   * within `radiusM` metres of a point (empty outside New York).
   */
  hotspots(
    topK = 20,
    near?: Point | null,
    radiusM = 2000,
    signal?: AbortSignal,
  ): Promise<HotspotsResponseType> {
    return request(
      `/hotspots${query({
        top_k: topK,
        lat: near ? coord(near.lat) : undefined,
        lon: near ? coord(near.lon) : undefined,
        radius: near ? radiusM : undefined,
      })}`,
      HotspotsResponse,
      signal,
    );
  },

  /**
   * Every cell of the NYC crash-model surface at one hour (the whole city).
   * With `danger`, each cell also carries its expected rate of that kind of
   * crash against a typical NYC cell-hour.
   */
  surface(
    time: TimeParams,
    danger: string | null = null,
    signal?: AbortSignal,
  ): Promise<SurfaceResponseType> {
    return request(
      `/surface${query({ hour: time.hour, day: time.day, danger })}`,
      SurfaceResponse,
      signal,
    );
  },

  /** The global crash model's cells around a point (regions such as the UAE). */
  globalSurface(
    point: Point,
    time: TimeParams,
    radiusKm: number,
    signal?: AbortSignal,
  ): Promise<GlobalSurfaceResponseType> {
    return request(
      `/surface/global${query({
        lat: point.lat.toFixed(4),
        lon: point.lon.toFixed(4),
        radius_km: radiusKm,
        hour: time.hour,
        day: time.day,
      })}`,
      GlobalSurfaceResponse,
      signal,
    );
  },

  /** The global crash model's card: cities, protocol, held-out results. */
  globalModelCard(signal?: AbortSignal): Promise<GlobalModelCardType> {
    return request("/global_model", GlobalModelCard, signal);
  },

  /** The danger-type model card: definitions, gate and held-out metrics. */
  dangerTypes(signal?: AbortSignal): Promise<DangerTypesCardType> {
    return request("/danger_types", DangerTypesCard, signal);
  },

  modelCard(signal?: AbortSignal): Promise<ModelCardType> {
    return request("/model_card", ModelCard, signal);
  },

  trend(
    point: Point,
    day: number,
    signal?: AbortSignal,
  ): Promise<RiskTrendResponseType> {
    return request(
      `/risk/trend${query({
        lat: coord(point.lat),
        lon: coord(point.lon),
        day,
      })}`,
      RiskTrendResponse,
      signal,
    );
  },

  compare(
    a: Point,
    b: Point,
    time: TimeParams,
    signal?: AbortSignal,
  ): Promise<RiskCompareResponseType> {
    return request(
      `/risk/compare${query({
        lat1: coord(a.lat),
        lon1: coord(a.lon),
        lat2: coord(b.lat),
        lon2: coord(b.lon),
        hour: time.hour,
        day: time.day,
      })}`,
      RiskCompareResponse,
      signal,
    );
  },

  safeRoute(
    origin: Point,
    destination: Point,
    time: TimeParams,
    signal?: AbortSignal,
  ): Promise<SafeRouteResponseType> {
    return request(
      `/safe_route${query({
        olat: coord(origin.lat),
        olon: coord(origin.lon),
        dlat: coord(destination.lat),
        dlon: coord(destination.lon),
        hour: time.hour,
        day: time.day,
        ...trafficParams(time),
      })}`,
      SafeRouteResponse,
      signal,
    );
  },

  nightPlan(
    origin: Point,
    destination: Point,
    time: TimeParams,
    signal?: AbortSignal,
  ): Promise<NightPlanResponseType> {
    return request(
      `/night_plan${query({
        olat: coord(origin.lat),
        olon: coord(origin.lon),
        dlat: coord(destination.lat),
        dlon: coord(destination.lon),
        hour: time.hour,
        day: time.day,
        ...trafficParams(time),
      })}`,
      NightPlanResponse,
      signal,
    );
  },

  guidance(
    origin: Point,
    destination: Point,
    time: TimeParams,
    route: number,
    signal?: AbortSignal,
  ): Promise<GuidanceResponseType> {
    return request(
      `/safe_route/guidance${query({
        olat: coord(origin.lat),
        olon: coord(origin.lon),
        dlat: coord(destination.lat),
        dlon: coord(destination.lon),
        hour: time.hour,
        day: time.day,
        route,
        ...trafficParams(time),
      })}`,
      GuidanceResponse,
      signal,
    );
  },

  departures(
    origin: Point,
    destination: Point,
    time: TimeParams,
    route: number,
    from: TimeParams | null,
    signal?: AbortSignal,
  ): Promise<DeparturesResponseType> {
    return request(
      `/safe_route/departures${query({
        olat: coord(origin.lat),
        olon: coord(origin.lon),
        dlat: coord(destination.lat),
        dlon: coord(destination.lon),
        hour: time.hour,
        day: time.day,
        route,
        ...trafficParams(time),
        from_hour: from?.hour,
        from_day: from?.day,
      })}`,
      DeparturesResponse,
      signal,
    );
  },

  stats(signal?: AbortSignal): Promise<StatsResponseType> {
    return request("/stats", StatsResponse, signal);
  },

  health(signal?: AbortSignal): Promise<HealthResponseType> {
    return request("/health", HealthResponse, signal);
  },

  nearestSafe(
    point: Point,
    radius = 3000,
    signal?: AbortSignal,
  ): Promise<NearestSafeResponseType> {
    return request(
      `/nearest_safe${query({
        lat: coord(point.lat),
        lon: coord(point.lon),
        radius,
      })}`,
      NearestSafeResponse,
      signal,
    );
  },

  alerts(signal?: AbortSignal): Promise<AlertsResponseType> {
    return request("/alerts", AlertsResponse, signal);
  },

  locationAlerts(
    point: Point,
    signal?: AbortSignal,
  ): Promise<LocationAlertsResponseType> {
    return request(
      `/alerts/check_location${query({
        lat: coord(point.lat),
        lon: coord(point.lon),
      })}`,
      LocationAlertsResponse,
      signal,
    );
  },

  mlStats(signal?: AbortSignal): Promise<MLStatsResponseType> {
    return request("/ml/stats", MLStatsResponse, signal);
  },

  /**
   * Score one crash scenario with the research-only severity model. `vehicle`
   * and `factor` are free text the server maps onto its own groups.
   */
  severity(
    point: Point,
    time: TimeParams,
    scenario: { vehicle?: string; factor?: string } = {},
    signal?: AbortSignal,
  ): Promise<SeverityResponseType> {
    const search = new URLSearchParams({
      lat: coord(point.lat),
      lon: coord(point.lon),
      hour: String(time.hour),
      day: String(time.day),
    });
    if (scenario.vehicle) search.append("vehicle", scenario.vehicle);
    if (scenario.factor) search.append("factor", scenario.factor);
    return request(`/ml/severity?${search.toString()}`, SeverityResponse, signal);
  },
};

/** Static charts the API renders for the severity model card. */
export const ML_IMAGES = {
  confusionMatrix: `${API_BASE}/ml/confusion_matrix`,
  featureImportance: `${API_BASE}/ml/feature_importance`,
} as const;

export type Place = {
  id: string;
  name: string;
  detail: string;
  lat: number;
  lon: number;
};

type NominatimResult = {
  place_id?: number;
  display_name?: string;
  name?: string;
  lat?: string;
  lon?: string;
  type?: string;
  address?: Record<string, string>;
};

export async function searchPlaces(
  queryText: string,
  signal?: AbortSignal,
): Promise<Place[]> {
  const text = queryText.trim();
  if (text.length < 2) return [];
  let response: Response;
  try {
    response = await fetch(
      `${GEOCODE_BASE}${query({ q: text, limit: 6 })}`,
      { headers: { Accept: "application/json" }, signal },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new NetworkError("Place search is unavailable");
  }
  if (!response.ok) throw new ApiError("Place search failed", response.status, null);
  const body = (await response.json()) as NominatimResult[];
  return body
    .filter((item) => item.lat && item.lon)
    .map((item, index) => {
      const display = item.display_name ?? "Unknown place";
      const name = item.name ?? display.split(",")[0] ?? display;
      return {
        id: String(item.place_id ?? `${name}-${index}`),
        name,
        detail: display,
        lat: Number(item.lat),
        lon: Number(item.lon),
      };
    });
}

