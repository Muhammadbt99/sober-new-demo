"""Keyless data providers with fallbacks: weather, places and road type.

Each lookup tries its providers in order, is cached, is bounded by a short
timeout, and is allowed to fail: callers get ``None`` and carry on. Nothing
here may stall a request for long. The previous design leant on Overpass for
live lookups, and when every public Overpass server timed out (as they did on
2026-11-03) "nearby safe stops" hung for 16 seconds per click.

Providers, in fallback order:
  weather  WeatherAPI.com (only if WEATHER_API_KEY is set) -> Open-Meteo -> MET Norway
  places   Photon (komoot, OpenStreetMap search)
  road     Photon reverse geocoding filtered to highways
"""
from __future__ import annotations

import ipaddress
import math
import socket
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Callable

import requests
import requests.adapters

# MET Norway rejects anonymous clients; every provider gets the same identity.
USER_AGENT = "SOBER-safer-routing-demo/2.0 (+https://github.com/sober)"
TIMEOUT_S = 3.0


class Breaker:
    """Stop calling a provider for a while after it fails a few times in a row.

    Without this a slow provider is retried by every caller: a heatmap of 64
    cells once waited 64 x 8 seconds on a throttled geocoder.
    """

    def __init__(self, name: str, failures: int = 3, cooldown_s: float = 60.0):
        self.name = name
        self.failures = failures
        self.cooldown_s = cooldown_s
        self._count = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        return time.time() >= self._open_until

    def success(self) -> None:
        with self._lock:
            self._count = 0

    def failure(self) -> None:
        with self._lock:
            self._count += 1
            if self._count >= self.failures:
                self._open_until = time.time() + self.cooldown_s
                self._count = 0


BREAKERS = {
    name: Breaker(name) for name in ("weatherapi", "open-meteo", "met-norway", "photon", "nominatim")
}


def provider_status() -> dict:
    """``{"photon": "ok" | "paused 42s", ...}`` for /health: which providers are
    being skipped right now because they failed several times in a row."""
    now = time.time()
    return {
        name: "ok" if breaker.allow() else f"paused {max(1, round(breaker._open_until - now))}s"
        for name, breaker in BREAKERS.items()
    }

NOMINATIM = "https://nominatim.openstreetmap.org"
_nominatim_gate = threading.Lock()
_nominatim_last = [0.0]


def _nominatim(path: str, params: dict) -> object | None:
    """One Nominatim call, never more than one per second (its usage policy)."""
    if not BREAKERS["nominatim"].allow():
        return None
    with _nominatim_gate:
        wait_s = 1.05 - (time.time() - _nominatim_last[0])
        if wait_s > 0:
            time.sleep(wait_s)
        _nominatim_last[0] = time.time()
    try:
        response = _http().get(f"{NOMINATIM}/{path}", params={**params, "format": "jsonv2"}, timeout=(CONNECT_S, TIMEOUT_S))
        response.raise_for_status()
        data = response.json()
    except Exception:  # noqa: BLE001 - optional data
        BREAKERS["nominatim"].failure()
        return None
    BREAKERS["nominatim"].success()
    return data

# (connect, read). Some of a provider's addresses may not answer from a given
# network; urllib3 tries each in turn, so a long connect timeout multiplies (a
# 3 s timeout over Nominatim's four addresses once cost 12 s). Fail each fast.
CONNECT_S = 1.5

# One pooled session for every thread, so a good connection is reused instead
# of renegotiated per worker thread.
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32))


def _http() -> requests.Session:
    return _session


class TTLCache:
    """A small thread-safe LRU cache whose entries expire."""

    def __init__(self, ttl_s: float, limit: int = 5000):
        self.ttl_s = ttl_s
        self.limit = limit
        self._items: "OrderedDict[object, tuple[float, object]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            stamp, value = item
            if time.time() - stamp > self.ttl_s:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    def put(self, key, value) -> None:
        with self._lock:
            self._items[key] = (time.time(), value)
            self._items.move_to_end(key)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

_weather_cache = TTLCache(600)
#: The provider that served the last successful reading, for /health.
WEATHER_STATUS = {"provider": None, "last_error": None, "checked_at": None}

# WMO weather interpretation codes (Open-Meteo).
_WMO_RAIN = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
_WMO_SNOW = set(range(71, 78)) | {85, 86}
_WMO_FOG = {45, 48}


