import { useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import type { Place, Point } from "@/lib/api";

export type Panel = "explore" | "plan" | "method";

export const NYC_CENTER: Point = { lat: 40.758, lon: -73.9855 };

export const DEMO_ORIGIN: Place = {
  id: "demo-origin",
  name: "Times Square",
  detail: "Manhattan, New York",
  lat: 40.758,
  lon: -73.9855,
};

export const DEMO_DESTINATION: Place = {
  id: "demo-destination",
  name: "Brooklyn Borough Hall",
  detail: "Downtown Brooklyn, New York",
  lat: 40.6928,
  lon: -73.9901,
};

/**
 * Demo trips. New York is scored by the crash model; the others by the global
 * estimate (country road-death rate, road, time, weather, nightlife).
 */
export type DemoTrip = {
  id: string;
  city: string;
  origin: Place;
  destination: Place;
  hour: number;
  day: number;
};

const place = (id: string, name: string, detail: string, lat: number, lon: number): Place => ({
  id,
  name,
  detail,
  lat,
  lon,
});

export const DEMO_TRIPS: DemoTrip[] = [
  {
    id: "saturday-night",
    city: "New York",
    origin: DEMO_ORIGIN,
    destination: DEMO_DESTINATION,
    hour: 23,
    day: 5,
  },
  {
    id: "london-friday",
    city: "London",
    origin: place("demo-london-o", "Trafalgar Square", "Westminster, London", 51.508, -0.1281),
    destination: place("demo-london-d", "Canary Wharf", "Tower Hamlets, London", 51.5054, -0.0235),
    hour: 23,
    day: 4,
  },
  {
    id: "paris-saturday",
    city: "Paris",
    origin: place("demo-paris-o", "Arc de Triomphe", "Paris 8e", 48.8738, 2.295),
    destination: place("demo-paris-d", "Gare de Lyon", "Paris 12e", 48.8443, 2.3744),
    hour: 23,
    day: 5,
  },
  {
    id: "dubai-friday",
    city: "Dubai",
    origin: place("demo-dubai-o", "Burj Khalifa", "Downtown Dubai", 25.1972, 55.2744),
    destination: place("demo-dubai-d", "Dubai Marina", "Dubai", 25.0805, 55.1403),
    hour: 22,
    day: 4,
  },
  {
    id: "riyadh-thursday",
    city: "Riyadh",
    origin: place("demo-riyadh-o", "Kingdom Centre", "Al Olaya, Riyadh", 24.7114, 46.6744),
    destination: place("demo-riyadh-d", "King Khalid International Airport", "Riyadh", 24.9576, 46.6988),
    hour: 23,
    day: 3,
  },
  {
    id: "tokyo-friday",
    city: "Tokyo",
    origin: place("demo-tokyo-o", "Shibuya Crossing", "Shibuya, Tokyo", 35.6595, 139.7005),
    destination: place("demo-tokyo-d", "Tokyo Tower", "Minato, Tokyo", 35.6586, 139.7454),
    hour: 23,
    day: 4,
  },
];

export const DEFAULT_HOUR = 23;
export const DEFAULT_DAY = 5;

export type UrlState = {
  panel: Panel;
  lat: number;
  lon: number;
  /** Name of the selected point when it came from search or a shortcut (`pname`). */
  placeName: string | null;
  hour: number;
  day: number;
  cell: string | null;
  scenario: string | null;
  origin: Place | null;
  destination: Place | null;
  run: boolean;
  routeIndex: number | null;
  segmentIndex: number | null;
  present: boolean;
  /** Place B of the Explore compare tool, as `cmp=lat,lon`. */
  compare: Point | null;
  /** True while the next map click should set place B instead of moving the point. */
  picking: boolean;
  /**
   * What the sun, the night side and the light/dark map follow: the real clock
   * (`live`, the default), or the time-dock hour (`dock`, via `sun=dock`).
   */
  sunMode: "live" | "dock";
};

function pointParam(value: string | null): Point | null {
  if (!value) return null;
  const [lat, lon] = value.split(",").map(Number);
  if (lat === undefined || lon === undefined) return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon };
}

export function formatPointParam(point: Point): string {
  return `${point.lat.toFixed(5)},${point.lon.toFixed(5)}`;
}

