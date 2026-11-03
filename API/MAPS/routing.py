"""Multi-provider routing fallback for the S.O.B.E.R backend.

``/safe_route`` needs a drivable route between two coordinates. The backend used
to call the public OSRM demo server directly, so one upstream outage turned
every route request into a 502. This module walks an ordered chain of routing
providers and returns the first usable answer.

Chain order (a provider is skipped when it is not configured):

    1. osrm-public       OSRM_BASE_URL (default http://router.project-osrm.org)
    2. osrm-fossgis      https://routing.openstreetmap.de/routed-car  (keyless)
    3. valhalla-fossgis  https://valhalla1.openstreetmap.de/route      (keyless)
    4. brouter           https://brouter.de/brouter                    (keyless)
    5. graphhopper       https://graphhopper.com/api/1/route  (GRAPHHOPPER_API_KEY)
    6. ors               https://api.openrouteservice.org/...  (ORS_API_KEY)

Every provider is normalised to the OSRM route shape, so callers do not need to
care which one answered::

    {"geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},
     "distance": <metres, float>,
     "duration": <seconds, float>}

Honesty rule
------------
A route is only as trustworthy as its provenance. OSRM, Valhalla, BRouter,
GraphHopper and ORS disagree about turn restrictions, ferries, and one-way
roads, and the risk score the API attaches to a route is computed from that
specific geometry. Presenting a fallback geometry as if it came from OSRM would
be a quiet lie about where the numbers came from. So ``get_routes`` always
returns the id of the provider that actually served the route, and the caller is
expected to surface it (for example in ``/safe_route`` and ``/health``) instead
of dropping it. When nothing works the id is ``"none"`` and the route list is
empty - never a fabricated or cached substitute.

Reliability behaviour
---------------------
* Per-provider timeout: ``ROUTING_TIMEOUT_S`` (default 6s), overridable per call
  through the ``timeout`` argument.
* Total budget: ``ROUTING_TOTAL_DEADLINE_S`` (default 20s). No new provider is
  started once the budget is exhausted, and a provider that does start is only
  given the budget that is actually left.
* Circuit breaker: ``ROUTING_FAILURE_THRESHOLD`` (default 3) consecutive
  failures open a provider's circuit for ``ROUTING_COOLDOWN_S`` (default 300s),
  so a dead upstream cannot cost every request a full timeout. The count resets
  on success. After the cooldown one trial request is allowed through.
* Thread safe: breaker and attempt state are guarded by ``threading.Lock``
  because the Flask app scores routes from a ``ThreadPoolExecutor``.
* Never raises: every failure path becomes ``([], "none")``.

Only ``requests`` (already a dependency) and the standard library are used.

Logging
-------
One line per attempt, on this module's logger::

    routing: <provider> ok <ms>ms
    routing: <provider> failed: <error>

Successes are logged at INFO and failures at WARNING, so failures are visible by
default while successes need ``logging.basicConfig(level=logging.INFO)`` (or the
equivalent for the ``routing`` logger). API keys never reach the log: error
strings are scrubbed before logging, which also covers ``requests`` exceptions
that embed the full request URL.

Environment
-----------
``OSRM_BASE_URL``, ``ROUTING_TIMEOUT_S``, ``ROUTING_TOTAL_DEADLINE_S``,
``ROUTING_FAILURE_THRESHOLD``, ``ROUTING_COOLDOWN_S``, ``GRAPHHOPPER_API_KEY``,
``ORS_API_KEY``. All are read per call, so changing one takes effect without a
restart and without re-importing the module.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

__all__ = ["get_routes", "describe_providers", "last_attempts"]

LOG = logging.getLogger(__name__)

USER_AGENT = "SOBER-Project/1.0 (routing fallback)"

DEFAULT_OSRM_BASE_URL = "http://router.project-osrm.org"
FOSSGIS_OSRM_BASE_URL = "https://routing.openstreetmap.de/routed-car"
VALHALLA_URL = "https://valhalla1.openstreetmap.de/route"
BROUTER_URL = "https://brouter.de/brouter"
GRAPHHOPPER_URL = "https://graphhopper.com/api/1/route"
ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"

DEFAULT_TIMEOUT_S = 6.0
DEFAULT_TOTAL_DEADLINE_S = 20.0
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_S = 300.0

# Do not start a provider with less budget than this - a request that cannot
# possibly finish is worse than a clean "skipped" entry in the diagnostics.
MIN_ATTEMPT_BUDGET_S = 0.25
# A single timeout value applies to connect *and* read in requests, so cap the
# connect half to keep one dead host from eating the whole per-provider budget.
MAX_CONNECT_TIMEOUT_S = 3.5
MAX_ERROR_CHARS = 300


class RoutingError(Exception):
    """Internal: a provider could not produce a valid route."""


_Point = tuple[float, float]
_Fetch = Callable[[_Point, _Point, float], list[dict]]


@dataclass(frozen=True)
class _Provider:
    """One entry in the fallback chain."""

    id: str
    enabled: bool
    reason: str
    fetch: _Fetch | None
    secrets: tuple[str, ...] = ()


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float | None = None


# ---------------------------------------------------------------------------
# Shared state (the app calls get_routes from a ThreadPoolExecutor)
# ---------------------------------------------------------------------------

_BREAKER_LOCK = threading.Lock()
_BREAKER_STATE: dict[str, _BreakerState] = {}

_ATTEMPTS_LOCK = threading.Lock()
_LAST_ATTEMPTS: list[dict] = []

_LOCAL = threading.local()


def _session() -> requests.Session:
    """Return a per-thread Session so connections are reused safely.

    ``requests.Session`` is not documented as thread safe, so each worker thread
    gets its own; that keeps the connection pool useful without sharing mutable
    state across threads.
    """
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        _LOCAL.session = session
    return session


# ---------------------------------------------------------------------------
# Env parsing (never raises: bad config degrades to the default)
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        LOG.warning("routing: ignoring invalid %s=%r, using %s", name, raw, default)
        return default
    if not math.isfinite(value) or value <= 0:
        LOG.warning("routing: ignoring non-positive %s=%r, using %s", name, raw, default)
        return default
    return value


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        LOG.warning("routing: ignoring invalid %s=%r, using %s", name, raw, default)
        return default
    if value < minimum:
        LOG.warning("routing: ignoring %s=%r (minimum %s)", name, raw, minimum)
        return default
    return value


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _finite_non_negative(value: Any) -> float | None:
    """Coerce to a finite, non-negative float or return None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _clean_coordinates(geometry: Any) -> list[list[float]] | None:
    """Validate a GeoJSON LineString and return 2D ``[lon, lat]`` pairs.

    Rejects anything that is not a LineString with at least two finite, in-range
    coordinates. Extra ordinates (BRouter adds elevation) are dropped so every
    consumer sees the same two-element shape.
    """
    if not isinstance(geometry, dict):
        return None
    kind = geometry.get("type")
    if kind is not None and kind != "LineString":
        return None
    raw = geometry.get("coordinates")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None

    cleaned: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return None
        try:
            lon = float(item[0])
            lat = float(item[1])
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return None
        if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            return None
        cleaned.append([lon, lat])
    return cleaned