def _open_meteo(lat: float, lon: float) -> dict:
    response = _http().get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": round(lat, 3),
            "longitude": round(lon, 3),
            "current": "temperature_2m,precipitation,rain,snowfall,weather_code,"
            "wind_speed_10m,visibility",
            "wind_speed_unit": "kmh",
        },
        timeout=(CONNECT_S, TIMEOUT_S),
    )
    response.raise_for_status()
    current = response.json()["current"]
    code = int(current.get("weather_code") or 0)
    visibility = current.get("visibility")
    return {
        "temperature_c": float(current["temperature_2m"]),
        "precipitation_mm": float(current.get("precipitation") or 0.0),
        "rain": code in _WMO_RAIN or float(current.get("rain") or 0) > 0,
        "snow": code in _WMO_SNOW or float(current.get("snowfall") or 0) > 0,
        "fog": code in _WMO_FOG,
        "wind_kph": float(current.get("wind_speed_10m") or 0.0),
        "visibility_km": None if visibility is None else float(visibility) / 1000.0,
    }


def _met_norway(lat: float, lon: float) -> dict:
    response = _http().get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": round(lat, 2), "lon": round(lon, 2)},
        timeout=(CONNECT_S, TIMEOUT_S),
    )
    response.raise_for_status()
    now = response.json()["properties"]["timeseries"][0]["data"]
    details = now["instant"]["details"]
    next_hour = now.get("next_1_hours") or {}
    symbol = (next_hour.get("summary") or {}).get("symbol_code", "")
    precipitation = float((next_hour.get("details") or {}).get("precipitation_amount") or 0.0)
    return {
        "temperature_c": float(details["air_temperature"]),
        "precipitation_mm": precipitation,
        "rain": "rain" in symbol or "sleet" in symbol or "thunder" in symbol,
        "snow": "snow" in symbol or "sleet" in symbol,
        "fog": "fog" in symbol,
        "wind_kph": float(details.get("wind_speed") or 0.0) * 3.6,
        "visibility_km": None,
    }


def _weatherapi(lat: float, lon: float, key: str) -> dict:
    response = _http().get(
        "https://api.weatherapi.com/v1/current.json",
        params={"key": key, "q": f"{lat},{lon}", "aqi": "no"},
        timeout=(CONNECT_S, TIMEOUT_S),
    )
    response.raise_for_status()
    current = response.json()["current"]
    text = str((current.get("condition") or {}).get("text", "")).lower()
    return {
        "temperature_c": float(current["temp_c"]),
        "precipitation_mm": float(current.get("precip_mm") or 0.0),
        "rain": any(word in text for word in ("rain", "drizzle", "shower", "thunder", "sleet")),
        "snow": any(word in text for word in ("snow", "sleet", "blizzard", "ice")),
        "fog": "fog" in text or "mist" in text,
        "wind_kph": float(current.get("wind_kph") or 0.0),
        "visibility_km": float(current["vis_km"]) if current.get("vis_km") is not None else None,
    }


def current_weather(lat: float, lon: float) -> dict | None:
    """Current conditions near a point, from the first provider that answers.

    Cached per ~1 km for ten minutes. Returns None when every provider fails.
    """
    key = (round(lat, 2), round(lon, 2))
    cached = _weather_cache.get(key)
    if cached is not None:
        return cached
    chain: list[tuple[str, Callable[[], dict]]] = []
    api_key = (os.environ.get("WEATHER_API_KEY") or "").strip()
    if api_key:
        chain.append(("weatherapi", lambda: _weatherapi(lat, lon, api_key)))
    chain += [
        ("open-meteo", lambda: _open_meteo(lat, lon)),
        ("met-norway", lambda: _met_norway(lat, lon)),
    ]
    for name, fetch in chain:
        breaker = BREAKERS[name]
        if not breaker.allow():
            continue
        try:
            reading = fetch()
        except Exception as exc:  # noqa: BLE001 - fall through to the next provider
            breaker.failure()
            WEATHER_STATUS["last_error"] = f"{name}: {type(exc).__name__}"
            continue
        breaker.success()
        reading["provider"] = name
        _weather_cache.put(key, reading)
        WEATHER_STATUS.update(provider=name, checked_at=time.time())
        return reading
    WEATHER_STATUS["checked_at"] = time.time()
    return None


# ---------------------------------------------------------------------------
# Places and roads (Photon, OpenStreetMap data)
# ---------------------------------------------------------------------------

PHOTON_SEARCH = "https://photon.komoot.io/api/"
PHOTON_REVERSE = "https://photon.komoot.io/reverse"
# (connect, read): a refusing Photon must fail fast so the fallback still fits the budget.
PHOTON_TIMEOUT = (CONNECT_S, TIMEOUT_S)
_place_cache = TTLCache(6 * 3600)
_road_cache = TTLCache(24 * 3600, limit=20000)
# Photon is a free public service that throttles bursts. Callers queue for one of
# a few slots instead of all asking at once (their own deadlines still apply).
PHOTON_SLOTS = 4
_photon_slots = threading.BoundedSemaphore(PHOTON_SLOTS)


