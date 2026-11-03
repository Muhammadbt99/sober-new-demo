import requests, time, random, math, joblib, os, json, numpy as np, sqlite3
from datetime import datetime
import pandas as pd
import threading
from dotenv import load_dotenv

from features import time_features, in_model_area
from severity_features import (
    CLASS_NAMES, SEVERITY_FEATURES, build_row_from_parts,
)
from routing import (
    describe_providers,
    get_routes as routing_get_routes,
    get_via_route as routing_get_via_route,
)
from crash_model import risk_model as crash_risk_model
import providers

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(MODULE_DIR, "API_KEY.env"))
load_dotenv()

active_alerts = []
alert_status = {
    "last_checked": None,
    "data_quality": "unknown",
    "warning": None,
    "locations_checked": 0,
}
alert_lock = threading.Lock()
routes_lock = threading.Lock()
active_routes = {}
ROUTE_CACHE_EXPIRY = 300
DB_PATH = os.environ.get("SOBER_DB_PATH") or os.path.join(MODULE_DIR, "sober.db")

_overpass_cache = {}
_bars_cache = {}
OVERPASS_CACHE_EXPIRY = 21600
_overpass_down_until = 0

WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
_weather_cache = {}
WEATHER_CACHE_EXPIRY = 600

# The request caches are keyed by coordinates, so a caller that walks the
# coordinates of the world would grow them without bound.  Cap them and
# evict the oldest entries instead.
MAX_CACHE_ENTRIES = 10000


def _cache_put(cache, key, value, limit=MAX_CACHE_ENTRIES):
    """Insert into a request cache, evicting the oldest entries when full."""
    if len(cache) >= limit:
        for stale_key in list(cache)[:limit // 2]:
            del cache[stale_key]
    cache[key] = value

MODEL_PATH = os.path.join(MODULE_DIR, "model.pkl")
try:
    severity_model = joblib.load(MODEL_PATH)
    print("ML model loaded successfully")
except Exception as e:
    severity_model = None
    print(f"Model load failed: {e}")

# Offline indexes are optional so the API still boots if the data files are absent.
try:
    from geo import CrashIndex
    CRASH_INDEX = CrashIndex()
    print(f"Crash index: {'ready' if CRASH_INDEX.available else 'unavailable'}")
except Exception as e:
    CrashIndex = None
    CRASH_INDEX = None
    print(f"Crash index failed to load: {e}")

try:
    from bars import BarsIndex
    BARS_INDEX = BarsIndex()
    print(f"Bars index: {'ready' if BARS_INDEX.available else 'unavailable (live Overpass fallback)'}")
except Exception as e:
    BarsIndex = None
    BARS_INDEX = None
    print(f"Bars index failed to load: {e}")

try:
    from safe_stops import SafeStopsIndex
    SAFE_STOPS_INDEX = SafeStopsIndex()
    print(f"Safe stops index: {'ready' if SAFE_STOPS_INDEX.available else 'unavailable (live Overpass fallback)'}")
except Exception as e:
    SafeStopsIndex = None
    SAFE_STOPS_INDEX = None
    print(f"Safe stops index failed to load: {e}")

OSRM_BASE_URL = os.environ.get(
    "OSRM_BASE_URL", "http://router.project-osrm.org"
).rstrip("/")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    # Renamed from overpass.kumi.systems (OSM wiki, checked 2026-11-03).
    "https://overpass.private.coffee/api/interpreter",
]

OVERPASS_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "User-Agent": "SOBER-Project/1.0",
}


def get_overpass_url():
    return random.choice(OVERPASS_ENDPOINTS)


def get_time_features(hour=None, day=None):
    """Return (hour, weekday, is_weekend).

    ``hour`` and ``day`` are optional client-supplied local-time overrides.
    The server's clock is UTC on most hosts, so callers should pass the
    user's local time whenever it is available.
    """
    now = datetime.now()
    try:
        hour = int(hour) if hour is not None else now.hour
    except (TypeError, ValueError):
        hour = now.hour
    try:
        day_of_week = int(day) if day is not None else now.weekday()
    except (TypeError, ValueError):
        day_of_week = now.weekday()
    hour = hour % 24
    day_of_week = day_of_week % 7
    is_weekend = 1 if day_of_week >= 5 else 0
    return hour, day_of_week, is_weekend


