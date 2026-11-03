import type { Route } from "@/lib/contract";

/** The name a route goes by, on its card and on its chip on the map. */
export function routeLabel(route: Route): string {
  if (route.is_safest) return "Safest";
  if (route.is_fastest) return "Fastest";
  if (route.is_balanced) return "Balanced";
  return `Option ${route.rank}`;
}

/** The route the comparison is measured against: the quickest one. */
export function fastestRoute(routes: Route[]): Route | undefined {
  return (
    routes.find((route) => route.is_fastest) ??
    routes.reduce<Route | undefined>(
      (best, route) =>
        !best || (route.duration_min ?? Infinity) < (best.duration_min ?? Infinity)
          ? route
          : best,
      undefined,
    )
  );
}

/** How a route compares with the fastest one, in the terms a driver cares about. */
export function tradeoff(route: Route, fastest: Route | undefined) {
  if (!fastest || route === fastest) return null;
  const minutes =
    route.duration_min !== null && fastest.duration_min !== null
      ? route.duration_min - fastest.duration_min
      : null;
  const km = route.distance_km - fastest.distance_km;
  const crashes =
    fastest.expected_crashes_total > 0
      ? ((route.expected_crashes_total - fastest.expected_crashes_total) /
          fastest.expected_crashes_total) *
        100
      : null;
  return { minutes, km, crashes };
}