def _photon_get(url: str, params: dict):
    with _photon_slots:
        return _http().get(url, params=params, timeout=PHOTON_TIMEOUT)


def _bbox(lat: float, lon: float, radius_m: float) -> str:
    d_lat = radius_m / 111_320.0
    d_lon = radius_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return f"{lon - d_lon:.5f},{lat - d_lat:.5f},{lon + d_lon:.5f},{lat + d_lat:.5f}"


def places(lat: float, lon: float, radius_m: float, tag: str, query: str, limit: int = 30) -> list[dict] | None:
    """OpenStreetMap features with one ``key:value`` tag inside a square around a point."""
    key = (tag, round(lat, 3), round(lon, 3), int(radius_m), limit)
    cached = _place_cache.get(key)
    if cached is not None:
        return cached
    if not BREAKERS["photon"].allow():
        return _places_nominatim(lat, lon, radius_m, tag, limit, key)
    try:
        response = _photon_get(
            PHOTON_SEARCH,
            {"q": query, "osm_tag": tag, "bbox": _bbox(lat, lon, radius_m), "limit": limit},
        )
        response.raise_for_status()
        features = response.json().get("features", [])
    except Exception:  # noqa: BLE001 - optional data
        BREAKERS["photon"].failure()
        features = None
    if features is None:
        return _places_nominatim(lat, lon, radius_m, tag, limit, key)
    BREAKERS["photon"].success()
    found = []
    for feature in features:
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        properties = feature.get("properties") or {}
        found.append(
            {
                "name": properties.get("name") or tag.split(":")[1].replace("_", " ").title(),
                "lat": float(coordinates[1]),
                "lon": float(coordinates[0]),
                "tag": tag,
            }
        )
    _place_cache.put(key, found)
    return found


# ---------------------------------------------------------------------------
# Nightlife counts (Overpass)
# ---------------------------------------------------------------------------

# Keyless Overpass servers from the OSM wiki ("Public Overpass API instances",
# checked 2026-11-03). A regional server only holds its own region and answers 0
# for anywhere else, so it is only asked about the countries it covers. The
# global ones are often at capacity; each has its own breaker, and one that
# fails twice is left alone for five minutes.
EUROPE = {
    "AD", "AL", "AT", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK", "EE", "ES",
    "FI", "FO", "FR", "GB", "GG", "GI", "GR", "HR", "HU", "IE", "IM", "IS", "IT", "JE",
    "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL", "PT", "RO",
    "RS", "SE", "SI", "SK", "SM", "TR", "UA", "VA", "XK",
}
BRITAIN_AND_IRELAND = {"GB", "IE", "IM"}
OVERPASS_SERVERS = (
    ("overpass-europe", "https://ovp.dresel.systems/api/interpreter", EUROPE),
    ("overpass-britain", "https://overpass.atownsend.org.uk/api/interpreter", BRITAIN_AND_IRELAND),
    ("overpass", "https://overpass-api.de/api/interpreter", None),
    ("overpass-coffee", "https://overpass.private.coffee/api/interpreter", None),
    ("overpass-vk", "https://maps.mail.ru/osm/tools/overpass/api/interpreter", None),
)
for _name, _url, _covers in OVERPASS_SERVERS:
    # The regional servers are healthy and get a second chance; the global ones
    # are usually at capacity, so one failure parks them for five minutes.
    BREAKERS[_name] = Breaker(_name, failures=2 if _covers else 1, cooldown_s=300.0)
# One Overpass attempt never waits longer than this for an answer.
OVERPASS_ATTEMPT_S = 2.5

NIGHTLIFE_FILTER = 'nwr["amenity"~"^(bar|pub|nightclub)$"]'
_nightlife_cache = TTLCache(6 * 3600)
_venue_cache = TTLCache(6 * 3600, limit=200)


def _overpass_servers(lat: float, lon: float) -> list[tuple[str, str, bool]]:
    """``(name, url, regional)`` for the servers that hold data for a point."""
    country = country_offline(lat, lon)
    # The European extract stops at the continent; overseas territories go global.
    in_europe = 27.0 <= lat <= 72.0 and -32.0 <= lon <= 45.0
    return [
        (name, url, covers is not None)
        for name, url, covers in OVERPASS_SERVERS
        if covers is None or (country in covers and in_europe)
    ]


#: The global servers are usually at capacity: never spend more than this on
#: them for one query, whatever the caller's budget.
OVERPASS_GLOBAL_BUDGET_S = 3.0


