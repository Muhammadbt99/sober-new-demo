import type { Route, RouteTraffic } from "@/lib/contract";

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

/** Enough to recognise one road when a refresh reorders the list. */
export function routeKey(route: Route): string {
  return `${route.distance_km}:${route.geometry[Math.floor(route.geometry.length / 2)]}`;
}

/** Minutes a live reroute saves against the best of the usual roads, if any. */
export function rerouteSaving(routes: Route[]): number {
  const quickest = (list: Route[]) => Math.min(...list.map((route) => route.duration_min ?? Infinity));
  const usual = routes.filter((route) => !route.live_reroute);
  const found = routes.filter((route) => route.live_reroute);
  if (!usual.length || !found.length) return 0;
  return Math.round(quickest(usual) - quickest(found));
}

/** Where a trip's drive times come from, in words. */
export function trafficNote(
  traffic: RouteTraffic | undefined,
  routes: Route[],
  dateLabel: string,
  hourLabel: string,
): { text: string; title: string; kind: "live" | "typical" | "none" } {
  if (!traffic?.available) {
    return {
      kind: "none",
      text: "Drive times assume empty roads",
      title: traffic?.reason ?? "Traffic could not be checked",
    };
  }
  const saving = rerouteSaving(routes);
  const found = saving >= 1 ? ` · found a road ${saving} min faster` : "";
  if (traffic.mode === "live") {
    const checked = traffic.checked_at ? new Date(traffic.checked_at) : null;
    const clock = checked
      ? `${String(checked.getHours()).padStart(2, "0")}:${String(checked.getMinutes()).padStart(2, "0")}`
      : "";
    return {
      kind: "live",
      text: `Live traffic · TomTom · checked ${clock}${found}`,
      title: "Routes and times refresh every 3 minutes while this is the time shown",
    };
  }
  return {
    kind: "typical",
    text: `Typical traffic for ${dateLabel}, ${hourLabel} · TomTom${found}`,
    title: "A forecast from past traffic. Crashes and closures on the day cannot be known yet.",
  };
}