def get_engineered_time_features(hour):
    return time_features(hour)


def _redact_key(value):
    """Strip the WeatherAPI key from anything we log."""
    text = str(value)
    if WEATHER_API_KEY:
        text = text.replace(WEATHER_API_KEY, "***")
    return text


def get_weather_ex(lat, lon):
    """Return ((temperature, rain, wind_speed), source).

    source is 'live' when any provider in ``providers.current_weather``'s chain
    answered (WeatherAPI.com if a key is set, then the keyless Open-Meteo and
    MET Norway), otherwise 'fallback'. Callers must surface a fallback instead
    of pretending the numbers are real.
    """
    reading = providers.current_weather(lat, lon)
    if reading is None:
        return (20, 0, 0), "fallback"
    return (
        reading["temperature_c"],
        1 if reading["rain"] else 0,
        reading["wind_kph"],
    ), "live"


def get_weather(lat, lon):
    return get_weather_ex(lat, lon)[0]


def _overpass_query(query, timeout=8, mirrors=None):
    """Try every mirror in turn; return parsed JSON or None.

    A short circuit breaker prevents 60 heatmap cells from each waiting on a
    dead Overpass mirror: after one full failure, later calls fail fast.
    """
    global _overpass_down_until
    if time.time() < _overpass_down_until:
        return None

    endpoints = OVERPASS_ENDPOINTS if mirrors is None else OVERPASS_ENDPOINTS[:mirrors]
    for url in endpoints:
        try:
            response = requests.post(
                url, data={"data": query}, headers=OVERPASS_HEADERS, timeout=timeout
            )
            if response.status_code != 200:
                print(f"Overpass {url} -> HTTP {response.status_code}: {response.text[:120]}")
                continue
            return response.json()
        except Exception as e:
            print(f"Overpass {url} failed: {e}")

    # Only trip the global breaker when every mirror was tried. A partial
    # sweep (mirrors=1, used by the bars lookup) says nothing about the
    # health of the mirrors it never contacted.
    if len(endpoints) >= len(OVERPASS_ENDPOINTS):
        _overpass_down_until = time.time() + 120
        print("Overpass unreachable - failing fast for the next 120s")
    return None


def _overpass_count(query, timeout=8, mirrors=None):
    data = _overpass_query(query, timeout=timeout, mirrors=mirrors)
    if not data:
        return None
    try:
        return int(data.get("elements", [{}])[0].get("tags", {}).get("total", 0))
    except (IndexError, TypeError, ValueError):
        return None


def get_bars_ex(lat, lon, radius=1000):
    """Nightlife venues within radius. Returns (count, source).

    Inside the model area the offline NYC index answers. Elsewhere it used to
    answer too, with 0, because the index only holds New York; now Photon
    (OpenStreetMap) counts bars, pubs and nightclubs, in parallel and cached.
    """
    if (
        BARS_INDEX is not None
        and getattr(BARS_INDEX, "available", False)
        and in_model_area(lat, lon)
    ):
        return int(BARS_INDEX.count(lat, lon, radius_km=radius / 1000.0)), "offline"
    counted = providers.nightlife_count(lat, lon, radius)
    if counted is not None:
        return counted, "live"
    found = providers.places_many(
        lat,
        lon,
        radius,
        [("amenity:bar", "bar", 50), ("amenity:pub", "pub", 50), ("amenity:nightclub", "nightclub", 50)],
        deadline_s=3.0,
    )
    if all(value is None for value in found.values()):
        return 0, "fallback"
    return sum(len(value or []) for value in found.values()), "live"


def get_bars(lat, lon, radius=1000):
    return get_bars_ex(lat, lon, radius)[0]