def overpass(
    query: str, lat: float, lon: float, budget_s: float = 4.0, attempt_s: float | None = None
) -> dict | None:
    """Run an Overpass query on the first server that covers ``lat, lon`` and
    answers within the budget; None when none does. ``attempt_s`` caps the wait
    for one regional server (default ``OVERPASS_ATTEMPT_S``); a global server
    always gets the short default."""
    attempt_s = attempt_s or OVERPASS_ATTEMPT_S
    started = time.time()
    deadline = started + budget_s
    for name, url, regional in _overpass_servers(lat, lon):
        breaker = BREAKERS[name]
        if not breaker.allow():
            continue
        until = deadline if regional else min(deadline, started + OVERPASS_GLOBAL_BUDGET_S)
        remaining = until - time.time()
        if remaining < 0.8:
            continue
        wait_s = attempt_s if regional else OVERPASS_ATTEMPT_S
        try:
            response = _http().post(
                url,
                data={"data": query},
                timeout=(min(CONNECT_S, remaining), min(wait_s, remaining)),
            )
            response.raise_for_status()
            data = response.json()
        except Exception:  # noqa: BLE001 - try the next server
            breaker.failure()
            continue
        breaker.success()
        return data
    return None


def nightlife_count(lat: float, lon: float, radius_m: float = 1000.0, budget_s: float = 2.5) -> int | None:
    """Bars, pubs and nightclubs within ``radius_m``, counted by Overpass.

    Photon only finds venues whose *name* contains the search word, so it
    undercounts badly (8 venues in Soho, where Overpass counts 364). ``None``
    when no server answers in time; the caller then falls back to Photon.
    """
    key = (round(lat, 3), round(lon, 3), int(radius_m))
    cached = _nightlife_cache.get(key)
    if cached is not None:
        return cached
    data = overpass(
        f"[out:json][timeout:5];{NIGHTLIFE_FILTER}(around:{int(radius_m)},{lat:.5f},{lon:.5f});out count;",
        lat, lon, budget_s,
    )
    try:
        total = int(data["elements"][0]["tags"]["total"]) if data else None
    except (KeyError, IndexError, TypeError, ValueError):
        total = None
    if total is not None:
        _nightlife_cache.put(key, total)
    return total