def _build_route(geometry: Any, distance: Any, duration: Any) -> dict | None:
    """Normalise one route to the OSRM shape, or None when it is unusable."""
    coordinates = _clean_coordinates(geometry)
    if coordinates is None:
        return None
    metres = _finite_non_negative(distance)
    seconds = _finite_non_negative(duration)
    if metres is None or seconds is None:
        return None
    return {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "distance": metres,
        "duration": seconds,
    }


def _require_ok(response: requests.Response, label: str) -> None:
    if response.status_code != 200:
        snippet = " ".join((response.text or "").split())[:140]
        raise RoutingError(f"HTTP {response.status_code}: {snippet}")


def _json(response: requests.Response, label: str) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise RoutingError(f"{label} returned non-JSON") from None
    if not isinstance(payload, dict):
        raise RoutingError(f"{label} returned {type(payload).__name__}, expected object")
    return payload


def _timeouts(budget: float) -> tuple[float, float]:
    """requests timeout pair: (connect, read), both bounded by the budget."""
    connect = min(MAX_CONNECT_TIMEOUT_S, budget)
    return (max(0.05, connect), max(0.05, budget))


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _decode_polyline(encoded: Any, precision: int = 6) -> list[list[float]]:
    """Decode a Google/Valhalla encoded polyline into ``[lon, lat]`` pairs.

    Valhalla uses precision 6 (1e-6 degrees), unlike Google's precision 5, so
    the default here is 6. Decoding with the wrong precision yields coordinates
    hundreds of degrees out of range, which is why precision is explicit.
    """
    if not isinstance(encoded, str) or not encoded:
        raise RoutingError("missing encoded shape")

    factor = float(10**precision)
    coordinates: list[list[float]] = []
    index = 0
    lat = 0
    lon = 0
    length = len(encoded)

    while index < length:
        for axis in range(2):
            shift = 0
            result = 0
            while True:
                if index >= length:
                    raise RoutingError("truncated encoded shape")
                chunk = ord(encoded[index]) - 63
                index += 1
                if chunk < 0:
                    raise RoutingError("invalid character in encoded shape")
                result |= (chunk & 0x1F) << shift
                shift += 5
                if chunk < 0x20:
                    break
                if shift > 60:
                    raise RoutingError("encoded shape delta overflow")
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coordinates.append([lon / factor, lat / factor])

    return coordinates


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def _osrm_endpoint(base_url: str, origin: _Point, dest: _Point) -> str:
    base = base_url.rstrip("/")
    return (
        f"{base}/route/v1/driving/"
        f"{origin[1]:.6f},{origin[0]:.6f};{dest[1]:.6f},{dest[0]:.6f}"
    )