def get_accident_density_ex(lat, lon):
    """Weighted crash density from the offline NYC dataset.

    Returns (density, source). The old OSM accident=* query was removed: it
    returned near-zero counts that never matched the training distribution.
    """
    if CRASH_INDEX is not None and getattr(CRASH_INDEX, "available", False):
        if in_model_area(lat, lon):
            return round(float(CRASH_INDEX.density(lat, lon)), 1), "offline"
        return 0.0, "out_of_area"
    return 0.0, "unavailable"


def get_accident_density(lat, lon, radius=2000):
    return get_accident_density_ex(lat, lon)[0]


def get_cached_overpass_ex(lat, lon):
    """Return ((bars, accident_density), sources) for a point."""
    key = (round(lat, 2), round(lon, 2))
    cached = _overpass_cache.get(key)
    if cached and time.time() - cached["time"] < OVERPASS_CACHE_EXPIRY:
        return cached["data"], cached["sources"]

    bars, bars_source = get_bars_ex(lat, lon)
    density, density_source = get_accident_density_ex(lat, lon)
    data = (bars, density)
    sources = {"bars": bars_source, "crash_density": density_source}
    _cache_put(_overpass_cache, key, {"time": time.time(), "data": data, "sources": sources})
    return data, sources


def get_cached_overpass(lat, lon):
    return get_cached_overpass_ex(lat, lon)[0]


def build_features(lat, lon, hour=None, day=None):
    hour, day_of_week, is_weekend = get_time_features(hour=hour, day=day)
    time_of_day, is_night, is_rush_hour = time_features(hour)
    (temperature, rain, wind_speed), weather_source = get_weather_ex(lat, lon)
    (bars, accident_density), sources = get_cached_overpass_ex(lat, lon)

    covered = in_model_area(lat, lon)
    warnings = []
    if weather_source == "fallback":
        warnings.append("Live weather unavailable - using neutral defaults")
    elif weather_source == "stale":
        warnings.append("Weather API refresh failed - using last cached reading")
    if not covered:
        warnings.append("No crash data for this region (NYC-only dataset)")
    if sources.get("bars") == "fallback":
        warnings.append("Nightlife data unavailable - bars treated as 0")

    return {
        "hour": hour,
        "day_of_week": day_of_week,
        "is_weekend": is_weekend,
        "latitude": lat,
        "longitude": lon,
        "temperature": temperature,
        "rain": rain,
        "wind_speed": wind_speed,
        "bars": bars,
        "accident_density": accident_density,
        "time_of_day": time_of_day,
        "is_night": is_night,
        "is_rush_hour": is_rush_hour,
        "coverage": "offline" if covered else "rule_based_only",
        "data_quality": {
            "weather": weather_source,
            "bars": sources.get("bars", "unavailable"),
            "crash_density": sources.get("crash_density", "unavailable"),
        },
        "warnings": warnings,
    }


def predict_severity(
    *,
    hour,
    is_weekend,
    lat,
    lon,
    month=None,
    borough=None,
    vehicles=(),
    factors=(),
):
    """Score one crash scenario with the retrospective severity model.

    ``vehicles`` and ``factors`` are the raw strings the NYC dataset uses
    ("Bike", "Unsafe Speed"). They are what the model actually keys on: the
    same place and hour can be property-damage-only for a sedan and fatal for
    a bicycle. Omitting them still works, but the prediction then describes a
    generic crash.

    Returns ``None`` when no model is loaded, otherwise::

        {"predicted": "fatal_risk", "confidence": 0.61,
         "probabilities": {"property_damage_only": 0.2, ...}}

    This is a retrospective model. It is deliberately not called by the
    ``/risk`` scoring path - see ``train.py`` for why.
    """
    if severity_model is None:
        return None

    row = build_row_from_parts(
        hour=int(hour) % 24,
        month=int(month) if month is not None else datetime.now().month,
        is_weekend=int(bool(is_weekend)),
        lat=float(lat),
        lon=float(lon),
        borough=borough,
        vehicles=vehicles,
        factors=factors,
    )
    frame = pd.DataFrame(
        [[row[name] for name in SEVERITY_FEATURES]], columns=SEVERITY_FEATURES
    )
    proba = severity_model.predict_proba(frame)[0]
    classes = [int(value) for value in getattr(severity_model, "classes_", [0, 1, 2])]

    probabilities = {
        CLASS_NAMES[class_index]: round(float(value), 4)
        for class_index, value in zip(classes, proba)
    }
    best_index = classes[int(np.argmax(proba))]

    return {
        "predicted": CLASS_NAMES[best_index],
        "confidence": round(float(np.max(proba)), 4),
        "probabilities": probabilities,
    }