def nightlife_counts(
    points: list[tuple[float, float]], radius_m: float = 1000.0, budget_s: float = 4.0
) -> list[int] | None:
    """``nightlife_count`` for many points in one Overpass request (a heatmap tile)."""
    if not points:
        return []
    key = (tuple((round(lat, 3), round(lon, 3)) for lat, lon in points), int(radius_m))
    cached = _nightlife_cache.get(key)
    if cached is not None:
        return cached
    statements = "".join(
        f"{NIGHTLIFE_FILTER}(around:{int(radius_m)},{lat:.5f},{lon:.5f});out count;" for lat, lon in points
    )
    centre = points[len(points) // 2]
    data = overpass(f"[out:json][timeout:20];{statements}", centre[0], centre[1], budget_s)
    try:
        counts = [
            int(element["tags"]["total"])
            for element in (data or {}).get("elements", [])
            if element.get("type") == "count"
        ]
    except (KeyError, TypeError, ValueError):
        return None
    if len(counts) != len(points):
        return None
    _nightlife_cache.put(key, counts)
    return counts


class VenueGrid:
    """Venues bucketed on a 0.01-degree grid, for fast "how many within r" counts.

    ``bounds`` is the box the venue list is complete for; a count is only
    trusted where the whole search circle fits inside it.
    """

    STEP = 0.01

    def __init__(self, venues: list[tuple[float, float]], bounds: tuple[float, float, float, float]):
        self.bounds = bounds
        self.size = len(venues)
        self.buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for lat, lon in venues:
            self.buckets.setdefault((int(lat // self.STEP), int(lon // self.STEP)), []).append((lat, lon))

    @staticmethod
    def _span(lat: float, radius_m: float) -> tuple[float, float]:
        d_lat = radius_m / 111_320.0
        return d_lat, d_lat / max(0.2, math.cos(math.radians(lat)))

    def covers(self, lat: float, lon: float, radius_m: float = 1000.0) -> bool:
        d_lat, d_lon = self._span(lat, radius_m)
        south, west, north, east = self.bounds
        return south <= lat - d_lat and lat + d_lat <= north and west <= lon - d_lon and lon + d_lon <= east

    def count(self, lat: float, lon: float, radius_m: float = 1000.0) -> int:
        d_lat, d_lon = self._span(lat, radius_m)
        scale = math.cos(math.radians(lat))
        limit = (radius_m / 111_320.0) ** 2
        total = 0
        for i in range(int((lat - d_lat) // self.STEP), int((lat + d_lat) // self.STEP) + 1):
            for j in range(int((lon - d_lon) // self.STEP), int((lon + d_lon) // self.STEP) + 1):
                for vlat, vlon in self.buckets.get((i, j), ()):
                    if (vlat - lat) ** 2 + ((vlon - lon) * scale) ** 2 <= limit:
                        total += 1
        return total


def nightlife_venues(bounds: tuple[float, float, float, float], budget_s: float = 6.0) -> VenueGrid | None:
    """Every bar, pub and nightclub in ``(south, west, north, east)``, as a VenueGrid.

    Mapped as points only: venues drawn as building outlines make the server
    compute centres, which more than doubled the time for the Paris trip (5.2 s
    against 2.2 s) for 2.5% more venues.
    """
    key = tuple(round(value, 3) for value in bounds)
    cached = _venue_cache.get(key)
    if cached is not None:
        return cached
    south, west, north, east = bounds
    data = overpass(
        f'[out:json][timeout:12];node["amenity"~"^(bar|pub|nightclub)$"]'
        f"({south:.5f},{west:.5f},{north:.5f},{east:.5f});out skel qt;",
        (south + north) / 2.0, (west + east) / 2.0, budget_s, attempt_s=budget_s,
    )
    if data is None:
        return None
    venues = []
    for element in data.get("elements", []):
        where = element if "lat" in element else element.get("center") or {}
        if "lat" in where and "lon" in where:
            venues.append((float(where["lat"]), float(where["lon"])))
    grid = VenueGrid(venues, bounds)
    _venue_cache.put(key, grid)
    return grid


# ---------------------------------------------------------------------------
# DNS: reuse the last good answer when a lookup fails
# ---------------------------------------------------------------------------

_resolve = socket.getaddrinfo
_resolved: dict = {}
_resolved_lock = threading.Lock()
DNS_STALE_S = 6 * 3600.0


def _is_address(host) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _getaddrinfo(host, port, *args, **kwargs):
    """``socket.getaddrinfo`` that falls back to the last good answer.

    A fresh lookup is always tried first, so nothing changes while DNS works.
    When the resolver drops out (it does on this network, for a minute at a
    time), a name that resolved in the last six hours still connects.
    """
    key = (host, port, args, tuple(sorted(kwargs.items())))
    try:
        answer = _resolve(host, port, *args, **kwargs)
    except socket.gaierror:
        with _resolved_lock:
            known = _resolved.get(key)
        if known is not None and time.time() - known[0] < DNS_STALE_S:
            return known[1]
        raise
    if isinstance(host, str) and host and not _is_address(host):
        with _resolved_lock:
            _resolved[key] = (time.time(), answer)
    return answer


def install_dns_fallback() -> None:
    """Make every outbound request in this process survive a resolver drop-out."""
    if socket.getaddrinfo is not _getaddrinfo:
        socket.getaddrinfo = _getaddrinfo


PROVIDER_HOSTS = (
    "photon.komoot.io", "nominatim.openstreetmap.org", "api.open-meteo.com", "api.met.no",
    "router.project-osrm.org", "routing.openstreetmap.de", "ovp.dresel.systems",
    "overpass.atownsend.org.uk", "overpass-api.de",
)


def warm_up_dns() -> None:
    """Resolve every provider's name once, in the background, at start-up.

    The first lookup of a name on this machine can take longer than a whole
    request's connect timeout, so the first place search after a restart used
    to fail over to the offline list. Resolving early also seeds the fallback
    above. Nothing is sent to the providers themselves.
    """

    def resolve_all():
        for host in PROVIDER_HOSTS:
            try:
                socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)
            except OSError:
                pass

    threading.Thread(target=resolve_all, name="dns-warm-up", daemon=True).start()


# ---------------------------------------------------------------------------
# Place search (Photon, then Nominatim, then the offline city list)
# ---------------------------------------------------------------------------

_search_cache = TTLCache(24 * 3600, limit=2000)
#: How long Photon gets on its own before Nominatim is asked as well.
SEARCH_HEDGE_S = 1.2
#: The most a search waits after that for either of them.
SEARCH_DEADLINE_S = 3.5


def _photon_place(feature: dict) -> dict | None:
    """A Photon feature in the shape the app reads from Nominatim."""
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return None
    props = feature.get("properties") or {}
    name = props.get("name") or props.get("street") or props.get("city")
    if not name:
        return None
    parts = [name, props.get("street"), props.get("district"), props.get("city"),
             props.get("state"), props.get("country")]
    display = ", ".join(dict.fromkeys(part for part in parts if part))
    return {
        "place_id": f"photon-{props.get('osm_type', '')}{props.get('osm_id', '')}",
        "display_name": display,
        "name": name,
        "lat": str(coordinates[1]),
        "lon": str(coordinates[0]),
        "type": props.get("osm_value"),
        "class": props.get("osm_key"),
        "source": "photon",
    }


def _search_photon(text: str, limit: int) -> list[dict] | None:
    if not BREAKERS["photon"].allow():
        return None
    try:
        # English names where OpenStreetMap has them ("Sharjah", not the
        # Arabic); more candidates than shown, so ranking has room to work.
        response = _photon_get(PHOTON_SEARCH, {"q": text, "limit": max(limit, 10), "lang": "en"})
        response.raise_for_status()
        features = response.json().get("features", [])
    except Exception:  # noqa: BLE001 - fall through to Nominatim
        BREAKERS["photon"].failure()
        return None
    BREAKERS["photon"].success()
    return [place for place in (_photon_place(feature) for feature in features) if place]


def _search_nominatim(text: str, limit: int) -> list[dict] | None:
    data = _nominatim("search", {"q": text, "addressdetails": 1, "limit": limit})
    if not isinstance(data, list):
        return None
    return [
        {
            "place_id": item.get("place_id"),
            "display_name": item.get("display_name"),
            "name": item.get("name"),
            "lat": item.get("lat"),
            "lon": item.get("lon"),
            "type": item.get("type"),
            "source": "nominatim",
        }
        for item in data
        if item.get("lat") and item.get("lon")
    ]


# Which kind of place someone typing a name most likely means, best first.
_PLACE_RANK = {
    "city": 0, "capital": 0, "town": 1, "state": 1, "country": 1, "attraction": 2,
    "tower": 2, "museum": 2, "stadium": 2, "aerodrome": 2, "station": 2, "university": 2,
    "suburb": 3, "neighbourhood": 3, "quarter": 3, "district": 3, "village": 4,
    "hamlet": 5, "locality": 5,
}


def _rank_places(text: str, places: list[dict], limit: int) -> list[dict]:
    """Exact name matches first, cities before villages; a major city typed by
    name ("Bangkok") leads even when a village of that name ranks higher online."""
    import gazetteer

    query = gazetteer._fold(text)
    known = gazetteer.canonical(text)

    def bare(name: str) -> str:
        # "The Dubai Mall" is what someone typing "Dubai Mall" means.
        folded = gazetteer._fold(name)
        return folded[4:] if folded.startswith("the ") else folded

    def key(place: dict) -> tuple[int, int]:
        exact = bare(str(place.get("name") or "")) in (query, bare(query))
        return (0 if exact else 1, _PLACE_RANK.get(str(place.get("type") or ""), 4))

    ranked = sorted(places, key=key)
    major = [city for city in gazetteer.search(text, 3) if gazetteer._fold(city["name"]) == known]
    if major:
        city = major[0]
        lat, lon = float(city["lat"]), float(city["lon"])
        near = next(
            (place for place in ranked
             if gazetteer._fold(str(place.get("name") or "")) == known
             and haversine_km(lat, lon, float(place["lat"]), float(place["lon"])) <= 25.0),
            None,
        )
        if near is not None:
            ranked.remove(near)
            ranked.insert(0, near)
        else:
            ranked.insert(0, city)
    # A city and its province often share a name, so two rows would read the
    # same and one drops a pin in the middle of the province. Keep the better.
    seen: set[str] = set()
    unique = []
    for place in ranked:
        label = gazetteer._fold(str(place.get("display_name") or place.get("name") or ""))
        if label not in seen:
            seen.add(label)
            unique.append(place)
    return unique[:limit]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def search_places(text: str, limit: int = 6) -> list[dict] | None:
    """Places matching ``text``, Nominatim-shaped, or None if nothing answered.

    Photon is built for search-as-you-type and goes first. If it has not
    answered within ``SEARCH_HEDGE_S``, Nominatim is asked too (its policy
    allows one request a second, which the shared gate keeps to) and the first
    useful answer wins. When neither answers, the offline list of major cities
    still finds "Bangkok"; None only when that has nothing either.
    """
    import gazetteer  # local: only place search needs it

    key = (" ".join(text.lower().split()), int(limit))
    cached = _search_cache.get(key)
    if cached is not None:
        return cached
    pool = ThreadPoolExecutor(max_workers=2)
    found = None
    answered = False
    try:
        photon = pool.submit(_search_photon, text, limit)
        done, _ = wait([photon], timeout=SEARCH_HEDGE_S)
        if photon in done:
            found = photon.result()
            answered = found is not None
        if not found:
            waiting = [photon] if photon not in done else []
            waiting.append(pool.submit(_search_nominatim, text, limit))
            deadline = time.time() + SEARCH_DEADLINE_S
            while waiting and not found and time.time() < deadline:
                done, _ = wait(waiting, timeout=max(0.0, deadline - time.time()), return_when="FIRST_COMPLETED")
                for future in done:
                    waiting.remove(future)
                    value = future.result()
                    answered = answered or value is not None
                    if value:
                        found = value
                        break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if found:
        found = _rank_places(text, found, limit)
        _search_cache.put(key, found)
        return found
    offline = gazetteer.search(text, limit)
    if offline:
        return offline
    return [] if answered else None


# Nominatim answers one request a second, so only the stops that matter most
# fall back to it; nightlife and car parks wait for Photon.
_NOMINATIM_TAGS = {"amenity:police", "amenity:hospital"}


def _places_nominatim(lat, lon, radius_m, tag, limit, key):
    key_name, value = tag.split(":", 1)
    if tag not in _NOMINATIM_TAGS:
        return None
    x0, y0, x1, y1 = _bbox(lat, lon, radius_m).split(",")
    data = _nominatim(
        "search",
        {key_name: value, "viewbox": f"{x0},{y1},{x1},{y0}", "bounded": 1, "limit": min(limit, 20)},
    )
    if not isinstance(data, list):
        return None
    found = [
        {"name": item.get("name") or value.replace("_", " ").title(), "lat": float(item["lat"]),
         "lon": float(item["lon"]), "tag": tag}
        for item in data if "lat" in item and "lon" in item
    ]
    _place_cache.put(key, found)
    return found


def places_many(
    lat: float, lon: float, radius_m: float, specs: list[tuple[str, str, int]], deadline_s: float = 6.5
) -> dict[str, list[dict] | None]:
    """Several ``places`` lookups at once; a slow one is reported as None."""
    pool = ThreadPoolExecutor(max_workers=max(1, len(specs)))
    futures = {tag: pool.submit(places, lat, lon, radius_m, tag, query, limit) for tag, query, limit in specs}
    done, _ = wait(futures.values(), timeout=deadline_s)
    pool.shutdown(wait=False, cancel_futures=True)
    return {tag: (future.result() if future in done else None) for tag, future in futures.items()}


def road_at(lat: float, lon: float, fallback: bool = True) -> dict | None:
    """The road nearest a point: ``{"name", "class", "country"}`` or None.

    ``class`` is the OpenStreetMap highway value (motorway, primary,
    residential...). ``country`` is the ISO 3166-1 alpha-2 code.
    """
    key = (round(lat, 4), round(lon, 4))
    cached = _road_cache.get(key)
    if cached is not None:
        return cached or None
    if not BREAKERS["photon"].allow():
        return _road_nominatim(lat, lon, key) if fallback else None
    try:
        response = _photon_get(
            PHOTON_REVERSE,
            {"lat": lat, "lon": lon, "osm_tag": "highway", "radius": 0.25, "limit": 1},
        )
        response.raise_for_status()
        features = response.json().get("features", [])
    except Exception:  # noqa: BLE001 - optional data
        BREAKERS["photon"].failure()
        features = None
    if features is None:
        return _road_nominatim(lat, lon, key) if fallback else None
    BREAKERS["photon"].success()
    road = {}
    if features:
        properties = features[0].get("properties") or {}
        road = {
            "name": properties.get("name") or properties.get("street"),
            "class": properties.get("osm_value"),
            "country": (properties.get("countrycode") or "").upper() or None,
        }
    _road_cache.put(key, road)
    return road or None


_COUNTRY_SHAPES: list[tuple[str, tuple[float, float, float, float], list[list[tuple[float, float]]]]] | None = None


def _country_shapes():
    """Country outlines from data/countries.geojson: (iso2, bbox, outer rings)."""
    global _COUNTRY_SHAPES
    if _COUNTRY_SHAPES is None:
        import json

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "countries.geojson")
        shapes = []
        with open(path, encoding="utf-8") as handle:
            for feature in json.load(handle)["features"]:
                iso2 = feature["properties"].get("iso2")
                geometry = feature["geometry"]
                polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
                rings = [[(float(x), float(y)) for x, y in polygon[0]] for polygon in polygons]
                xs = [x for ring in rings for x, _ in ring]
                ys = [y for ring in rings for _, y in ring]
                if iso2 and xs:
                    shapes.append((iso2, (min(xs), min(ys), max(xs), max(ys)), rings))
        _COUNTRY_SHAPES = shapes
    return _COUNTRY_SHAPES


def _inside(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def country_offline(lat: float, lon: float, reach_deg: float = 0.6) -> str | None:
    """Country at a point from bundled outlines; no network.

    The outlines are coarse (1:110m), so a coastal city can fall just outside
    its country; then the nearest outline within ``reach_deg`` wins.
    """
    shapes = _country_shapes()
    for iso2, (x0, y0, x1, y1), rings in shapes:
        if x0 <= lon <= x1 and y0 <= lat <= y1 and any(_inside(lon, lat, ring) for ring in rings):
            return iso2
    best, best_d = None, reach_deg ** 2
    for iso2, (x0, y0, x1, y1), rings in shapes:
        if not (x0 - reach_deg <= lon <= x1 + reach_deg and y0 - reach_deg <= lat <= y1 + reach_deg):
            continue
        for ring in rings:
            for x, y in ring:
                d = (x - lon) ** 2 + (y - lat) ** 2
                if d < best_d:
                    best, best_d = iso2, d
    return best


#: Nominatim categories that describe a region or water, not something at the point.
AREA_NOT_A_PLACE = {"boundary", "natural", "place", "waterway"}


def _road_nominatim(lat: float, lon: float, key) -> dict | None:
    data = _nominatim("reverse", {"lat": lat, "lon": lon, "zoom": 17})
    if not isinstance(data, dict):
        return None
    if "error" in data:
        # A finished answer ("Unable to geocode"): nothing mapped at this point.
        _road_cache.put(key, {})
        return None
    address = data.get("address") or {}
    road = {
        "name": data.get("name") or address.get("road"),
        "class": data.get("type") if data.get("category") == "highway" else None,
        "country": (address.get("country_code") or "").upper() or None,
    }
    # At sea inside a country's waters Nominatim names the county or country.
    if data.get("category") in AREA_NOT_A_PLACE:
        road["area_only"] = True
    _road_cache.put(key, road)
    return road


_LAND = None


def _land_rings():
    """Natural Earth 1:50m land, per polygon: its box and rings as number arrays."""
    global _LAND
    if _LAND is None:
        import json

        import numpy as np

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "land_50m.geojson")
        polygons = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for geometry in json.load(handle)["geometries"]:
                    rings = [np.asarray(ring, dtype=np.float64) for ring in geometry["coordinates"]]
                    outer = rings[0]
                    box = (outer[:, 0].min(), outer[:, 1].min(), outer[:, 0].max(), outer[:, 1].max())
                    polygons.append((box, rings))
        _LAND = polygons
    return _LAND


def on_land(lat: float, lon: float) -> bool | None:
    """Is the point on land? None when the land file is missing.

    The country outlines cannot answer this: they are coarse and reach 0.6 degrees
    past the coast, so most of the Gulf reads as Iran or Qatar. This map is finer
    but still misses piers and new islands, so "not on land" alone proves nothing
    at the coast: callers also check for a road.
    """
    polygons = _land_rings()
    if not polygons:
        return None
    for (x0, y0, x1, y1), rings in polygons:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        crossings = 0  # Even-odd over every ring, so lakes cut out of a polygon stay water.
        for ring in rings:
            xs, ys = ring[:-1, 0], ring[:-1, 1]
            xs_next, ys_next = ring[1:, 0], ring[1:, 1]
            spans = (ys > lat) != (ys_next > lat)
            if spans.any():
                x, y = xs[spans], ys[spans]
                dx, dy = (xs_next - xs)[spans], (ys_next - ys)[spans]
                crossings += int((lon < x + (lat - y) * dx / dy).sum())
        if crossings % 2:
            return True
    return False


def no_road_found(lat: float, lon: float) -> bool:
    """True only when a finished lookup found no road here. A failed or unfinished lookup is not an answer."""
    found = _road_cache.get((round(lat, 4), round(lon, 4)))
    return found == {} or bool(found and found.get("area_only"))


def country_at(lat: float, lon: float) -> str | None:
    """ISO alpha-2 country code at a point, or None.

    Bundled outlines first (instant, works offline); Photon only as a fallback
    for points no outline reaches.
    """
    offline = country_offline(lat, lon)
    if offline:
        return offline
    road = road_at(lat, lon)
    if road and road.get("country"):
        return road["country"]
    key = ("country", round(lat, 2), round(lon, 2))
    cached = _road_cache.get(key)
    if cached is not None:
        return cached or None
    if not BREAKERS["photon"].allow():
        return None
    try:
        response = _photon_get(PHOTON_REVERSE, {"lat": lat, "lon": lon, "limit": 1})
        response.raise_for_status()
        features = response.json().get("features", [])
    except Exception:  # noqa: BLE001 - optional data
        BREAKERS["photon"].failure()
        return None
    BREAKERS["photon"].success()
    code = ((features[0].get("properties") or {}).get("countrycode") or "").upper() if features else ""
    _road_cache.put(key, code)
    return code or None


def roads_many(points: list[tuple[float, float]], deadline_s: float = 6.0, workers: int = 6) -> list[dict | None]:
    """``road_at`` for many points in parallel, within a time budget."""
    pool = ThreadPoolExecutor(max_workers=workers)
    # Bulk lookups never fall back to Nominatim: it allows one request a second.
    futures = [pool.submit(road_at, lat, lon, False) for lat, lon in points]
    done, _ = wait(futures, timeout=deadline_s)
    pool.shutdown(wait=False, cancel_futures=True)
    return [future.result() if future in done else None for future in futures]