def _fetch_osrm(base_url: str, origin: _Point, dest: _Point, budget: float) -> list[dict]:
    response = _session().get(
        _osrm_endpoint(base_url, origin, dest),
        params={
            "alternatives": 3,
            "geometries": "geojson",
            "overview": "full",
            # Street names, junctions and per-road speeds: free with the route,
            # and what scores a route outside the NYC model area.
            "steps": "true",
            "annotations": "speed",
        },
        timeout=_timeouts(budget),
    )
    return _osrm_routes(response)


def _fetch_osrm_via(
    base_url: str, origin: _Point, via: _Point, dest: _Point, budget: float
) -> list[dict]:
    """One OSRM route forced through ``via``.

    OSRM's default ``continue_straight`` for cars forbids a U-turn at a waypoint,
    which is what stops a waypoint beside a dead end from producing a spur.
    """
    base = base_url.rstrip("/")
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in (origin, via, dest))
    response = _session().get(
        f"{base}/route/v1/driving/{coordinates}",
        params={
            "alternatives": "false",
            "geometries": "geojson",
            "overview": "full",
            "steps": "true",
            "annotations": "speed",
        },
        timeout=_timeouts(budget),
    )
    return _osrm_routes(response)


def _osrm_routes(response: requests.Response) -> list[dict]:
    _require_ok(response, "OSRM")
    payload = _json(response, "OSRM")

    code = payload.get("code")
    if code is not None and code != "Ok":
        raise RoutingError(f"OSRM code={code}")

    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list):
        raise RoutingError("OSRM response has no routes list")

    routes = []
    for raw in raw_routes:
        if isinstance(raw, dict):
            route = _build_route(raw.get("geometry"), raw.get("distance"), raw.get("duration"))
            if route is not None:
                route.update(_osrm_details(raw, len(route["geometry"]["coordinates"])))
                routes.append(route)
    if not routes:
        raise RoutingError("OSRM returned no valid routes")
    return routes


_PLAIN_STEPS = {"depart", "arrive", "new name", "notification", "continue", "fork", "off ramp"}


def _maneuver(step: dict) -> dict | None:
    """The manoeuvre at the start of an OSRM step, if it can add crash risk.

    Turns are reported as ``{"kind": "turn", "turn": "left" | "right"}``. Which
    of them crosses oncoming traffic depends on the side of the road, and the
    public OSRM servers report right-hand traffic everywhere (London and Tokyo
    included), so the scorer decides that from the country instead.
    """
    maneuver = step.get("maneuver") or {}
    location = maneuver.get("location")
    if not (isinstance(location, list) and len(location) == 2):
        return None
    kind = str(maneuver.get("type") or "")
    modifier = str(maneuver.get("modifier") or "")
    turn = None
    if modifier == "uturn" and kind not in ("depart", "arrive"):
        found = "uturn"
    elif kind in ("roundabout", "rotary", "roundabout turn"):
        found = "roundabout"
    elif kind in ("merge", "on ramp"):
        found = "merge"
    elif kind in _PLAIN_STEPS:
        return None
    elif modifier in ("left", "sharp left", "right", "sharp right"):
        found, turn = "turn", modifier.split()[-1]
    else:
        return None
    item = {
        "lat": float(location[1]),
        "lon": float(location[0]),
        "kind": found,
        "street": str(step.get("name") or step.get("ref") or ""),
    }
    if turn:
        item["turn"] = turn
    return item