def get_model_metrics():
    path = os.path.join(MODULE_DIR, "cv_results.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    # Rounding can push the haversine term a hair above 1 for near-antipodal
    # points, which makes math.asin raise a domain error.
    a = min(1.0, max(0.0, a))
    return 2 * R * math.asin(math.sqrt(a))


def get_routes(origin_lat, origin_lon, dest_lat, dest_lon):
    """Return ``(routes, provider_id)`` from the first provider that answers.

    The chain and its circuit breakers live in ``routing.py``: public OSRM, then
    FOSSGIS OSRM, Valhalla, BRouter, and the keyed providers when configured.
    The provider id is returned rather than swallowed because the API must be
    able to say which service produced a route - a silent fallback would make a
    degraded route look identical to a normal one.
    """
    return routing_get_routes(origin_lat, origin_lon, dest_lat, dest_lon)


def get_via_route(origin, via, dest):
    """Return ``([route], provider_id)`` for one route forced through ``via``.

    Optional extra alternatives for the route planner; see ``routing.py``.
    """
    return routing_get_via_route(origin, via, dest)


def sample_waypoints(geometry, num_points=6):
    """Index-based sampling (kept for backwards compatibility)."""
    coords = geometry.get("coordinates", [])
    if not coords:
        return []
    total = len(coords)
    if total <= num_points:
        return [{"lat": c[1], "lon": c[0]} for c in coords]
    step = total // num_points
    sampled = [coords[i] for i in range(0, total, step)][:num_points]
    return [{"lat": c[1], "lon": c[0]} for c in sampled]


def sample_waypoints_by_distance(geometry, spacing_m=500, max_points=20):
    """Sample route geometry roughly every `spacing_m` metres.

    Unlike index sampling this keeps the destination and is not biased by
    how densely OSRM happens to encode straight sections.
    """
    coords = geometry.get("coordinates", [])
    if not coords:
        return []

    points = [coords[0]]
    accumulated = 0.0
    for previous, current in zip(coords, coords[1:]):
        accumulated += haversine_m(previous[1], previous[0], current[1], current[0])
        if accumulated >= spacing_m:
            points.append(current)
            accumulated = 0.0
    if points[-1] != coords[-1]:
        points.append(coords[-1])

    if len(points) > max_points:
        step = len(points) / max_points
        points = [points[int(i * step)] for i in range(max_points)]
        points[-1] = coords[-1]

    return [{"lat": c[1], "lon": c[0]} for c in points]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        # The demo build must not ship or create geolocated request history.
        # Earlier builds stored coordinates in these tables; drop them once and
        # recreate a coordinate-free schema so old rows cannot survive an upgrade.
        risk_columns = {row[1] for row in c.execute("PRAGMA table_info(risk_logs)")}
        if {"latitude", "longitude"} & risk_columns:
            c.execute("DROP TABLE risk_logs")
        route_columns = {row[1] for row in c.execute("PRAGMA table_info(route_logs)")}
        if {"origin_lat", "origin_lon", "dest_lat", "dest_lon"} & route_columns:
            c.execute("DROP TABLE route_logs")

        c.execute('''
            CREATE TABLE IF NOT EXISTS risk_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                endpoint TEXT,
                hour INTEGER,
                is_weekend INTEGER,
                risk_score INTEGER,
                risk_level TEXT,
                bars INTEGER,
                expected_rate REAL,
                temperature REAL,
                rain INTEGER,
                wind_speed REAL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS route_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                routes_returned INTEGER,
                recommended_risk_score INTEGER,
                recommended_risk_level TEXT,
                distance_km REAL,
                duration_min REAL
            )
        ''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_risk_logs_timestamp ON risk_logs(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_risk_logs_score ON risk_logs(risk_score)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_route_logs_timestamp ON route_logs(timestamp)")

        # Keep the demo database small: risk logs older than the retention window
        # are not needed by /stats or /crash_hotspots.
        retention_days = os.environ.get("LOG_RETENTION_DAYS", "90")
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            retention_days = 90
        if retention_days > 0:
            c.execute(
                "DELETE FROM risk_logs WHERE timestamp < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            c.execute(
                "DELETE FROM route_logs WHERE timestamp < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
        conn.commit()
    finally:
        conn.close()
    print("Database initialized")


def log_risk(endpoint, hour, is_weekend, risk_score, risk_level, bars, expected_rate, temperature, rain, wind_speed):
    """Record an aggregate request outcome, never the requested location."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO risk_logs (endpoint, hour, is_weekend, risk_score, risk_level, bars, expected_rate, temperature, rain, wind_speed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (endpoint, hour, is_weekend, risk_score, risk_level, bars, expected_rate, temperature, rain, wind_speed))
        conn.commit()
    except Exception as e:
        print(f"DB log error: {e}")
    finally:
        if conn is not None:
            conn.close()


def log_route(routes_returned, recommended_risk_score, recommended_risk_level, distance_km, duration_min):
    """Record an aggregate route outcome, never the origin or destination."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO route_logs(routes_returned, recommended_risk_score, recommended_risk_level, distance_km, duration_min)
            VALUES (?, ?, ?, ?, ?)
        ''', (routes_returned, recommended_risk_score, recommended_risk_level, distance_km, duration_min))
        conn.commit()
    except Exception as e:
        print(f"DB route log error: {e}")
    finally:
        if conn is not None:
            conn.close()


def _alerts_for_conditions(location_name, lat, lon, temperature, rain, wind_speed, source):
    alerts_for_loc = []

    if rain:
        alerts_for_loc.append({
            "type": "RAIN",
            "severity": "HIGH",
            "message": f"Rain detected at {location_name} - reduced visibility, increased crash risk",
        })
    if wind_speed > 30:
        alerts_for_loc.append({
            "type": "HIGH_WIND",
            "severity": "HIGH",
            "message": f"High winds ({wind_speed} km/h) at {location_name} - dangerous driving conditions",
        })
    elif wind_speed > 20:
        alerts_for_loc.append({
            "type": "MODERATE_WIND",
            "severity": "MEDIUM",
            "message": f"Moderate winds ({wind_speed} km/h) at {location_name} - drive with caution",
        })
    if temperature >= 38:
        alerts_for_loc.append({
            "type": "EXTREME_HEAT",
            "severity": "HIGH",
            "message": f"Extreme heat ({temperature}\u00b0C) at {location_name} - risk of tyre blowouts and driver fatigue",
        })
    if rain and wind_speed > 20:
        alerts_for_loc.append({
            "type": "SEVERE_CONDITIONS",
            "severity": "CRITICAL",
            "message": f"Combined rain + high wind at {location_name} - avoid driving if possible",
        })

    for alert in alerts_for_loc:
        alert["location"] = location_name
        alert["lat"] = lat
        alert["lon"] = lon
        alert["temperature"] = temperature
        alert["wind_speed"] = wind_speed
        alert["rain"] = bool(rain)
        alert["data_source"] = source
        alert["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return alerts_for_loc


def evaluate_weather_alerts(location_name, lat, lon):
    """Run threshold checks for a single location and return any alerts."""
    (temperature, rain, wind_speed), source = get_weather_ex(lat, lon)
    return _alerts_for_conditions(
        location_name, lat, lon, temperature, rain, wind_speed, source
    )


def evaluate_weather_alerts_ex(location_name, lat, lon):
    """Like evaluate_weather_alerts, but also return the weather source."""
    (temperature, rain, wind_speed), source = get_weather_ex(lat, lon)
    alerts = _alerts_for_conditions(
        location_name, lat, lon, temperature, rain, wind_speed, source
    )
    return alerts, source


def check_weather_alerts(locations=None):
    """Poll weather for a set of monitored locations.

    If every location falls back to neutral defaults, keep the previous
    alerts instead of silently clearing the alert feed.
    """
    if locations is None:
        locations = [
            {"name": "Times Square", "lat": 40.7580, "lon": -73.9855},
            {"name": "Downtown Dubai", "lat": 25.2048, "lon": 55.2708},
            {"name": "JFK Airport", "lat": 40.6413, "lon": -73.7781},
        ]

    new_alerts = []
    sources = []
    for loc in locations:
        try:
            alerts_for_loc, source = evaluate_weather_alerts_ex(
                loc["name"], loc["lat"], loc["lon"]
            )
            new_alerts.extend(alerts_for_loc)
            sources.append(source)
            print(f"Alert check: {loc['name']} - {len(alerts_for_loc)} alerts ({source})")
        except Exception as e:
            sources.append("fallback")
            print(f"Alert check error for {loc['name']}: {type(e).__name__}")

    # No usable reading at all (including "no locations to check") must not
    # wipe the alert feed.
    all_fallback = not sources or all(source == "fallback" for source in sources)
    with alert_lock:
        if all_fallback:
            warning = "Weather data unavailable - showing last known alerts"
            quality = "fallback"
        else:
            active_alerts.clear()
            active_alerts.extend(new_alerts)
            if any(source != "live" for source in sources):
                warning = "Some locations used cached or fallback weather"
                quality = "partial"
            else:
                warning = None
                quality = "live"
        alert_status.update({
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_quality": quality,
            "warning": warning,
            "locations_checked": len(locations),
        })

    print(f"Alert system updated - {len(active_alerts)} active alerts ({quality})")
    return new_alerts


_alert_poller_thread = None


def start_alert_poller(interval_minutes=30):
    """Run the weather alert check every interval_minutes in the background."""
    global _alert_poller_thread
    if _alert_poller_thread is not None and _alert_poller_thread.is_alive():
        return _alert_poller_thread

    def poller():
        while True:
            # A failure here must not kill the thread: the alert feed would
            # silently freeze for the rest of the process lifetime.
            try:
                print("Running scheduled weather alert check...")
                check_weather_alerts()
            except Exception as e:
                print(f"Alert poller error: {type(e).__name__}: {e}")
            time.sleep(interval_minutes * 60)

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    _alert_poller_thread = thread
    print(f"Alert poller started - checking every {interval_minutes} minutes")
    return thread


_hotspots_cache = None


def _load_crash_hotspots():
    """Group every crash into a rounded grid cell.

    ``crashes.csv`` is a static build artefact, so the grouped table is built
    once and reused instead of re-parsing 2.7 MB of CSV on every request.
    """
    df = pd.read_csv(os.path.join(MODULE_DIR, "crashes.csv"))
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    df = df.dropna(subset=["latitude", "longitude"])
    df = df[(df["latitude"] != 0) & (df["longitude"] != 0)]
    df = df[(df["latitude"].between(40.4, 41.0)) & (df["longitude"].between(-74.5, -73.5))]

    df["lat_r"] = df["latitude"].round(3)
    df["lon_r"] = df["longitude"].round(3)

    hotspots = df.groupby(["lat_r", "lon_r"]).size().reset_index(name="crash_count")
    hotspots = hotspots.sort_values("crash_count", ascending=False).reset_index(drop=True)

    return [
        {
            "lat": row["lat_r"],
            "lon": row["lon_r"],
            "crash_count": int(row["crash_count"]),
            "risk_level": "CRITICAL" if row["crash_count"] > 50 else
                          "HIGH" if row["crash_count"] > 20 else
                          "MEDIUM",
        }
        for _, row in hotspots.iterrows()
    ]


def get_crash_hotspots(top_k=10):
    global _hotspots_cache
    if _hotspots_cache is None:
        _hotspots_cache = _load_crash_hotspots()

    return [
        dict(hotspot, rank=index + 1)
        for index, hotspot in enumerate(_hotspots_cache[:max(0, top_k)])
    ]


_hotspot_arrays = None


def get_crash_hotspots_near(lat, lon, radius_m=2000, top_k=10):
    """The biggest crash clusters within ``radius_m`` of a point, busiest first.

    Same clusters as ``get_crash_hotspots``, but local: the list a person
    looking at one neighbourhood actually needs. Empty outside New York, where
    there are no crash records.
    """
    global _hotspots_cache, _hotspot_arrays
    if _hotspots_cache is None:
        _hotspots_cache = _load_crash_hotspots()
    if _hotspot_arrays is None:
        _hotspot_arrays = (
            np.asarray([spot["lat"] for spot in _hotspots_cache], dtype=float),
            np.asarray([spot["lon"] for spot in _hotspots_cache], dtype=float),
        )
    lats, lons = _hotspot_arrays
    if top_k <= 0 or lats.size == 0:
        return []
    # Flat-earth distance is accurate to well under 1% at city scale.
    scale = math.cos(math.radians(lat))
    distance_m = 111_320.0 * np.hypot(lats - lat, (lons - lon) * scale)
    # The cache is ordered by crash count, so the first matches are the busiest.
    nearby = np.flatnonzero(distance_m <= radius_m)[:top_k]
    return [
        dict(_hotspots_cache[index], rank=rank + 1, distance_m=int(round(distance_m[index])))
        for rank, index in enumerate(nearby)
    ]


def get_cached_route(route_id):
    with routes_lock:
        if route_id in active_routes:
            cached = active_routes[route_id]
            if time.time() - cached["last_updated"] < ROUTE_CACHE_EXPIRY:
                print(f"Route cache hit: {route_id}")
                return cached
            del active_routes[route_id]
    return None


def cache_route(route_id, data):
    with routes_lock:
        active_routes[route_id] = data

        expired = [k for k, v in active_routes.items()
                   if time.time() - v["last_updated"] > ROUTE_CACHE_EXPIRY]
        for k in expired:
            del active_routes[k]


def get_nearest_safe(lat, lon, radius=3000):
    """Find nearest safe stops - parking, police, hospitals within radius.

    Every stop in range is returned, ordered by safety priority first and
    distance second.  Callers decide how many of them to expose.
    """
    if (
        SAFE_STOPS_INDEX is not None
        and getattr(SAFE_STOPS_INDEX, "available", False)
        and in_model_area(lat, lon)
    ):
        return SAFE_STOPS_INDEX.nearest(
            lat, lon, radius_km=radius / 1000.0, limit=None
        )

    # Outside the NYC index: Photon (OpenStreetMap search), all types at once and
    # cached. This used to be one Overpass query for every car park within 3 km,
    # which in a big city timed out on both mirrors and stalled for 16 s.
    found = providers.places_many(
        lat,
        lon,
        radius,
        [
            ("amenity:police", "police", 15),
            ("amenity:hospital", "hospital", 15),
            ("amenity:clinic", "clinic", 15),
            ("highway:rest_area", "rest area", 10),
            ("amenity:parking", "parking", 15),
        ],
        deadline_s=4.5,
    )
    elements = [
        {"lat": place["lat"], "lon": place["lon"], "tags": {
            ("highway" if place["tag"].startswith("highway") else "amenity"): place["tag"].split(":", 1)[1],
            "name": place["name"],
        }}
        for places_found in found.values() if places_found
        for place in places_found
    ]
    safe_stops = []
    for el in elements:
        el_lat = el.get("lat")
        el_lon = el.get("lon")
        if not el_lat or not el_lon:
            continue

        distance_m = haversine_m(lat, lon, el_lat, el_lon)

        tags = el.get("tags", {})
        # Rest areas are tagged with highway=, not amenity=, so they would
        # otherwise be reported as an unknown type with priority 6.
        if tags.get("amenity"):
            amenity = tags["amenity"]
        elif tags.get("highway") == "rest_area":
            amenity = "rest_area"
        else:
            amenity = "unknown"
        name = tags.get("name") or amenity.replace("_", " ").title()

        safety_priority = {
            "police": 1,
            "hospital": 2,
            "clinic": 3,
            "rest_area": 4,
            "parking": 5,
        }.get(amenity, 6)

        safe_stops.append({
            "name": name,
            "type": amenity,
            "lat": el_lat,
            "lon": el_lon,
            "distance_m": round(distance_m),
            "distance_km": round(distance_m / 1000, 2),
            "safety_priority": safety_priority,
        })

    safe_stops.sort(key=lambda x: (x["safety_priority"], x["distance_m"]))
    return safe_stops


def validate_coordinates(lat, lon):
    """Returns (True, None) if valid, (False, error_message) if invalid."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return False, "Coordinates must be numbers"

    if not (-90 <= lat <= 90):
        return False, f"Latitude must be between -90 and 90, got {lat}"
    if not (-180 <= lon <= 180):
        return False, f"Longitude must be between -180 and 180, got {lon}"
    if lat == 0 and lon == 0:
        return False, "Coordinates (0,0) are invalid - that's the middle of the ocean"

    return True, None


def routing_chain_summary():
    """One-line description of the routing fallback chain, for /health.

    Diagnostics must never be able to break health, so any failure degrades to
    a readable string instead of raising.
    """
    try:
        return " -> ".join(
            f"{provider['id']}{'' if provider['enabled'] else ' (disabled)'}"
            for provider in describe_providers()
        )
    except Exception as exc:  # noqa: BLE001 - /health must always answer
        return f"unavailable ({type(exc).__name__})"


def _context_version():
    from context_model import VERSION

    return VERSION


def data_source_status(deep=False):
    """Health information for /health?deep=1.

    Model, indexes and providers are reported separately: a missing index is a
    different failure from a missing model, and a missing weather key is a
    different failure again. Collapsing them into one "ok" hides which part of
    the demo is actually live.
    """
    checks = {
        "crash_risk_model": crash_risk_model.health(),
        "severity_model": "ok" if severity_model is not None else "unavailable",
        "indexes": {
            "crash": "ok" if (CRASH_INDEX is not None and getattr(CRASH_INDEX, "available", False)) else "unavailable",
            "bars": "ok" if (BARS_INDEX is not None and getattr(BARS_INDEX, "available", False)) else "live_overpass",
            "safe_stops": "ok" if (SAFE_STOPS_INDEX is not None and getattr(SAFE_STOPS_INDEX, "available", False)) else "live_overpass",
        },
        "providers": {
            "weather_api_key": "set" if WEATHER_API_KEY else "missing",
            "weather_chain": ("weatherapi -> " if WEATHER_API_KEY else "") + "open-meteo -> met-norway",
            "weather_last": providers.WEATHER_STATUS["provider"],
            "places": "photon -> nominatim (OpenStreetMap)",
            "nightlife": " -> ".join(name for name, _, _ in providers.OVERPASS_SERVERS) + " -> photon",
            "provider_state": providers.provider_status(),
            "context_model": f"{_context_version()} (outside NYC)",
            "osrm": OSRM_BASE_URL,
            "routing_chain": routing_chain_summary(),
        },
    }
    if deep:
        _, weather_source = get_weather_ex(40.7580, -73.9855)
        checks["providers"]["weather"] = weather_source
        checks["providers"]["overpass"] = probe_overpass()
    return checks


def probe_overpass(timeout=6):
    data = _overpass_query(
        '[out:json][timeout:5];node["amenity"="police"](around:300,40.7580,-73.9855);out count;',
        timeout=timeout,
    )
    return "ok" if data is not None else "unreachable"
