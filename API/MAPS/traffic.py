"""Traffic for a trip: which roads to consider, and how long each one takes.

``route_options`` asks TomTom which roads it would take in this traffic. Those go
through the same risk scoring as every other route, so a road chosen for speed
still has to show what it costs in risk.

``route_times`` then times every route. The risk numbers belong to one exact
geometry, so TomTom is handed each route as supporting points and asked how long
that road takes, instead of being left to choose.

The free routers know road speeds, not traffic. So a trip either has traffic
times for all its routes or for none: a card at 24 minutes in traffic beside one
at 21 minutes on empty roads would compare two different things.

The key travels in the URL, because that is TomTom's API. Exceptions from
``requests`` quote the URL, so they are never logged or returned here.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence

import requests

ROUTING_URL = "https://api.tomtom.com/routing/1/calculateRoute/{start}:{end}/json"
SATELLITE_URL = "https://api.tomtom.com/maps/orbis/map-display/tile/satellite/{z}/{x}/{y}.jpg"
MAX_CACHED_TILES = 2000
TIMEOUT_S = 8.0
#: Enough to pin a 2,000 km route to its road; the probe matched to under 0.1%.
MAX_POINTS = 300
#: TomTom's distance must agree with ours, or it timed some other road.
MAX_DISTANCE_GAP = 0.10
#: Roads to ask TomTom for beyond its first choice.
MAX_ALTERNATIVES = 2
#: Live traffic moves in minutes. A forecast for a later hour barely moves, and
#: the free tier is a monthly allowance.
LIVE_TTL_S = 180.0
FORECAST_TTL_S = 6 * 3600.0
#: After a real refusal (a bad key, an exhausted allowance), stop asking for a
#: while. A 429 is not one: it means "wait a second", from about 6 calls a second.
COOLDOWN_S = 300.0
RETRIES = 2
MAX_WAIT_S = 2.0
#: Calls in flight at once, kept under that per-second limit.
WORKERS = 3

_cache: dict[tuple, tuple[float, dict]] = {}
_tiles: dict[tuple[int, int, int], bytes] = {}
_lock = threading.Lock()
_paused_until = 0.0


class TrafficError(Exception):
    """A plain reason for the user. Never holds a URL."""


def configured() -> bool:
    return bool(_key())


def _key() -> str:
    return (os.environ.get("TOMTOM_API_KEY") or "").strip()


def _send(call: Callable[[], requests.Response]) -> requests.Response:
    """Make a call, waiting out "too many requests" as TomTom asks."""
    global _paused_until
    for attempt in range(RETRIES + 1):
        response = call()
        if response.status_code != 429 or attempt == RETRIES:
            break
        try:
            wait = float((getattr(response, "headers", None) or {}).get("Retry-After", 1))
        except ValueError:
            wait = 1.0
        time.sleep(min(wait, MAX_WAIT_S))
    if response.status_code in (401, 403):
        _paused_until = time.time() + COOLDOWN_S
    return response


def depart_for(live: bool, date: str | None, hour: int) -> str:
    return "now" if live else f"{date}T{hour:02d}:00:00"


def route_options(origin: Sequence[float], destination: Sequence[float], depart: str) -> list[dict]:
    """The roads TomTom would take, in the routers' shape. Empty if it cannot say."""
    if not configured() or time.time() < _paused_until:
        return []
    url = ROUTING_URL.format(
        start=f"{origin[0]},{origin[1]}", end=f"{destination[0]},{destination[1]}"
    )
    try:
        response = _send(
            lambda: requests.get(
                url,
                params={
                    "key": _key(),
                    "traffic": "true",
                    "travelMode": "car",
                    "routeType": "fastest",
                    "maxAlternatives": MAX_ALTERNATIVES,
                    "departAt": depart,
                },
                timeout=TIMEOUT_S,
            )
        )
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    try:
        return [_as_raw_route(route) for route in response.json()["routes"]]
    except (ValueError, KeyError, TypeError):
        return []


def _as_raw_route(route: dict) -> dict:
    coordinates = [
        [point["longitude"], point["latitude"]]
        for leg in route["legs"]
        for point in leg["points"]
    ]
    return {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "distance": float(route["summary"]["lengthInMeters"]),
        "duration": float(route["summary"]["travelTimeInSeconds"]),
        "live_reroute": True,
    }


def thin(points: Sequence[Sequence[float]], count: int = MAX_POINTS) -> list[Sequence[float]]:
    """At most ``count`` points in order, keeping both ends."""
    if len(points) <= count:
        return list(points)
    step = (len(points) - 1) / (count - 1)
    return [points[round(index * step)] for index in range(count)]


def _post(points: Sequence[Sequence[float]], depart: str) -> dict:
    """TomTom's summary for one route. Raises ``TrafficError``, never the URL."""
    start, end = points[0], points[-1]
    url = ROUTING_URL.format(start=f"{start[0]},{start[1]}", end=f"{end[0]},{end[1]}")
    try:
        response = _send(
            lambda: requests.post(
                url,
                params={
                    "key": _key(),
                    "traffic": "true",
                    "travelMode": "car",
                    "routeType": "fastest",
                    "computeTravelTimeFor": "all",
                    "departAt": depart,
                },
                json={"supportingPoints": [{"latitude": lat, "longitude": lon} for lat, lon in points]},
                timeout=TIMEOUT_S,
            )
        )
    except requests.RequestException:
        raise TrafficError("The traffic service could not be reached") from None
    if response.status_code in (401, 403):
        raise TrafficError("The traffic service refused the request")
    if response.status_code == 429:
        raise TrafficError("The traffic service is busy, try again in a moment")
    if response.status_code != 200:
        raise TrafficError("The traffic service could not time this route")
    try:
        return response.json()["routes"][0]["summary"]
    except (ValueError, KeyError, IndexError):
        raise TrafficError("The traffic service sent an unreadable answer") from None