def _osrm_details(raw: dict, coordinate_count: int) -> dict:
    """Street names, junctions, manoeuvres and speeds from an OSRM route.

    ``steps``: ``[{"name", "distance"}]`` in driving order, for naming stretches.
    ``junctions``: ``[[lat, lon]]`` of every intersection the route passes.
    ``maneuvers``: turns and merges that add risk (see ``_maneuver``).
    ``speeds_kmh``: one speed per pair of consecutive coordinates. Dropped if
    it does not line up with the geometry (a coordinate was cleaned out).
    """
    steps, junctions, speeds, maneuvers = [], [], [], []
    for leg in raw.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        for step in leg.get("steps") or []:
            if not isinstance(step, dict):
                continue
            name = step.get("name") or step.get("ref") or ""
            distance = _finite_non_negative(step.get("distance"))
            if distance:
                steps.append({"name": str(name), "distance": distance})
            found = _maneuver(step)
            if found:
                maneuvers.append(found)
            for crossing in step.get("intersections") or []:
                location = (crossing or {}).get("location")
                if isinstance(location, list) and len(location) == 2:
                    junctions.append([float(location[1]), float(location[0])])
        for speed in (leg.get("annotation") or {}).get("speed") or []:
            value = _finite_non_negative(speed)
            speeds.append(round(value * 3.6, 1) if value is not None else None)
    details: dict = {}
    if steps:
        details["steps"] = steps
    if junctions:
        details["junctions"] = junctions
    if maneuvers:
        details["maneuvers"] = maneuvers
    if speeds and len(speeds) == coordinate_count - 1:
        details["speeds_kmh"] = speeds
    return details


def _fetch_valhalla(origin: _Point, dest: _Point, budget: float) -> list[dict]:
    body = {
        "locations": [
            {"lat": origin[0], "lon": origin[1]},
            {"lat": dest[0], "lon": dest[1]},
        ],
        "costing": "auto",
        "directions_options": {"units": "kilometers"},
    }
    response = _session().post(VALHALLA_URL, json=body, timeout=_timeouts(budget))
    _require_ok(response, "Valhalla")
    payload = _json(response, "Valhalla")

    trip = payload.get("trip")
    if not isinstance(trip, dict):
        raise RoutingError("Valhalla response has no trip")

    legs = trip.get("legs")
    if not isinstance(legs, list) or not legs:
        raise RoutingError("Valhalla trip has no legs")

    coordinates: list[list[float]] = []
    length_km = 0.0
    duration_s = 0.0
    for leg in legs:
        if not isinstance(leg, dict):
            raise RoutingError("Valhalla leg is not an object")
        leg_coordinates = _decode_polyline(leg.get("shape"), precision=6)
        if coordinates and leg_coordinates and coordinates[-1] == leg_coordinates[0]:
            leg_coordinates = leg_coordinates[1:]
        coordinates.extend(leg_coordinates)

        leg_summary = leg.get("summary")
        if isinstance(leg_summary, dict):
            leg_length = _finite_non_negative(leg_summary.get("length"))
            leg_time = _finite_non_negative(leg_summary.get("time"))
            if leg_length is not None:
                length_km += leg_length
            if leg_time is not None:
                duration_s += leg_time

    summary = trip.get("summary")
    if isinstance(summary, dict):
        summary_length = _finite_non_negative(summary.get("length"))
        summary_time = _finite_non_negative(summary.get("time"))
        if summary_length is not None:
            length_km = summary_length
        if summary_time is not None:
            duration_s = summary_time

    geometry = {"type": "LineString", "coordinates": coordinates}
    route = _build_route(geometry, length_km * 1000.0, duration_s)
    if route is None:
        raise RoutingError("Valhalla returned no valid route")
    return [route]