function numberParam(value: string | null, fallback: number): number {
  if (value === null || value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function intParam(value: string | null, fallback: number, min: number, max: number): number {
  const parsed = Math.round(numberParam(value, fallback));
  return Math.min(max, Math.max(min, parsed));
}

function placeParam(
  prefix: "o" | "d",
  params: URLSearchParams,
): Place | null {
  const lat = params.get(`${prefix}lat`);
  const lon = params.get(`${prefix}lon`);
  const name = params.get(`${prefix}name`);
  if (!lat || !lon || !name) return null;
  const parsedLat = Number(lat);
  const parsedLon = Number(lon);
  if (!Number.isFinite(parsedLat) || !Number.isFinite(parsedLon)) return null;
  return {
    id: `${prefix}-url`,
    name,
    detail: params.get(`${prefix}detail`) ?? "",
    lat: parsedLat,
    lon: parsedLon,
  };
}

function panelFromPath(pathname: string): Panel {
  if (pathname.startsWith("/method")) return "method";
  if (pathname.startsWith("/plan")) return "plan";
  return "explore";
}

export function useUrlState() {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();

  const state = useMemo<UrlState>(() => {
    const panel = panelFromPath(location.pathname);
    return {
      panel,
      lat: numberParam(searchParams.get("lat"), NYC_CENTER.lat),
      lon: numberParam(searchParams.get("lon"), NYC_CENTER.lon),
      // The default point is Times Square; name it so a fresh load says so.
      placeName:
        searchParams.get("pname") ?? (searchParams.has("lat") ? null : "Times Square"),
      hour: intParam(searchParams.get("hour"), DEFAULT_HOUR, 0, 23),
      day: intParam(searchParams.get("day"), DEFAULT_DAY, 0, 6),
      cell: searchParams.get("cell"),
      scenario: searchParams.get("scenario"),
      origin: placeParam("o", searchParams),
      destination: placeParam("d", searchParams),
      run: searchParams.get("run") === "1",
      routeIndex: searchParams.has("route")
        ? intParam(searchParams.get("route"), 0, 0, 10)
        : null,
      segmentIndex: searchParams.has("seg")
        ? intParam(searchParams.get("seg"), 0, 0, 500)
        : null,
      present: searchParams.get("present") === "1",
      compare: pointParam(searchParams.get("cmp")),
      picking: searchParams.get("pick") === "1",
      sunMode: searchParams.get("sun") === "dock" ? "dock" : "live",
    };
  }, [location.pathname, searchParams]);

  // React Router hands a functional `setSearchParams` the params captured at the
  // last render, so two updates in one event handler (select a route, then clear
  // the segment) each start from the same stale copy and the last one wins,
  // silently undoing the first. Track the freshest params ourselves so updates
  // made in the same tick compose.
  const paramsRef = useRef(searchParams);
  useEffect(() => {
    paramsRef.current = searchParams;
  }, [searchParams]);

  const update = useCallback(
    (patch: Partial<Record<string, string | number | boolean | null | undefined>>) => {
      const next = new URLSearchParams(paramsRef.current);
      for (const [key, raw] of Object.entries(patch)) {
        if (raw === null || raw === undefined || raw === "") {
          next.delete(key);
        } else {
          next.set(key, String(raw));
        }
      }
      paramsRef.current = next;
      setSearchParams(next, { replace: true });
    },
    [setSearchParams],
  );

  const goTo = useCallback(
    (
      panel: Panel,
      patch: Partial<Record<string, string | number | boolean | null | undefined>> = {},
      { replace = true }: { replace?: boolean } = {},
    ) => {
      const next = new URLSearchParams(paramsRef.current);
      for (const [key, raw] of Object.entries(patch)) {
        if (raw === null || raw === undefined || raw === "") {
          next.delete(key);
        } else {
          next.set(key, String(raw));
        }
      }
      paramsRef.current = next;
      const search = next.toString();
      navigate(`/${panel}${search ? `?${search}` : ""}`, { replace });
    },
    [navigate],
  );

  const setPanel = useCallback(
    (panel: Panel) => {
      if (panel === state.panel) return;
      const search = paramsRef.current.toString();
      navigate(`/${panel}${search ? `?${search}` : ""}`);
    },
    [navigate, state.panel],
  );

  /**
   * Select a point. `name` is kept only when the point came with one (search,
   * a shortcut); a map click clears it and the API names the place instead.
   */
  const setPoint = useCallback(
    (point: Point, cell?: string | null, name?: string | null) => {
      update({
        lat: point.lat.toFixed(5),
        lon: point.lon.toFixed(5),
        cell: cell ?? null,
        pname: name ?? null,
      });
    },
    [update],
  );

  const loadScenario = useCallback(
    (run = true, tripId?: string) => {
      const trip = DEMO_TRIPS.find((item) => item.id === tripId) ?? DEMO_TRIPS[0]!;
      goTo("plan", {
        olat: trip.origin.lat.toFixed(5),
        olon: trip.origin.lon.toFixed(5),
        oname: trip.origin.name,
        odetail: trip.origin.detail,
        dlat: trip.destination.lat.toFixed(5),
        dlon: trip.destination.lon.toFixed(5),
        dname: trip.destination.name,
        ddetail: trip.destination.detail,
        lat: trip.origin.lat.toFixed(5),
        lon: trip.origin.lon.toFixed(5),
        pname: trip.origin.name,
        cell: null,
        hour: trip.hour,
        day: trip.day,
        scenario: trip.id,
        run: run ? "1" : null,
        route: null,
        seg: null,
      });
    },
    [goTo],
  );

  const setOrigin = useCallback(
    (place: Place | null) => {
      update({
        olat: place ? place.lat.toFixed(5) : null,
        olon: place ? place.lon.toFixed(5) : null,
        oname: place?.name ?? null,
        odetail: place?.detail ?? null,
        run: null,
        route: null,
        seg: null,
      });
    },
    [update],
  );

  const setDestination = useCallback(
    (place: Place | null) => {
      update({
        dlat: place ? place.lat.toFixed(5) : null,
        dlon: place ? place.lon.toFixed(5) : null,
        dname: place?.name ?? null,
        ddetail: place?.detail ?? null,
        run: null,
        route: null,
        seg: null,
      });
    },
    [update],
  );

  /** Swap both endpoints in one URL write; two chained updates clobber each other. */
  const swapEndpoints = useCallback(
    (from: Place | null, to: Place | null) => {
      update({
        olat: to ? to.lat.toFixed(5) : null,
        olon: to ? to.lon.toFixed(5) : null,
        oname: to?.name ?? null,
        odetail: to?.detail ?? null,
        dlat: from ? from.lat.toFixed(5) : null,
        dlon: from ? from.lon.toFixed(5) : null,
        dname: from?.name ?? null,
        ddetail: from?.detail ?? null,
        run: null,
        route: null,
        seg: null,
      });
    },
    [update],
  );

  return {
    state,
    update,
    swapEndpoints,
    goTo,
    setPanel,
    setPoint,
    loadScenario,
    setOrigin,
    setDestination,
  };
}