def route_guidance(route: dict, depart: str) -> dict:
    """Turn-by-turn instructions for one scored route, in English.

    The risk model knows nothing about turns and the free routers' street names
    are in the local script, so this comes from TomTom, along the same road.
    """
    if not configured():
        return {"available": False, "reason": "Turn directions need the traffic service"}
    if time.time() < _paused_until:
        return {"available": False, "reason": "The traffic service is paused for a few minutes"}
    points = thin(route["geometry"])
    key = ("guidance", tuple(points[0]), tuple(points[-1]), route["distance_km"])
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < FORECAST_TTL_S:
            return cached[1]
    url = ROUTING_URL.format(
        start=f"{points[0][0]},{points[0][1]}", end=f"{points[-1][0]},{points[-1][1]}"
    )
    try:
        response = _send(
            lambda: requests.post(
                url,
                params={
                    "key": _key(),
                    "travelMode": "car",
                    "routeType": "fastest",
                    "departAt": depart,
                    "instructionsType": "text",
                    "language": "en-GB",
                },
                json={"supportingPoints": [{"latitude": lat, "longitude": lon} for lat, lon in points]},
                timeout=TIMEOUT_S,
            )
        )
    except requests.RequestException:
        return {"available": False, "reason": "The traffic service could not be reached"}
    if response.status_code != 200:
        return {"available": False, "reason": "Turn directions are not available for this trip"}
    try:
        found = response.json()["routes"][0]
        answer = {
            "available": True,
            "source": "TomTom",
            "length_m": float(found["summary"]["lengthInMeters"]),
            "instructions": [
                {
                    "offset_m": float(step["routeOffsetInMeters"]),
                    "message": str(step.get("message") or "Continue"),
                    "maneuver": str(step.get("maneuver") or "STRAIGHT"),
                    "street": step.get("street"),
                }
                for step in found["guidance"]["instructions"]
            ],
        }
    except (ValueError, KeyError, IndexError, TypeError):
        return {"available": False, "reason": "Turn directions are not available for this trip"}
    with _lock:
        _cache[key] = (time.time(), answer)
    return answer


def satellite_tile(z: int, x: int, y: int) -> bytes | None:
    """One satellite tile, or None. Fetched here so the key never reaches a browser."""
    if not configured() or time.time() < _paused_until:
        return None
    key = (z, x, y)
    with _lock:
        cached = _tiles.get(key)
    if cached is not None:
        return cached
    try:
        response = _send(
            lambda: requests.get(
                SATELLITE_URL.format(z=z, x=x, y=y),
                params={"key": _key(), "apiVersion": 1},
                timeout=TIMEOUT_S,
            )
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    with _lock:
        if len(_tiles) >= MAX_CACHED_TILES:
            _tiles.clear()
        _tiles[key] = response.content
    return response.content


def _timed(index: int, route: dict, depart: str, post: Callable) -> dict:
    points = thin(route["geometry"])
    summary = post(points, depart)
    distance_km = float(summary["lengthInMeters"]) / 1000.0
    ours = float(route["distance_km"])
    if ours > 0 and abs(distance_km - ours) / ours > MAX_DISTANCE_GAP:
        raise TrafficError("The traffic service timed a different road")
    return {
        "route_index": index,
        "traffic_min": round(float(summary["travelTimeInSeconds"]) / 60.0, 1),
        "no_traffic_min": round(float(summary.get("noTrafficTravelTimeInSeconds") or 0) / 60.0, 1),
        "typical_min": round(float(summary.get("historicTrafficTravelTimeInSeconds") or 0) / 60.0, 1),
        "delay_min": round(float(summary.get("trafficDelayInSeconds") or 0) / 60.0, 1),
    }


def route_times(routes: Sequence[dict], *, date: str | None, hour: int, live: bool) -> dict:
    """Traffic times for every route of a trip, or ``available: False`` and why.

    ``date`` and ``hour`` are local to the trip; ``live`` asks about right now.
    """
    if not configured():
        return {"available": False, "reason": "No traffic service is set up"}
    if time.time() < _paused_until:
        return {"available": False, "reason": "The traffic service is paused for a few minutes"}

    depart = depart_for(live, date, hour)
    key = (depart, tuple((tuple(r["geometry"][0]), tuple(r["geometry"][-1]), r["distance_km"]) for r in routes))
    ttl = LIVE_TTL_S if live else FORECAST_TTL_S
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < ttl:
            return cached[1]

    try:
        with ThreadPoolExecutor(max_workers=min(WORKERS, max(len(routes), 1))) as pool:
            timed = list(
                pool.map(lambda item: _timed(item[0], item[1], depart, _post), enumerate(routes))
            )
    except TrafficError as exc:
        return {"available": False, "reason": str(exc)}

    answer = {
        "available": True,
        "mode": "live" if live else "typical",
        "source": "TomTom",
        "depart": depart,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "routes": timed,
    }
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (time.time(), answer)
    return answer


def reset() -> None:
    """Forget cached answers and any pause. For tests."""
    global _paused_until
    with _lock:
        _cache.clear()
        _tiles.clear()
    _paused_until = 0.0