def _fetch_brouter(origin: _Point, dest: _Point, budget: float) -> list[dict]:
    params = {
        "lonlats": f"{origin[1]:.6f},{origin[0]:.6f}|{dest[1]:.6f},{dest[0]:.6f}",
        "profile": "car-fast",
        "format": "geojson",
        "alternativeidx": 0,
    }
    response = _session().get(BROUTER_URL, params=params, timeout=_timeouts(budget))
    _require_ok(response, "BRouter")
    payload = _json(response, "BRouter")

    features = payload.get("features")
    if not isinstance(features, list) or not features:
        detail = ""
        messages = payload.get("properties")
        if isinstance(messages, dict) and messages.get("messages"):
            detail = f": {str(messages['messages'])[:120]}"
        raise RoutingError(f"BRouter returned no features{detail}")

    feature = features[0]
    if not isinstance(feature, dict):
        raise RoutingError("BRouter feature is not an object")

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    route = _build_route(
        feature.get("geometry"),
        properties.get("track-length"),
        properties.get("total-time"),
    )
    if route is None:
        raise RoutingError("BRouter returned an invalid route")
    return [route]


def _fetch_graphhopper(
    origin: _Point, dest: _Point, budget: float, api_key: str
) -> list[dict]:
    params = [
        ("point", f"{origin[0]:.6f},{origin[1]:.6f}"),
        ("point", f"{dest[0]:.6f},{dest[1]:.6f}"),
        ("profile", "car"),
        ("points_encoded", "false"),
        ("key", api_key),
    ]
    response = _session().get(GRAPHHOPPER_URL, params=params, timeout=_timeouts(budget))
    _require_ok(response, "GraphHopper")
    payload = _json(response, "GraphHopper")

    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise RoutingError("GraphHopper returned no paths")

    path = paths[0]
    if not isinstance(path, dict):
        raise RoutingError("GraphHopper path is not an object")

    points = path.get("points")
    geometry = points if isinstance(points, dict) else None

    # GraphHopper reports time in milliseconds, the module contract is seconds.
    time_ms = _finite_non_negative(path.get("time"))
    duration_s = time_ms / 1000.0 if time_ms is not None else None

    route = _build_route(geometry, path.get("distance"), duration_s)
    if route is None:
        raise RoutingError("GraphHopper returned an invalid route")
    return [route]


