import { useQuery } from "@tanstack/react-query";

import { api, type Place, type Point, type TimeParams } from "@/lib/api";
import { isNow } from "@/lib/risk";

const MINUTE = 60_000;

export const queryKeys = {
  risk: (point: Point, time: TimeParams) =>
    ["risk", point.lat.toFixed(4), point.lon.toFixed(4), time.hour, time.day] as const,
  heatmap: (point: Point, time: TimeParams) =>
    ["heatmap", point.lat.toFixed(3), point.lon.toFixed(3), time.hour, time.day] as const,
  hotspots: (near: Point | null) =>
    ["hotspots", near ? near.lat.toFixed(3) : "city", near ? near.lon.toFixed(3) : "city"] as const,
  surface: (time: TimeParams, danger: string | null) =>
    ["surface", time.hour, time.day, danger ?? "risk"] as const,
  trend: (point: Point, day: number) =>
    ["trend", point.lat.toFixed(4), point.lon.toFixed(4), day] as const,
  compare: (a: Point, b: Point, time: TimeParams) =>
    [
      "compare",
      a.lat.toFixed(4),
      a.lon.toFixed(4),
      b.lat.toFixed(4),
      b.lon.toFixed(4),
      time.hour,
      time.day,
    ] as const,
  route: (origin: Point, destination: Point, time: TimeParams) =>
    [
      "route",
      origin.lat.toFixed(4),
      origin.lon.toFixed(4),
      destination.lat.toFixed(4),
      destination.lon.toFixed(4),
      time.hour,
      time.day,
    ] as const,
  departures: (
    origin: Point,
    destination: Point,
    time: TimeParams,
    route: number,
    from: TimeParams | null,
  ) =>
    [
      "departures",
      origin.lat.toFixed(4),
      origin.lon.toFixed(4),
      destination.lat.toFixed(4),
      destination.lon.toFixed(4),
      time.hour,
      time.day,
      route,
      from?.hour,
      from?.day,
    ] as const,
  stats: ["stats"] as const,
  health: ["health"] as const,
  nearestSafe: (point: Point) =>
    ["nearest-safe", point.lat.toFixed(4), point.lon.toFixed(4)] as const,
  alerts: ["alerts"] as const,
  locationAlerts: (point: Point) =>
    ["location-alerts", point.lat.toFixed(4), point.lon.toFixed(4)] as const,
};

export function useRisk(point: Point, time: TimeParams) {
  return useQuery({
    queryKey: queryKeys.risk(point, time),
    queryFn: ({ signal }) => api.risk(point, time, signal),
    staleTime: 30_000,
  });
}

/**
 * A sampled 8 x 8 grid around a point. Inside NYC the whole-city `/surface`
 * replaces it; it is still what draws the global estimate elsewhere, and the
 * fallback if the API predates `/surface`.
 */
export function useHeatmap(point: Point, time: TimeParams, enabled = true) {
  return useQuery({
    queryKey: queryKeys.heatmap(point, time),
    queryFn: ({ signal }) => api.heatmap(point, time, 8, 0.2, signal),
    enabled,
    staleTime: 30_000,
  });
}

/** Crash clusters near a point (or the city's busiest when `near` is null). */
export function useHotspots(near: Point | null) {
  return useQuery({
    queryKey: queryKeys.hotspots(near),
    queryFn: ({ signal }) => api.hotspots(20, near, 2000, signal),
    staleTime: 5 * MINUTE,
    // Keep the last list on screen while the next place loads.
    placeholderData: (previous) => previous,
  });
}

/**
 * The whole NYC model surface for an hour; independent of the selected point.
 * `danger` adds one kind of crash's layer (impaired, drowsy, ...).
 */
export function useSurface(time: TimeParams, danger: string | null = null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.surface(time, danger),
    queryFn: ({ signal }) => api.surface(time, danger, signal),
    enabled,
    staleTime: 2 * MINUTE,
    retry: false,
    // Scrubbing the hour swaps the colours in place instead of blanking the map.
    placeholderData: (previous) => previous,
  });
}

/**
 * The global crash model's cells around a point, in a region it serves (the
 * UAE). The point is snapped to ~2 km so panning nearby reuses the answer.
 */
export function useGlobalSurface(point: Point, time: TimeParams, enabled = true, radiusKm = 14) {
  const snapped = {
    lat: Math.round(point.lat * 50) / 50,
    lon: Math.round(point.lon * 50) / 50,
  };
  return useQuery({
    queryKey: ["global-surface", snapped.lat, snapped.lon, radiusKm, time.hour, time.day] as const,
    queryFn: ({ signal }) => api.globalSurface(snapped, time, radiusKm, signal),
    enabled,
    staleTime: 5 * MINUTE,
    retry: false,
    placeholderData: (previous) => previous,
  });
}

export function useTrend(point: Point, day: number) {
  return useQuery({
    queryKey: queryKeys.trend(point, day),
    queryFn: ({ signal }) => api.trend(point, day, signal),
    staleTime: 60_000,
  });
}

export function useCompare(a: Point, b: Point, time: TimeParams, enabled = true) {
  return useQuery({
    queryKey: queryKeys.compare(a, b, time),
    queryFn: ({ signal }) => api.compare(a, b, time, signal),
    enabled,
    staleTime: 60_000,
  });
}