def _fetch_ors(origin: _Point, dest: _Point, budget: float, api_key: str) -> list[dict]:
    body = {"coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]]}
    headers = {
        "Authorization": api_key,
        "Accept": "application/geo+json, application/json",
    }
    response = _session().post(
        ORS_URL, json=body, headers=headers, timeout=_timeouts(budget)
    )
    _require_ok(response, "ORS")
    payload = _json(response, "ORS")

    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise RoutingError("ORS returned no features")

    feature = features[0]
    if not isinstance(feature, dict):
        raise RoutingError("ORS feature is not an object")

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    summary = properties.get("summary")
    distance = duration = None
    if isinstance(summary, dict):
        distance = summary.get("distance")
        duration = summary.get("duration")

    if distance is None or duration is None:
        # Some ORS responses only carry per-segment values.
        segments = properties.get("segments")
        if isinstance(segments, list) and segments:
            distance = sum(
                _finite_non_negative(segment.get("distance")) or 0.0
                for segment in segments
                if isinstance(segment, dict)
            )
            duration = sum(
                _finite_non_negative(segment.get("duration")) or 0.0
                for segment in segments
                if isinstance(segment, dict)
            )

    route = _build_route(feature.get("geometry"), distance, duration)
    if route is None:
        raise RoutingError("ORS returned an invalid route")
    return [route]


# ---------------------------------------------------------------------------
# Chain construction
# ---------------------------------------------------------------------------


def _provider_chain() -> list[_Provider]:
    """Build the chain from the current environment, in fallback order."""
    chain: list[_Provider] = []

    raw_base = os.environ.get("OSRM_BASE_URL")
    base_url = DEFAULT_OSRM_BASE_URL if raw_base is None else raw_base.strip()
    if base_url:
        chain.append(
            _Provider(
                id="osrm-public",
                enabled=True,
                reason=f"OSRM_BASE_URL={base_url}",
                fetch=lambda o, d, b, url=base_url: _fetch_osrm(url, o, d, b),
            )
        )
    else:
        chain.append(
            _Provider("osrm-public", False, "OSRM_BASE_URL is empty", None)
        )

    chain.append(
        _Provider(
            id="osrm-fossgis",
            enabled=True,
            reason=f"{FOSSGIS_OSRM_BASE_URL} (keyless)",
            fetch=lambda o, d, b: _fetch_osrm(FOSSGIS_OSRM_BASE_URL, o, d, b),
        )
    )

    chain.append(
        _Provider(
            id="valhalla-fossgis",
            enabled=True,
            reason=f"{VALHALLA_URL} (keyless, polyline precision 6)",
            fetch=_fetch_valhalla,
        )
    )

    chain.append(
        _Provider(
            id="brouter",
            enabled=True,
            reason=f"{BROUTER_URL} profile=car-fast (keyless)",
            fetch=_fetch_brouter,
        )
    )

    graphhopper_key = (os.environ.get("GRAPHHOPPER_API_KEY") or "").strip()
    if graphhopper_key:
        chain.append(
            _Provider(
                id="graphhopper",
                enabled=True,
                reason="GRAPHHOPPER_API_KEY is set",
                fetch=lambda o, d, b, key=graphhopper_key: _fetch_graphhopper(o, d, b, key),
                secrets=(graphhopper_key,),
            )
        )
    else:
        chain.append(
            _Provider("graphhopper", False, "GRAPHHOPPER_API_KEY not set", None)
        )

    ors_key = (os.environ.get("ORS_API_KEY") or "").strip()
    if ors_key:
        chain.append(
            _Provider(
                id="ors",
                enabled=True,
                reason="ORS_API_KEY is set",
                fetch=lambda o, d, b, key=ors_key: _fetch_ors(o, d, b, key),
                secrets=(ors_key,),
            )
        )
    else:
        chain.append(_Provider("ors", False, "ORS_API_KEY not set", None))

    return chain


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def _breaker_allow(provider_id: str, cooldown: float, now: float) -> tuple[bool, float]:
    """Return (allowed, seconds_until_retry).

    A provider whose cooldown has elapsed is allowed one trial request; if that
    trial fails the caller refreshes the timestamp, so the cooldown restarts.
    """
    with _BREAKER_LOCK:
        state = _BREAKER_STATE.get(provider_id)
        if state is None or state.opened_at is None:
            return True, 0.0
        remaining = cooldown - (now - state.opened_at)
        if remaining <= 0.0:
            return True, 0.0
        return False, remaining


def _breaker_record_success(provider_id: str) -> None:
    with _BREAKER_LOCK:
        state = _BREAKER_STATE.get(provider_id)
        if state is not None:
            state.failures = 0
            state.opened_at = None


def _breaker_record_failure(provider_id: str, threshold: int, now: float) -> bool:
    """Count a failure and report whether the circuit is now open."""
    with _BREAKER_LOCK:
        state = _BREAKER_STATE.setdefault(provider_id, _BreakerState())
        state.failures += 1
        if state.failures >= threshold:
            state.opened_at = now
            return True
        return False


def _breaker_snapshot() -> dict[str, dict]:
    with _BREAKER_LOCK:
        return {
            provider_id: {"failures": state.failures, "opened_at": state.opened_at}
            for provider_id, state in _BREAKER_STATE.items()
        }


# ---------------------------------------------------------------------------
# Attempt diagnostics
# ---------------------------------------------------------------------------


def _record_attempts(attempts: list[dict]) -> None:
    global _LAST_ATTEMPTS
    with _ATTEMPTS_LOCK:
        _LAST_ATTEMPTS = [dict(item) for item in attempts]


def last_attempts() -> list[dict]:
    """Return the attempt log of the most recent ``get_routes`` call.

    Each entry is ``{"provider": id, "ok": bool, "error": str | None, "ms": int}``
    in attempt order. Providers that were skipped without a request (circuit
    open, or the total deadline already spent) appear with ``ok=False``,
    ``ms=0`` and an ``error`` starting with ``"skipped:"`` so the diagnostics
    explain the silence. Providers that are not configured do not appear at all.
    """
    with _ATTEMPTS_LOCK:
        return [dict(item) for item in _LAST_ATTEMPTS]


def describe_providers() -> list[dict]:
    """Describe the configured chain in fallback order.

    Each entry has ``id``, ``enabled`` (is it configured?) and ``reason``.
    ``circuit`` is an extra diagnostic field: it reports breaker state without
    conflating it with configuration.
    """
    snapshot = _breaker_snapshot()
    now = time.monotonic()
    cooldown = _env_float("ROUTING_COOLDOWN_S", DEFAULT_COOLDOWN_S)
    threshold = _env_int("ROUTING_FAILURE_THRESHOLD", DEFAULT_FAILURE_THRESHOLD)

    described = []
    for provider in _provider_chain():
        state = snapshot.get(provider.id)
        circuit = "closed"
        if state and state["opened_at"] is not None:
            remaining = cooldown - (now - state["opened_at"])
            if remaining > 0:
                circuit = f"open, retry in {remaining:.0f}s"
            else:
                circuit = "half-open, trial allowed"
        elif state and state["failures"]:
            circuit = f"closed, {state['failures']}/{threshold} failures"

        described.append(
            {
                "id": provider.id,
                "enabled": provider.enabled,
                "reason": provider.reason,
                "circuit": circuit,
            }
        )
    return described


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _coerce_point(lat: Any, lon: Any) -> _Point:
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        raise RoutingError("coordinates must be numbers") from None
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        raise RoutingError("coordinates must be finite")
    if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
        raise RoutingError("coordinates out of range")
    return (lat_f, lon_f)


def _run_chain(
    origin: _Point,
    dest: _Point,
    timeout: float | None,
    attempts: list[dict],
) -> tuple[list[dict], str]:
    if timeout is None:
        per_provider = _env_float("ROUTING_TIMEOUT_S", DEFAULT_TIMEOUT_S)
    else:
        try:
            per_provider = float(timeout)
        except (TypeError, ValueError):
            LOG.warning("routing: ignoring invalid timeout=%r, using env/default", timeout)
            per_provider = _env_float("ROUTING_TIMEOUT_S", DEFAULT_TIMEOUT_S)
        else:
            if not math.isfinite(per_provider) or per_provider <= 0:
                LOG.warning("routing: ignoring invalid timeout=%r, using env/default", timeout)
                per_provider = _env_float("ROUTING_TIMEOUT_S", DEFAULT_TIMEOUT_S)

    total_budget = _env_float("ROUTING_TOTAL_DEADLINE_S", DEFAULT_TOTAL_DEADLINE_S)
    threshold = _env_int("ROUTING_FAILURE_THRESHOLD", DEFAULT_FAILURE_THRESHOLD)
    cooldown = _env_float("ROUTING_COOLDOWN_S", DEFAULT_COOLDOWN_S)

    deadline = time.monotonic() + total_budget

    for provider in _provider_chain():
        if not provider.enabled or provider.fetch is None:
            continue

        now = time.monotonic()
        remaining = deadline - now
        if remaining <= MIN_ATTEMPT_BUDGET_S:
            attempts.append(
                {
                    "provider": provider.id,
                    "ok": False,
                    "error": f"skipped: total deadline {total_budget:.1f}s exceeded",
                    "ms": 0,
                }
            )
            LOG.debug("routing: %s skipped (total deadline spent)", provider.id)
            continue

        allowed, retry_in = _breaker_allow(provider.id, cooldown, now)
        if not allowed:
            attempts.append(
                {
                    "provider": provider.id,
                    "ok": False,
                    "error": f"skipped: circuit open, retry in {retry_in:.0f}s",
                    "ms": 0,
                }
            )
            LOG.debug("routing: %s skipped (circuit open)", provider.id)
            continue

        budget = min(per_provider, remaining)
        started = time.monotonic()
        try:
            routes = provider.fetch(origin, dest, budget)
            if not routes:
                raise RoutingError("provider returned no routes")
        except Exception as exc:  # noqa: BLE001 - every failure must fall through
            elapsed_ms = int(round((time.monotonic() - started) * 1000))
            detail = _scrub(str(exc) or exc.__class__.__name__, provider.secrets)
            detail = " ".join(detail.split())[:MAX_ERROR_CHARS]
            attempts.append(
                {"provider": provider.id, "ok": False, "error": detail, "ms": elapsed_ms}
            )
            opened = _breaker_record_failure(provider.id, threshold, time.monotonic())
            suffix = " (circuit opened)" if opened else ""
            LOG.warning("routing: %s failed: %s%s", provider.id, detail, suffix)
            continue

        elapsed_ms = int(round((time.monotonic() - started) * 1000))
        attempts.append({"provider": provider.id, "ok": True, "error": None, "ms": elapsed_ms})
        _breaker_record_success(provider.id)
        LOG.info("routing: %s ok %dms", provider.id, elapsed_ms)
        return routes, provider.id

    LOG.warning(
        "routing: all providers failed for %.4f,%.4f -> %.4f,%.4f",
        origin[0],
        origin[1],
        dest[0],
        dest[1],
    )
    return [], "none"


def get_routes(
    origin_lat: Any,
    origin_lon: Any,
    dest_lat: Any,
    dest_lon: Any,
    timeout: float | None = None,
):
    """Return ``(routes, provider_id)`` using the first provider that answers.

    ``routes`` is a list of dicts in the OSRM shape::

        {"geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},
         "distance": <metres, float>, "duration": <seconds, float>}

    ``provider_id`` is the id of the provider that served the route, or
    ``"none"`` when every provider failed.

    ``timeout`` overrides the per-provider budget for this call only; when it is
    omitted ``ROUTING_TIMEOUT_S`` is used. Never raises.
    """
    attempts: list[dict] = []
    routes: list[dict] = []
    provider_id = "none"

    try:
        origin = _coerce_point(origin_lat, origin_lon)
        dest = _coerce_point(dest_lat, dest_lon)
    except RoutingError as exc:
        # A caller bug, not an upstream outage: say so plainly, no traceback.
        LOG.warning("routing: invalid coordinates (%r, %r, %r, %r): %s",
                    origin_lat, origin_lon, dest_lat, dest_lon, exc)
        _record_attempts(attempts)
        return [], "none"
    except Exception:  # noqa: BLE001 - contract: get_routes never raises
        LOG.exception("routing: unexpected error validating coordinates")
        _record_attempts(attempts)
        return [], "none"

    try:
        routes, provider_id = _run_chain(origin, dest, timeout, attempts)
    except Exception:  # noqa: BLE001 - contract: get_routes never raises
        LOG.exception("routing: unexpected error, returning no routes")
        routes, provider_id = [], "none"
    finally:
        _record_attempts(attempts)

    return routes, provider_id


# ---------------------------------------------------------------------------
# Via routes (alternatives forced through a waypoint)
# ---------------------------------------------------------------------------

DEFAULT_VIA_TIMEOUT_S = 5.0


def _via_bases() -> list[tuple[str, str]]:
    """OSRM-compatible endpoints that can route through a waypoint, in order."""
    bases: list[tuple[str, str]] = []
    raw_base = os.environ.get("OSRM_BASE_URL")
    base_url = DEFAULT_OSRM_BASE_URL if raw_base is None else raw_base.strip()
    if base_url:
        bases.append(("osrm-public", base_url))
    bases.append(("osrm-fossgis", FOSSGIS_OSRM_BASE_URL))
    return bases


def get_via_route(
    origin: Any,
    via: Any,
    dest: Any,
    timeout: float | None = None,
) -> tuple[list[dict], str]:
    """Return ``([route], provider_id)`` for ``origin -> via -> dest``.

    ``origin``, ``via`` and ``dest`` are ``(lat, lon)`` pairs. The route is in
    the same OSRM shape ``get_routes`` returns. A via route is optional extra
    information, so this never raises, does not trip a provider's circuit
    breaker, and returns ``([], "none")`` when nothing answers.
    """
    try:
        points = [_coerce_point(p[0], p[1]) for p in (origin, via, dest)]
    except Exception:  # noqa: BLE001 - never raises
        return [], "none"
    budget = timeout if timeout and timeout > 0 else _env_float(
        "ROUTING_VIA_TIMEOUT_S", DEFAULT_VIA_TIMEOUT_S
    )
    cooldown = _env_float("ROUTING_COOLDOWN_S", DEFAULT_COOLDOWN_S)
    for provider_id, base_url in _via_bases():
        allowed, _retry = _breaker_allow(provider_id, cooldown, time.monotonic())
        if not allowed:
            continue
        try:
            return _fetch_osrm_via(base_url, points[0], points[1], points[2], budget), provider_id
        except Exception as exc:  # noqa: BLE001 - fall through to the next base
            LOG.debug("routing: via route via %s failed: %s", provider_id, exc)
    return [], "none"