export function useSafeRoute(
  origin: Place | null,
  destination: Place | null,
  time: TimeParams,
  enabled: boolean,
  /** Hold the list still, so a refresh cannot move anything during a drive. */
  frozen = false,
) {
  const live = isNow(time.day, time.hour);
  return useQuery({
    queryKey: [
      ...queryKeys.route(origin ?? { lat: 0, lon: 0 }, destination ?? { lat: 0, lon: 0 }, time),
      live,
    ],
    queryFn: ({ signal }) =>
      api.safeRoute(
        { lat: origin!.lat, lon: origin!.lon },
        { lat: destination!.lat, lon: destination!.lon },
        time,
        signal,
      ),
    enabled: Boolean(enabled && origin && destination),
    staleTime: (live ? 3 : 60) * MINUTE,
    // Traffic moves; a refresh swaps the times in without a loading state.
    refetchInterval: live && !frozen ? 3 * MINUTE : false,
    retry: false,
  });
}

export function useNightPlan(
  origin: Point,
  destination: Point,
  time: TimeParams,
  enabled: boolean,
) {
  return useQuery({
    queryKey: [
      "night-plan",
      origin.lat.toFixed(4),
      origin.lon.toFixed(4),
      destination.lat.toFixed(4),
      destination.lon.toFixed(4),
      time.hour,
      time.day,
    ],
    queryFn: ({ signal }) => api.nightPlan(origin, destination, time, signal),
    enabled,
    staleTime: 3 * MINUTE,
    retry: false,
  });
}

/** Directions for the route being driven; they do not change during a drive. */
export function useGuidance(origin: Point, destination: Point, time: TimeParams, route: number) {
  return useQuery({
    queryKey: [
      "guidance",
      origin.lat.toFixed(4),
      origin.lon.toFixed(4),
      destination.lat.toFixed(4),
      destination.lon.toFixed(4),
      time.hour,
      time.day,
      route,
    ],
    queryFn: ({ signal }) => api.guidance(origin, destination, time, route, signal),
    staleTime: 30 * MINUTE,
    retry: false,
  });
}

export function useDepartures(
  origin: Point,
  destination: Point,
  time: TimeParams,
  route: number,
  from: TimeParams | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.departures(origin, destination, time, route, from),
    queryFn: ({ signal }) => api.departures(origin, destination, time, route, from, signal),
    enabled,
    staleTime: 5 * MINUTE,
    retry: false,
  });
}

export function useStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: ({ signal }) => api.stats(signal),
    staleTime: 60_000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => api.health(signal),
    staleTime: 30_000,
  });
}

export function useNearestSafe(point: Point, enabled = true) {
  return useQuery({
    queryKey: queryKeys.nearestSafe(point),
    queryFn: ({ signal }) => api.nearestSafe(point, 3000, signal),
    enabled,
    staleTime: 5 * MINUTE,
    retry: false,
  });
}

export function useAlerts() {
  return useQuery({
    queryKey: queryKeys.alerts,
    queryFn: ({ signal }) => api.alerts(signal),
    staleTime: 60_000,
    retry: false,
  });
}

export function useLocationAlerts(point: Point, enabled = true) {
  return useQuery({
    queryKey: queryKeys.locationAlerts(point),
    queryFn: ({ signal }) => api.locationAlerts(point, signal),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

export function useMlStats() {
  return useQuery({
    queryKey: ["ml-stats"] as const,
    queryFn: ({ signal }) => api.mlStats(signal),
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

export type SeverityScenario = { vehicle?: string; factor?: string };

export function useSeverity(
  point: Point,
  time: TimeParams,
  scenario: SeverityScenario,
  enabled = true,
) {
  return useQuery({
    queryKey: [
      "severity",
      point.lat.toFixed(4),
      point.lon.toFixed(4),
      time.hour,
      time.day,
      scenario.vehicle ?? "",
      scenario.factor ?? "",
    ] as const,
    queryFn: ({ signal }) => api.severity(point, time, scenario, signal),
    enabled,
    staleTime: 5 * MINUTE,
    retry: false,
    // Keep the previous bars on screen while a new scenario loads, so the
    // numbers glide between scenarios instead of flashing to a skeleton.
    placeholderData: (previous) => previous,
  });
}

/** The danger-type model card (Method screen). */
export function useDangerCard(enabled = true) {
  return useQuery({
    queryKey: ["danger-types-card"] as const,
    queryFn: ({ signal }) => api.dangerTypes(signal),
    enabled,
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

/** The crash model's validation numbers (Method screen). */
export function useModelCard(enabled = true) {
  return useQuery({
    queryKey: ["model-card"] as const,
    queryFn: ({ signal }) => api.modelCard(signal),
    enabled,
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

/** The global crash model's card (Method screen). */
export function useGlobalModelCard(enabled = true) {
  return useQuery({
    queryKey: ["global-model-card"] as const,
    queryFn: ({ signal }) => api.globalModelCard(signal),
    enabled,
    staleTime: 10 * MINUTE,
    retry: false,
  });
}
