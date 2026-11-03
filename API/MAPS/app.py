from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from main import (
    get_routes, get_via_route, get_weather_ex, get_bars_ex, init_db, log_risk,
    log_route, DB_PATH, active_alerts, alert_status, alert_lock, check_weather_alerts,
    start_alert_poller, get_crash_hotspots, get_crash_hotspots_near, get_cached_route, cache_route,
    get_nearest_safe, validate_coordinates, evaluate_weather_alerts,
    evaluate_weather_alerts_ex,
    data_source_status, get_model_metrics, get_time_features,
    BARS_INDEX, CRASH_INDEX, MODULE_DIR, severity_model,
    predict_severity,
)
from features import NYC_BBOX, in_model_area
from crash_model import risk_model
from route_engine import RoutePlanningError, current_route_hint, plan_routes
import context_model
import providers
from severity_features import CLASS_NAMES, SEVERITY_FEATURES

import concurrent.futures
import math
import threading
import time
import os
import json
import sqlite3
import numpy as np
from datetime import datetime

HEATMAP_MAX_SPAN_DEG = 0.25
SCHEMA_VERSION = "1.0.0"
# Remembered so ``safe_route`` can tell when the provider has been replaced (a
# test double, a different backend). Waypoint alternatives talk to the real
# routing service, so they only run alongside the real provider.
_STOCK_GET_ROUTES = get_routes
# Bump when the route response changes shape so stale cached routes are not served.
ROUTE_CACHE_VERSION = "v5"
MODEL_METRICS = get_model_metrics()

try:
    init_db()
except Exception as exc:  # pragma: no cover - degraded start
    # The API still serves risk scores without the request log. Failing to open
    # the SQLite file must not abort the import, otherwise a read-only
    # filesystem or a bad SOBER_DB_PATH takes the whole service down.
    print(f"[app] WARNING: could not initialise the risk log at {DB_PATH} "
          f"({type(exc).__name__}: {exc}). Request logging is disabled.")
start_alert_poller(interval_minutes=30)


def _warm_up_model() -> None:
    # The first prediction in a process pays a one-off cost of over a second
    # (scikit-learn counts CPU cores by shelling out). Pay it at startup, in the
    # background, instead of on the first user's request.
    try:
        risk_model.warm_up()
    except Exception as exc:  # pragma: no cover - warm-up is best-effort
        print(f"[app] model warm-up skipped: {type(exc).__name__}: {exc}")


threading.Thread(target=_warm_up_model, name="model-warm-up", daemon=True).start()

# Off with SOBER_CONTEXT_MODEL=0: outside NYC the API then reports "no coverage"
# exactly as it did before the context estimate existed.
CONTEXT_ENABLED = os.environ.get("SOBER_CONTEXT_MODEL", "1") != "0"
# This machine's resolver drops out for a minute at a time; reuse the last good
# address rather than fail every provider at once.
providers.install_dns_fallback()
providers.warm_up_dns()
# Background lookups that overlap with routing (nightlife venues for a trip).
_PREFETCH = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="prefetch")
# A trip box larger than this is not worth downloading every venue for.
TRIP_VENUE_MAX_KM2 = 900.0
NIGHTLIFE_RADIUS_M = 1000.0
# How long a route request waits for nightlife, counted from its start. The
# lookups overlap with routing, so within this they usually cost nothing extra;
# past it the trip is scored without nightlife (and not cached).
NIGHTLIFE_ROUTE_BUDGET_S = 4.5


def _trip_venues(origin, destination):
    """Every nightlife venue around a trip (``providers.VenueGrid``), or None.

    The box is the trip's, widened for alternatives that swing wide and for the
    1 km counting radius. None when no Overpass server covers the region or
    answers in time, or when the box is too big to fetch.
    """
    (olat, olon), (dlat, dlon) = origin, destination
    mid_lat = (olat + dlat) / 2.0
    pad_lat = max(0.025, 0.25 * max(abs(dlat - olat), abs(dlon - olon))) + 0.009
    pad_lon = pad_lat / max(0.2, math.cos(math.radians(mid_lat)))
    south, north = min(olat, dlat) - pad_lat, max(olat, dlat) + pad_lat
    west, east = min(olon, dlon) - pad_lon, max(olon, dlon) + pad_lon
    area_km2 = (north - south) * 111.32 * (east - west) * 111.32 * math.cos(math.radians(mid_lat))
    if area_km2 > TRIP_VENUE_MAX_KM2:
        return None
    return providers.nightlife_venues((south, west, north, east))


def score_point(lat, lon, hour, day_of_week, *, weather=None, bars=None, road=None,
                country=None, weather_detail=None, lookup_road=True):
    """The crash model where it covers the point, a labelled estimate elsewhere.

    Inside the NYC bounding box an uncovered cell (a river, a park) keeps the
    honest "no coverage" answer. Outside it, ``context_model`` estimates risk
    from road, time, weather, nightlife and the country's road-death rate. On a
    route the router's speed and junction density stand in for the road class.
    """
    result = risk_model.score(lat, lon, hour, day_of_week, weather=weather, bars=bars)
    if not CONTEXT_ENABLED or result.get("coverage") != "rule_based_only" or in_model_area(lat, lon):
        return result
    hint = current_route_hint(lat, lon) or {}
    if lookup_road:
        if road is None and not hint:
            road = providers.road_at(lat, lon)
        if country is None:
            country = (road or {}).get("country") or providers.country_at(lat, lon)
        if weather_detail is None:
            weather_detail = providers.current_weather(lat, lon)
    else:
        # Bulk scoring (a heatmap, a route): everything was fetched once by the
        # caller. Never look anything up per point here.
        country = country or (road or {}).get("country")
    return context_model.estimate(
        lat, lon, hour, day_of_week,
        cell=result.get("cell"),
        weather=weather_detail,
        bars=bars,
        road=road,
        country=country,
        speed_kmh=hint.get("speed_kmh"),
        junctions_per_km=hint.get("junctions_per_km"),
        maneuvers=hint.get("maneuvers"),
    )


# How long a location waits for its live conditions. Free providers can stall
# for many seconds (throttling, overload); past this the answer goes out
# without what is missing, and the lookups finish in the background so the
# next request for the place finds them cached.
CONDITIONS_DEADLINE_S = 3.0


def _conditions(lat, lon):
    """Weather, nightlife and (outside NYC) the road at a point, fetched together."""
    weather_future = _PREFETCH.submit(get_weather_ex, lat, lon)
    bars_future = _PREFETCH.submit(get_bars_ex, lat, lon)
    road_future = None if in_model_area(lat, lon) else _PREFETCH.submit(providers.road_at, lat, lon)
    pending = [f for f in (weather_future, bars_future, road_future) if f is not None]
    concurrent.futures.wait(pending, timeout=CONDITIONS_DEADLINE_S)

    def ready(future, fallback):
        if future is None or not future.done():
            return fallback
        try:
            return future.result()
        except Exception:  # noqa: BLE001 - a failed optional lookup is "unavailable"
            return fallback

    weather, weather_source = ready(weather_future, ((20, 0, 0), "fallback"))
    bars, bars_source = ready(bars_future, (0, "fallback"))
    road = ready(road_future, None)
    return weather, weather_source, bars, bars_source, road


def place_for(lat, lon, result):
    """A human name for a scored point, without another network call.

    Inside the model: the cell's 2020 neighbourhood (NTA) and borough. Outside
    NYC: the road and country the global estimate already looked up. A river or
    park beside the city borrows the nearest neighbourhood ("Near ..."). None
    when nothing nearby has a name; the app then shows coordinates.
    """
    coverage = result.get("coverage")
    if coverage == "model":
        info = risk_model.cell_info(result.get("cell"))
        if info and info.get("name"):
            return {"name": info["name"], "area": info.get("borough"), "source": "neighbourhood"}
    if coverage == "context":
        context = result.get("context") or {}
        road, country = context.get("road_name"), context.get("country_name")
        if road or country:
            return {
                "name": road or country,
                "area": country if road else None,
                "source": "road" if road else "country",
            }
        return None
    near = risk_model.nearest_cell_info(lat, lon) if risk_model.available else None
    if near:
        return {"name": f"Near {near['name']}", "area": near.get("borough"), "source": "nearest_neighbourhood"}
    return None

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

cors_origins = os.environ.get(
    "CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
).strip()
if cors_origins == "*":
    cors_origins = "*"
else:
    cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
CORS(app, origins=cors_origins, expose_headers=["Content-Type"])

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120/minute"],
    storage_uri=os.environ.get("RATE_LIMIT_STORAGE", "memory://"),
    enabled=os.environ.get("RATE_LIMIT_ENABLED", "1") != "0",
)

# The map scrubs the hour and pans, so the heatmap needs a much higher budget
# than a one-shot API. Keep the limit in one place so the regression test can
# derive its request count instead of hard-coding it.
HEATMAP_RATE_LIMIT = "60/minute"


@app.after_request
def attach_schema_version(response):
    """Every JSON response carries the contract version, including errors."""
    # A body that already names its version (the large /surface payload does)
    # is left alone: parsing and re-dumping 1,120 cells cost ~200 ms.
    if response.is_json and b'"schema_version"' not in response.get_data():
        payload = response.get_json(silent=True)
        if isinstance(payload, dict) and "schema_version" not in payload:
            payload["schema_version"] = SCHEMA_VERSION
            response.set_data(json.dumps(payload))
            response.headers["Content-Length"] = str(len(response.get_data()))
    return response


def error(message, status=400):
    return jsonify({"error": message}), status


def parse_coords(lat_key="lat", lon_key="lon"):
    """Return ((lat, lon), None) or (None, error_response)."""
    lat = request.args.get(lat_key)
    lon = request.args.get(lon_key)
    if lat is None or lon is None:
        return None, error(f"Missing required parameters: {lat_key}, {lon_key}")
    ok, message = validate_coordinates(lat, lon)
    if not ok:
        return None, error(message)
    return (float(lat), float(lon)), None


def parse_int_arg(name, default, minimum, maximum):
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error(f"{name} must be an integer")
    return max(minimum, min(maximum, value)), None


def parse_count_arg(name, default, minimum, maximum):
    """Like parse_int_arg, but a negative value is rejected instead of clamped.

    Clamping a negative count or radius answers a different question than
    the one that was asked (and can return an empty result that looks like
    "nothing is nearby").
    """
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error(f"{name} must be an integer")
    if value < 0:
        return None, error(f"{name} must not be negative")
    return max(minimum, min(maximum, value)), None


def parse_time_args():
    """Parse optional client-local time overrides.

    ``hour`` is 0-23 and ``day`` is 0 (Monday) through 6 (Sunday).  When both
    are omitted the server clock is used.
    """
    raw_hour = request.args.get("hour")
    raw_day = request.args.get("day")
    if raw_hour is None and raw_day is None:
        return (None, None), None

    hour = day = None
    if raw_hour is not None:
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError):
            return None, error("hour must be an integer between 0 and 23")
        if not 0 <= hour <= 23:
            return None, error("hour must be between 0 and 23")

    if raw_day is not None:
        try:
            day = int(raw_day)
        except (TypeError, ValueError):
            return None, error("day must be an integer between 0 (Monday) and 6 (Sunday)")
        if not 0 <= day <= 6:
            return None, error("day must be between 0 (Monday) and 6 (Sunday)")

    return (hour, day), None


STATS_CACHE_EXPIRY = 60
_stats_cache = {"data": None, "time": 0.0}


@app.route("/health")
def health():
    deep = request.args.get("deep") in ("1", "true", "yes")
    # Read from the router itself, so the list cannot drift from what is served.
    endpoints = sorted(
        {rule.rule for rule in app.url_map.iter_rules() if rule.endpoint != "static"}
    )
    return jsonify({
        "status": "ok",
        "endpoints": endpoints,
        "endpoint_count": len(endpoints),
        "coverage": {"bounding_box": list(NYC_BBOX), "model_area": "New York City"},
        "checks": data_source_status(deep=deep),
    })


@app.route("/risk", methods=["GET"])
def risk():
    coords, err = parse_coords()
    if err:
        return err
    lat, lon = coords

    time_override, err = parse_time_args()
    if err:
        return err
    hour_override, day_override = time_override
    hour, day_of_week, is_weekend = get_time_features(
        hour=hour_override, day=day_override
    )

    (temperature, rain, wind_speed), weather_source, bars, bars_source, road = _conditions(lat, lon)

    result = score_point(
        lat,
        lon,
        hour,
        day_of_week,
        weather=(temperature, rain, wind_speed),
        bars=bars,
        road=road if road is not None else {},
    )

    warnings = []
    if weather_source == "fallback":
        warnings.append("Live weather unavailable - the model used a missing-value path")
    elif weather_source == "stale":
        warnings.append("Weather refresh failed - using the last cached reading")
    if bars_source == "fallback":
        warnings.append("Nightlife data unavailable - bars treated as 0")
    if result["coverage"] == "context":
        warnings.append(
            "Outside the NYC crash model: this is an estimate from road, time, "
            "weather, nightlife and country data, not a trained crash model"
        )
    elif result["coverage"] != "model":
        warnings.append(
            "No learned crash surface for this location - model points are unavailable"
        )

    log_risk(
        "/risk", hour, is_weekend,
        result["risk_score"], result["risk_level"],
        bars, result["expected_rate"] or 0.0,
        temperature, rain, wind_speed,
    )

    return jsonify({
        "risk_score": result["risk_score"],
        "risk_level": result["risk_level"],
        "reasons": result["reasons"],
        "action": result["action"],
        "risk_breakdown": {
            "model": result["model_points"],
            "nightlife": result["nightlife_points"],
            "total": result["risk_score"],
            "model_max": 60,
            "nightlife_max": 40,
            "percentile": result["percentile"],
            "expected_rate": result["expected_rate"],
            "expected_rate_per_km2_hour": result["expected_rate_per_km2_hour"],
        },
        "expected_rate": result["expected_rate"],
        "expected_rate_per_km2_hour": result["expected_rate_per_km2_hour"],
        "percentile": result["percentile"],
        "model_points": result["model_points"],
        "nightlife_points": result["nightlife_points"],
        "model_version": result["model_version"],
        "cell": result["cell"],
        "place": place_for(lat, lon, result),
        "local_time": {
            "hour": hour,
            "day_of_week": day_of_week,
            "is_weekend": bool(is_weekend),
        },
        "coverage": result["coverage"],
        "coverage_reason": result["coverage_reason"],
        "context": result.get("context"),
        "data_quality": {
            "model": "live" if result["model_available"] else "unavailable",
            "weather": weather_source,
            "bars": bars_source,
        },
        "warnings": warnings,
        "features": {
            "hour": hour,
            "day_of_week": day_of_week,
            "is_weekend": bool(is_weekend),
            "latitude": lat,
            "longitude": lon,
            "temperature": temperature,
            "rain": rain,
            "wind_speed": wind_speed,
            "bars": bars,
            "cell": result["cell"],
        },
    })


@app.route("/safe_route", methods=["GET"])
@limiter.limit("60/minute")
def safe_route():
    origin, err = parse_coords("olat", "olon")
    if err:
        return error(f"Invalid origin: {err[0].get_json()['error']}", 400)
    destination, err = parse_coords("dlat", "dlon")
    if err:
        return error(f"Invalid destination: {err[0].get_json()['error']}", 400)

    origin_lat, origin_lon = origin
    dest_lat, dest_lon = destination

    time_override, err = parse_time_args()
    if err:
        return err
    hour_override, day_override = time_override
    hour, day_of_week, is_weekend = get_time_features(
        hour=hour_override, day=day_override
    )

    route_id = (f"{round(origin_lat, 3)}_{round(origin_lon, 3)}_"
                f"{round(dest_lat, 3)}_{round(dest_lon, 3)}_"
                f"{hour}_{day_of_week}")
    cache_key = f"{route_id}:{ROUTE_CACHE_VERSION}"
    cached = get_cached_route(cache_key)
    if cached:
        return jsonify(cached["routes"])

    # Outside NYC, nightlife for the whole trip is fetched while the router
    # works: every venue in the trip's box (counted per stretch), and one count
    # halfway along as the fallback. Both start now so their waits overlap.
    outside_trip = CONTEXT_ENABLED and not (
        in_model_area(origin_lat, origin_lon) and in_model_area(dest_lat, dest_lon)
    )
    nightlife_deadline = time.time() + NIGHTLIFE_ROUTE_BUDGET_S
    trip_venues = (
        _PREFETCH.submit(_trip_venues, (origin_lat, origin_lon), (dest_lat, dest_lon))
        if outside_trip else None
    )
    trip_count = (
        _PREFETCH.submit(get_bars_ex, (origin_lat + dest_lat) / 2.0, (origin_lon + dest_lon) / 2.0)
        if outside_trip else None
    )

    def venue_grid():
        if trip_venues is None:
            return None
        try:
            return trip_venues.result(timeout=max(0.0, nightlife_deadline - time.time()))
        except Exception:  # noqa: BLE001 - nightlife falls back to one count
            return None

    def trip_level_bars():
        if trip_count is None:
            return get_bars_ex((origin_lat + dest_lat) / 2.0, (origin_lon + dest_lon) / 2.0)
        try:
            return trip_count.result(timeout=max(0.0, nightlife_deadline - time.time()))
        except Exception:  # noqa: BLE001 - no nightlife data for this trip
            return 0, "fallback"

    def score_factory(mid_lat, mid_lon):
        (temperature, rain, wind_speed), weather_source = get_weather_ex(
            mid_lat, mid_lon
        )
        # The offline nightlife index only covers NYC. Elsewhere one live lookup
        # halfway along the trip stands for every route of it.
        outside = not in_model_area(mid_lat, mid_lon)
        bars_available = (
            BARS_INDEX is not None and getattr(BARS_INDEX, "available", False) and not outside
        )
        country = providers.country_at(mid_lat, mid_lon) if outside else None
        weather_detail = providers.current_weather(mid_lat, mid_lon) if outside else None
        fixed_bars = None
        bars_source = "offline" if bars_available else None
        grid = None if bars_available else venue_grid()
        if grid is not None:
            bars_source = "live"
        elif not bars_available:
            # No venue list: one count halfway along stands for the whole trip.
            fixed_bars = trip_level_bars()
            bars_source = fixed_bars[1]

        def score_at(lat, lon):
            if grid is not None and grid.covers(lat, lon, NIGHTLIFE_RADIUS_M):
                bars, source = grid.count(lat, lon, NIGHTLIFE_RADIUS_M), "live"
            elif grid is not None:
                # A detour beyond the downloaded box: one count for the trip.
                bars, source = trip_level_bars()
            elif fixed_bars is not None:
                bars, source = fixed_bars
            else:
                bars, source = get_bars_ex(lat, lon)
            result = score_point(
                lat,
                lon,
                hour,
                day_of_week,
                weather=(temperature, rain, wind_speed),
                bars=bars,
                country=country,
                weather_detail=weather_detail,
                lookup_road=False,
            )
            result["_bars_source"] = source
            return result

        return score_at, {
            "weather": weather_source,
            "bars": bars_source or "offline",
            "temperature": temperature,
            "rain": rain,
            "wind_speed": wind_speed,
        }

    # Extra, road-snapped alternatives through waypoints beside the fastest
    # route. Skipped when the provider has been swapped out, and switchable off.
    via_provider = (
        get_via_route
        if get_routes is _STOCK_GET_ROUTES
        and os.environ.get("SOBER_VIA_ALTERNATIVES", "1") != "0"
        else None
    )

    try:
        plan = plan_routes(
            get_routes,
            (origin_lat, origin_lon),
            (dest_lat, dest_lon),
            score_factory,
            risk_model.cell_for,
            model_version=risk_model.info.version if risk_model.info else None,
            via_provider=via_provider,
        )
    except RoutePlanningError as exc:
        return error(str(exc), exc.status)

    scored_routes = plan.routes
    for route in scored_routes:
        context = route.pop("context", {})
        route["data_quality"] = {**route.get("data_quality", {}), **context}
        route["bars"] = route.get("data_quality", {}).get("bars")

    coverage_values = {route["coverage"] for route in scored_routes}
    if coverage_values == {"model"}:
        coverage = "model"
    elif coverage_values == {"context"}:
        coverage = "context"
    elif coverage_values & {"model", "mixed"}:
        coverage = "mixed"
    else:
        coverage = "rule_based_only"

    warnings = list(plan.warnings)
    weather_sources = {
        route["data_quality"].get("weather") for route in scored_routes
    }
    if "fallback" in weather_sources:
        warnings.append("Live weather unavailable - missing-value path")
    if "stale" in weather_sources:
        warnings.append("Weather refresh failed - cached readings were used")
    bars_sources = {route["data_quality"].get("bars") for route in scored_routes}
    if "fallback" in bars_sources:
        warnings.append("Nightlife data unavailable for part of this trip - counted as none")
    if coverage == "context":
        warnings.append(
            "Outside the NYC crash model: route risk is an estimate from routed "
            "speed, junctions, time, weather, nightlife and the country's road-death rate"
        )
    elif coverage != "model":
        warnings.append(
            "Part of this route is outside the model's training coverage; "
            "uncovered distance has no crash points"
        )
    if plan.detour_synthesis["attempted"]:
        if plan.detour_synthesis["scored_count"]:
            warnings.append(
                f"Synthesized {plan.detour_synthesis['scored_count']} detour "
                "candidate(s) because the provider returned fewer than two "
                "distinct routes"
            )
        else:
            warnings.append(
                "Fewer than two distinct routes were returned and no synthetic "
                "detour could be scored"
            )

    best = scored_routes[0]
    fastest = next(
        (route for route in scored_routes if route.get("is_fastest")),
        scored_routes[0],
    )
    reduction = fastest["expected_crashes_total"] - best["expected_crashes_total"]
    reduction_percent = (
        reduction / fastest["expected_crashes_total"] * 100.0
        if fastest["expected_crashes_total"] > 0
        else 0.0
    )
    detour = plan.detour_synthesis
    estimated = coverage == "context"
    # Outside the trained area the totals are relative-risk units, not crash
    # counts, so say "estimated risk" and quote the percentage only.
    measure = "estimated risk" if estimated else "expected injury crashes"
    amount = (
        f"{reduction_percent:.1f}%" if estimated
        else f"{reduction:.4f} per trip ({reduction_percent:.1f}%)"
    )
    if detour["attempted"] and detour["scored_count"]:
        if detour["improved"]:
            text = (
                f"Synthesized detour reduces {measure} by {amount} versus "
                "the fastest route"
            )
        else:
            text = (
                "No synthesized detour reduced expected exposure; the fastest "
                "route remains the lowest-exposure option"
            )
    elif len(scored_routes) > 1:
        if reduction <= 1e-9:
            text = "The fastest route is also the lowest-exposure route"
        else:
            text = (
                f"{best['route_type'].title()} route reduces {measure} by "
                f"{amount} versus the fastest route"
            )
    else:
        text = "Only one route is available; there is no alternative to compare"

    log_route(
        len(scored_routes), best["average_risk"], best["risk_level"],
        best["distance_km"], best["duration_min"],
    )

    response_data = {
        "route_id": route_id,
        "origin": {"lat": origin_lat, "lon": origin_lon},
        "destination": {"lat": dest_lat, "lon": dest_lon},
        "recommended_route": 0,
        "total_routes": len(scored_routes),
        "provider_routes": plan.distinct_provider_routes,
        "routing_source": plan.provider_source,
        "provider_source": plan.provider_source,
        "model_version": (
            context_model.VERSION if estimated
            else risk_model.info.version if risk_model.info else None
        ),
        "local_time": {
            "hour": hour,
            "day_of_week": day_of_week,
            "is_weekend": bool(is_weekend),
        },
        "coverage": coverage,
        "detour_synthesis": detour,
        "via_alternatives": plan.via_alternatives,
        "warnings": warnings,
        "recommendation": {
            "text": text,
            "risk_reduction": round(reduction, 6),
            "risk_reduction_percent": round(reduction_percent, 1),
            "detour_improved": bool(detour["improved"]),
            "basis": "relative_risk_estimate" if estimated else "expected_injury_crashes",
        },
        "routes": scored_routes,
    }

    # A trip scored without live weather or nightlife is answered but not kept:
    # the next request should try the providers again, not reuse the gap.
    if "fallback" not in weather_sources and "fallback" not in bars_sources:
        cache_route(cache_key, {"routes": response_data, "last_updated": time.time()})
    return jsonify(response_data)



@app.route("/geocode", methods=["GET"])
@limiter.limit("90/minute")
def geocode():
    """Place search for the app's search boxes, Nominatim-shaped.

    Photon, then Nominatim, then an offline list of major cities, all inside a
    few seconds; 503 only when none of them has anything.
    """
    text = (request.args.get("q") or "").strip()
    if len(text) < 2:
        return jsonify([])
    if len(text) > 200:
        return error("Search text is too long", 400)
    limit, err = parse_int_arg("limit", request.args.get("limit", 6), 1, 10)
    if err:
        return err
    results = providers.search_places(text, limit)
    if results is None:
        return error("Place search is unavailable right now", 503)
    return jsonify(results)


@app.route("/heatmap", methods=["GET"])
@limiter.limit(HEATMAP_RATE_LIMIT)
def heatmap():
    point_a, err = parse_coords("lat1", "lon1")
    if err:
        return err
    point_b, err = parse_coords("lat2", "lon2")
    if err:
        return err

    lat1, lon1 = point_a
    lat2, lon2 = point_b

    if abs(lat2 - lat1) > HEATMAP_MAX_SPAN_DEG or abs(lon2 - lon1) > HEATMAP_MAX_SPAN_DEG:
        return error(
            f"Area too large (max {HEATMAP_MAX_SPAN_DEG} degrees per side)", 400
        )

    grid_size, err = parse_int_arg("grid_size", request.args.get("grid", 6), 2, 8)
    if err:
        return err

    lat_steps = np.linspace(lat1, lat2, grid_size)
    lon_steps = np.linspace(lon1, lon2, grid_size)
    grid_points = [
        {"lat": round(float(lat), 4), "lon": round(float(lon), 4)}
        for lat in lat_steps for lon in lon_steps
    ]

    time_override, err = parse_time_args()
    if err:
        return err
    hour_override, day_override = time_override
    hour, day_of_week, is_weekend = get_time_features(
        hour=hour_override, day=day_override
    )
    # Weather is effectively constant across one heatmap tile. Fetch it once
    # at the centre instead of once per cell.
    (temperature, rain, wind_speed), weather_source = get_weather_ex(
        (lat1 + lat2) / 2, (lon1 + lon2) / 2
    )

    centre_lat, centre_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
    tile_outside = not in_model_area(centre_lat, centre_lon)
    cell_bars = None
    if tile_outside and CONTEXT_ENABLED:
        # Outside NYC: nightlife for every cell in one Overpass request, running
        # while the road types are looked up; one count for the tile if that fails.
        counts_future = _PREFETCH.submit(
            providers.nightlife_counts,
            [(p["lat"], p["lon"]) for p in grid_points],
            NIGHTLIFE_RADIUS_M,
        )
        # Road type at a sparse sample (at most 4 x 4) shared by nearby cells:
        # 64 reverse lookups per tile is more than a free geocoder should take.
        step = max(1, grid_size // 4)
        sample = [
            grid_points[row * grid_size + col]
            for row in range(0, grid_size, step)
            for col in range(0, grid_size, step)
        ]
        sampled = providers.roads_many([(p["lat"], p["lon"]) for p in sample], deadline_s=4.0)
        road_at_point = {}
        for p in grid_points:
            nearest = min(
                range(len(sample)),
                key=lambda i: (sample[i]["lat"] - p["lat"]) ** 2 + (sample[i]["lon"] - p["lon"]) ** 2,
            )
            road_at_point[(p["lat"], p["lon"])] = sampled[nearest]
        tile_country = next(
            (r["country"] for r in sampled if r and r.get("country")), None
        ) or providers.country_at(centre_lat, centre_lon)
        tile_weather = providers.current_weather(centre_lat, centre_lon)
        try:
            counts = counts_future.result(timeout=6.0)
        except Exception:  # noqa: BLE001 - fall back to one count for the tile
            counts = None
        if counts is not None:
            cell_bars = {(p["lat"], p["lon"]): n for p, n in zip(grid_points, counts)}
            tile_bars = None
        else:
            tile_bars = get_bars_ex(centre_lat, centre_lon)
    else:
        tile_bars, road_at_point, tile_country, tile_weather = None, {}, None, None

    def score_cell(point):
        try:
            if cell_bars is not None:
                bars, bars_source = cell_bars[(point["lat"], point["lon"])], "live"
            elif tile_bars is not None:
                bars, bars_source = tile_bars
            else:
                bars, bars_source = get_bars_ex(point["lat"], point["lon"])
            result = score_point(
                point["lat"],
                point["lon"],
                hour,
                day_of_week,
                weather=(temperature, rain, wind_speed),
                bars=bars,
                road=road_at_point.get((point["lat"], point["lon"])),
                country=tile_country,
                weather_detail=tile_weather,
                lookup_road=False,
            )
            return {
                "lat": point["lat"],
                "lon": point["lon"],
                "cell": result["cell"],
                "expected_rate": result["expected_rate"],
                "expected_rate_per_km2_hour": result["expected_rate_per_km2_hour"],
                "percentile": result["percentile"],
                "model_points": result["model_points"],
                "nightlife_points": result["nightlife_points"],
                "risk_score": result["risk_score"],
                "risk_level": result["risk_level"],
                "coverage": result["coverage"],
                "data_quality": {
                    "model": "live" if result["model_available"] else "unavailable",
                    "weather": weather_source,
                    "bars": bars_source,
                },
                "error": False,
            }
        except Exception as e:
            print(f"Heatmap point error: {e}")
            return {
                "lat": point["lat"],
                "lon": point["lon"],
                "cell": None,
                "expected_rate": None,
                "expected_rate_per_km2_hour": None,
                "percentile": None,
                "model_points": 0,
                "nightlife_points": 0,
                "risk_score": None,
                "risk_level": "UNKNOWN",
                "coverage": "rule_based_only",
                "data_quality": {},
                "error": True,
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(score_cell, grid_points))

    failed = sum(1 for r in results if r["error"])
    model_cells = sum(1 for r in results if r["coverage"] == "model")
    context_cells = sum(1 for r in results if r["coverage"] == "context")
    if model_cells == len(results):
        coverage = "model"
    elif context_cells and not model_cells:
        coverage = "context"
    elif model_cells == 0:
        coverage = "rule_based_only"
    else:
        coverage = "mixed"

    warnings = []
    if failed:
        warnings.append(f"{failed} of {len(results)} cells could not be scored")
    if coverage == "rule_based_only":
        warnings.append("No learned crash surface for this area - no model points")
    elif coverage == "mixed":
        warnings.append(
            f"{len(results) - model_cells} of {len(results)} cells are outside the model's training coverage"
        )

    return jsonify({
        "grid_size": grid_size,
        "total_points": len(results),
        "model_cells": model_cells,
        "failed_points": failed,
        "coverage": coverage,
        "model_version": risk_model.info.version if risk_model.info else None,
        "warnings": warnings,
        "bounds": {"lat1": lat1, "lon1": lon1, "lat2": lat2, "lon2": lon2},
        "heatmap": results,
    })


SURFACE_RATE_LIMIT = "90/minute"
#: Where the surface's single weather reading is taken (the city is ~50 km
#: across; one reading per hour is what /heatmap does per tile).
SURFACE_WEATHER_POINT = (40.7580, -73.9855)
SURFACE_CACHE_S = 300
_surface_cache: dict = {}
_surface_lock = threading.Lock()
_surface_bars = None


def _surface_bar_counts():
    """Nightlife venues within 1 km of every model cell's centre (offline, static)."""
    global _surface_bars
    if _surface_bars is None:
        cells = risk_model.cells
        if cells is None:
            return None
        if BARS_INDEX is not None and getattr(BARS_INDEX, "available", False):
            _surface_bars = BARS_INDEX.counts(
                cells["lat"].to_numpy(dtype=float),
                cells["lon"].to_numpy(dtype=float),
                radius_km=NIGHTLIFE_RADIUS_M / 1000.0,
            )
        else:
            _surface_bars = np.zeros(len(cells), dtype=int)
    return _surface_bars


@app.route("/surface", methods=["GET"])
@limiter.limit(SURFACE_RATE_LIMIT)
def surface():
    """Every cell of the NYC crash-model surface at one hour: the whole city.

    /heatmap samples an 8 x 8 grid, which leaves the map as scattered dots a
    few kilometres apart. The model only covers 1,120 cells, so this scores all
    of them in one batched prediction (tens of milliseconds) and the map shows
    the real surface. Each cell carries the numbers /risk gives for a point at
    its centre.
    """
    time_override, err = parse_time_args()
    if err:
        return err
    hour_override, day_override = time_override
    hour, day_of_week, is_weekend = get_time_features(hour=hour_override, day=day_override)
    if not risk_model.available:
        return error("The crash model is not loaded", 503)

    (temperature, rain, wind_speed), weather_source = get_weather_ex(*SURFACE_WEATHER_POINT)
    key = (hour, day_of_week, datetime.now().date().isoformat(), temperature, rain, wind_speed)
    with _surface_lock:
        cached = _surface_cache.get(key)
        if cached and time.time() - cached[0] < SURFACE_CACHE_S:
            return app.response_class(cached[1], mimetype="application/json")

    bars = _surface_bar_counts()
    cells = [
        # The map needs a few fields per hex; rounding halves the payload.
        {
            "cell": cell["cell"],
            "lat": cell["lat"],
            "lon": cell["lon"],
            "name": cell["name"],
            "borough": cell["borough"],
            "expected_rate": float(f"{cell['expected_rate']:.6g}"),
            "percentile": round(cell["percentile"], 2),
            "model_points": cell["model_points"],
            "nightlife_points": cell["nightlife_points"],
            "bars": cell["bars"],
            "risk_score": cell["risk_score"],
            "risk_level": cell["risk_level"],
            "coverage": cell["coverage"],
        }
        for cell in risk_model.score_cells(
            hour, day_of_week, weather=(temperature, rain, wind_speed), bars=bars
        )
    ]
    warnings = []
    if weather_source == "fallback":
        warnings.append("Live weather unavailable - neutral weather was assumed")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "hour": hour,
        "day_of_week": day_of_week,
        "is_weekend": bool(is_weekend),
        "coverage": "model",
        "model_version": risk_model.info.version if risk_model.info else None,
        "cell_count": len(cells),
        "data_quality": {
            "model": "live",
            "weather": weather_source,
            "bars": "offline" if BARS_INDEX is not None and getattr(BARS_INDEX, "available", False) else "unavailable",
        },
        "warnings": warnings,
        "cells": cells,
    }
    body = json.dumps(payload, separators=(",", ":"))
    with _surface_lock:
        # One entry per hour/day/weather; drop the rest so it cannot grow.
        if len(_surface_cache) > 200:
            _surface_cache.clear()
        _surface_cache[key] = (time.time(), body)
    return app.response_class(body, mimetype="application/json")


@app.route("/stats", methods=["GET"])
def stats():
    if (
        _stats_cache["data"] is not None
        and time.time() - _stats_cache["time"] < STATS_CACHE_EXPIRY
    ):
        return jsonify(_stats_cache["data"])
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM risk_logs")
        total_risk_requests = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM route_logs")
        total_route_requests = c.fetchone()[0]

        c.execute("SELECT AVG(risk_score) FROM risk_logs")
        avg_risk = round(c.fetchone()[0] or 0, 1)

        c.execute("""
            SELECT hour, AVG(risk_score) as avg_score
            FROM risk_logs
            GROUP BY hour
            ORDER BY avg_score DESC LIMIT 1
        """)
        dangerous_hour = c.fetchone()

        c.execute("""
            SELECT risk_level, COUNT(*)
            FROM risk_logs
            GROUP BY risk_level
        """)
        breakdown = dict(c.fetchall())

        data = {
            "total_requests": {"risk": total_risk_requests, "routes": total_route_requests},
            "average_risk_score": avg_risk,
            "most_dangerous_hour": {
                "hour": dangerous_hour[0] if dangerous_hour else None,
                "average_score": round(dangerous_hour[1], 1) if dangerous_hour else None,
            },
            "risk_level_breakdown": breakdown,
            "scope": "NYC demo build; aggregate request outcomes only",
            "privacy_note": "No coordinates are stored. The log holds endpoint, hour, score and band only.",
        }
        _stats_cache["data"] = data
        _stats_cache["time"] = time.time()
        return jsonify(data)
    except Exception as e:
        print(f"Stats error: {e}")
        return error("Unable to read usage statistics", 500)
    finally:
        if conn is not None:
            conn.close()


@app.route("/alerts", methods=["GET"])
def alerts():
    with alert_lock:
        current_alerts = list(active_alerts)
        status = dict(alert_status)

    return jsonify({
        "total_alerts": len(current_alerts),
        "alerts": current_alerts,
        "monitored_locations": status.get("locations_checked") or 3,
        "next_check": "Every 30 minutes",
        "last_checked": status.get("last_checked"),
        "data_quality": status.get("data_quality", "unknown"),
        "warning": status.get("warning"),
    })


@app.route("/alerts/check_location", methods=["GET"])
def alerts_check_location():
    coords, err = parse_coords()
    if err:
        return err

    lat, lon = coords
    name = request.args.get("name", "Custom Location")
    # The source matters: when the weather provider is unreachable the values
    # fall back to neutral defaults (20 C, no rain, no wind), which cross no
    # threshold and therefore produce zero alerts. Without reporting the source
    # a failed lookup is indistinguishable from genuinely clear weather.
    alerts_for_loc, weather_source = evaluate_weather_alerts_ex(name, lat, lon)

    return jsonify({
        "location": {"name": name, "lat": lat, "lon": lon},
        "total_alerts": len(alerts_for_loc),
        "alerts": alerts_for_loc,
        "message": "Location check completed",
        "weather_source": weather_source,
    })


@app.route("/ml/stats", methods=["GET"])
def ml_stats():
    if severity_model is not None:
        permutation = MODEL_METRICS.get("permutation_importance") or {}
        if permutation:
            importance = {
                feat: round(
                    float(values.get("mean", 0)) if isinstance(values, dict) else float(values),
                    4,
                )
                for feat, values in permutation.items()
            }
            importance_method = "permutation importance (drop in macro-F1)"
        else:
            importances = getattr(severity_model, "feature_importances_", None)
            if importances is None:
                importance = {}
                importance_method = "unavailable"
            else:
                importance = {
                    feat: round(float(imp), 3)
                    for feat, imp in zip(SEVERITY_FEATURES, importances)
                }
                importance_method = "model impurity importance"
        # HistGradientBoosting exposes max_iter; forests expose n_estimators.
        n_estimators = int(
            getattr(severity_model, "n_estimators", 0)
            or getattr(severity_model, "max_iter", 0)
            or 0
        )
    else:
        importance = {}
        importance_method = "unavailable"
        n_estimators = 0

    ablation = MODEL_METRICS.get("ablation_context_only") or {}

    return jsonify({
        "model": MODEL_METRICS.get("model") or "Unavailable",
        "version": "3.0",
        "experimental": True,
        "used_for_risk_scoring": False,
        "status": MODEL_METRICS.get(
            "status",
            "research only - retrospective severity model, excluded from /risk",
        ),
        "n_estimators": n_estimators,
        "features": SEVERITY_FEATURES,
        "training_records": MODEL_METRICS.get("training_records", 0),
        "cross_validation_accuracy": MODEL_METRICS.get("cv_accuracy_mean", 0),
        "cv_std": MODEL_METRICS.get("cv_accuracy_std", 0),
        "baseline_accuracy": MODEL_METRICS.get("baseline_accuracy"),
        "macro_f1": MODEL_METRICS.get("macro_f1"),
        "baseline_macro_f1": MODEL_METRICS.get("baseline_macro_f1"),
        "roc_auc_macro": MODEL_METRICS.get("roc_auc_macro"),
        "fatal_pr_auc": MODEL_METRICS.get("fatal_pr_auc"),
        "balanced_accuracy": MODEL_METRICS.get("balanced_accuracy"),
        "brier_score": MODEL_METRICS.get("brier_score"),
        "per_class_recall": MODEL_METRICS.get("per_class_recall", {}),
        "per_class_precision": MODEL_METRICS.get("per_class_precision", {}),
        "confusion_matrix": MODEL_METRICS.get("confusion_matrix"),
        "target": "crash_severity",
        "classes": CLASS_NAMES,
        "feature_importance": importance,
        "importance_method": importance_method,
        "dataset": MODEL_METRICS.get("dataset"),
        "ablation_context_only": (
            {
                "accuracy": ablation.get("accuracy"),
                "macro_f1": ablation.get("macro_f1"),
                "roc_auc_macro": ablation.get("roc_auc_macro"),
                "fatal_recall": (ablation.get("per_class_recall") or {}).get("fatal_risk"),
                "features": MODEL_METRICS.get("context_features", []),
            }
            if ablation
            else None
        ),
        # Measured on held-out data by train.py. Class weighting makes the fatal
        # score much larger than the real fatality rate; publishing the ratio is
        # the difference between a documented trade-off and a misleading number.
        "calibration": MODEL_METRICS.get("calibration"),
        "honesty": MODEL_METRICS.get("honesty", []),
        "limitations": MODEL_METRICS.get("limitations", [
            "Trained only on NYC crash records; no non-crash examples.",
            "Severity labels describe crashes that already happened, not crash likelihood.",
            "Reported metrics must be read next to the majority-class baseline.",
        ]),
    })


@app.route("/ml/severity", methods=["GET"])
@limiter.limit("60/minute")
def ml_severity():
    """Score one described crash scenario with the severity model.

    This is the only endpoint that calls the model. It is deliberately separate
    from ``/risk``: the model needs post-crash attributes (vehicle type,
    contributing factor), so it describes a crash that has already happened
    rather than predicting one.
    """
    try:
        hour = int(request.args.get("hour", datetime.now().hour))
    except (TypeError, ValueError):
        return error("hour must be an integer between 0 and 23", 400)
    if not 0 <= hour <= 23:
        return error("hour must be between 0 and 23", 400)

    try:
        month = int(request.args.get("month", datetime.now().month))
    except (TypeError, ValueError):
        return error("month must be an integer between 1 and 12", 400)
    if not 1 <= month <= 12:
        return error("month must be between 1 and 12", 400)

    day_arg = request.args.get("day")
    if day_arg is None:
        is_weekend = 1 if datetime.now().weekday() >= 5 else 0
    else:
        try:
            day = int(day_arg)
        except (TypeError, ValueError):
            return error("day must be an integer between 0 and 6 (Monday=0)", 400)
        if not 0 <= day <= 6:
            return error("day must be between 0 and 6 (Monday=0)", 400)
        is_weekend = 1 if day >= 5 else 0

    if request.args.get("lat") is None and request.args.get("lon") is None:
        # Times Square: the app is NYC-only, and a scenario endpoint should be
        # usable without hunting for coordinates first.
        lat, lon = 40.758, -73.9855
    else:
        coords, err = parse_coords()
        if err:
            return err
        lat, lon = coords

    vehicles = request.args.getlist("vehicle")
    factors = request.args.getlist("factor")
    borough = request.args.get("borough")

    result = predict_severity(
        hour=hour,
        is_weekend=is_weekend,
        lat=lat,
        lon=lon,
        month=month,
        borough=borough,
        vehicles=vehicles,
        factors=factors,
    )
    if result is None:
        return error("The severity model is not loaded", 503)

    fatal_ratio = (
        (MODEL_METRICS.get("calibration") or {})
        .get("per_class", {})
        .get("fatal_risk", {})
        .get("ratio")
    )
    inflation = (
        f" On held-out data the fatal score averages about {fatal_ratio}x the "
        "observed fatality rate."
        if fatal_ratio
        else ""
    )

    return jsonify({
        "predicted": result["predicted"],
        "confidence": result["confidence"],
        "probabilities": result["probabilities"],
        "input": {
            "hour": hour,
            "month": month,
            "is_weekend": is_weekend,
            "lat": lat,
            "lon": lon,
            "borough": borough,
            "vehicles": vehicles,
            "factors": factors,
        },
        "model": MODEL_METRICS.get("model", "unknown"),
        "experimental": True,
        "used_for_risk_scoring": False,
        "probability_semantics": "class_weighted",
        "note": (
            "Retrospective model: vehicle type and contributing factor are "
            "recorded after a crash, so this describes a crash rather than "
            "predicting one. Probabilities are class-weighted, not calibrated, "
            "so compare scenarios against each other instead of reading "
            "fatal_risk as a real-world fatality rate." + inflation
        ),
    })


@app.route("/ml/confusion_matrix", methods=["GET"])
def confusion_matrix_img():
    from flask import send_file
    path = os.path.join(MODULE_DIR, "confusion_matrix.png")
    if not os.path.exists(path):
        return error("confusion_matrix.png not generated yet", 404)
    return send_file(path, mimetype="image/png")


@app.route("/ml/feature_importance", methods=["GET"])
def feature_importance_img():
    from flask import send_file
    path = os.path.join(MODULE_DIR, "feature_importance.png")
    if not os.path.exists(path):
        return error("feature_importance.png not generated yet", 404)
    return send_file(path, mimetype="image/png")


@app.route("/risk/trend", methods=["GET"])
def risk_trend():
    coords, err = parse_coords()
    if err:
        return err
    lat, lon = coords

    time_override, err = parse_time_args()
    if err:
        return err
    _, day_override = time_override
    day = day_override if day_override is not None else datetime.now().weekday()
    is_weekend = 1 if day >= 5 else 0
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    (temperature, rain, wind_speed), weather_source, bars, bars_source, road = _conditions(lat, lon)

    trend = []
    for hour in range(24):
        result = score_point(
            lat,
            lon,
            hour,
            day,
            weather=(temperature, rain, wind_speed),
            bars=bars,
            road=road if road is not None else {},
        )
        trend.append({
            "hour": hour,
            "hour_label": f"{hour:02d}:00",
            "expected_rate": result["expected_rate"],
            "expected_rate_per_km2_hour": result["expected_rate_per_km2_hour"],
            "percentile": result["percentile"],
            "model_points": result["model_points"],
            "nightlife_points": result["nightlife_points"],
            "risk_score": result["risk_score"],
            "risk_level": result["risk_level"],
            "coverage": result["coverage"],
            "reasons": result["reasons"],
            "action": result["action"],
        })

    peak = max(trend, key=lambda x: x["risk_score"])
    safest = min(trend, key=lambda x: x["risk_score"])
    first = trend[0]

    warnings = []
    if weather_source == "fallback":
        warnings.append("Live weather unavailable - the model used a missing-value path")
    elif weather_source == "stale":
        warnings.append("Weather refresh failed - using the last cached reading")
    if first["model_points"] == 0 and risk_model.available:
        warnings.append("No learned crash surface for this location")

    return jsonify({
        "location": {"lat": lat, "lon": lon},
        "day": day_names[day],
        "is_weekend": bool(is_weekend),
        "coverage": first["coverage"],
        "model_version": risk_model.info.version if risk_model.info else None,
        "data_quality": {
            "model": "live" if risk_model.available else "unavailable",
            "weather": weather_source,
            "bars": bars_source,
        },
        "warnings": warnings,
        "trend": trend,
        "peak_risk": {
            "hour": peak["hour"],
            "hour_label": peak["hour_label"],
            "risk_score": peak["risk_score"],
            "risk_level": peak["risk_level"],
            "percentile": peak["percentile"],
        },
        "safest_time": {
            "hour": safest["hour"],
            "hour_label": safest["hour_label"],
            "risk_score": safest["risk_score"],
            "risk_level": safest["risk_level"],
            "percentile": safest["percentile"],
        },
        "average_risk": round(sum(h["risk_score"] for h in trend) / 24, 1),
    })


@app.route("/hotspots", methods=["GET"])
def hotspots():
    """Historical crash clusters: the city's busiest, or the busiest near a point.

    With ``lat`` and ``lon`` the list is local (within ``radius`` metres, 2 km
    by default) and each entry carries its distance. Outside New York it is
    empty: the crash records only cover the city.
    """
    near = None
    if request.args.get("lat") is not None or request.args.get("lon") is not None:
        coords, err = parse_coords()
        if err:
            return err
        radius, err = parse_count_arg("radius", 2000, 100, 10000)
        if err:
            return err
        near = {"lat": coords[0], "lon": coords[1], "radius_m": radius}
    try:
        top_k, err = parse_count_arg("top_k", 10, 0, 50)
        if err:
            return err
        if near is None:
            data = get_crash_hotspots(top_k=top_k)
        else:
            data = get_crash_hotspots_near(near["lat"], near["lon"], near["radius_m"], top_k=top_k)
    except Exception as e:
        print(f"Hotspots error: {e}")
        return error("Unable to load crash hotspots", 500)

    payload = {
        "source": "crashes.csv (offline analysis, NYC only)",
        "total_hotspots": len(data),
        "hotspots": data,
    }
    if near is not None:
        payload["near"] = near
    return jsonify(payload)


@app.route("/nearest_safe", methods=["GET"])
def nearest_safe():
    coords, err = parse_coords()
    if err:
        return err
    lat, lon = coords

    radius, err = parse_count_arg("radius", 3000, 100, 10000)
    if err:
        return err

    stops = get_nearest_safe(lat, lon, radius)
    if not stops:
        return jsonify({
            "message": "No safe stops found nearby (or Overpass is unavailable)",
            "suggestion": "Increase radius or try a different location",
            "stops": [],
        })

    # get_nearest_safe() returns the stops ordered by safety priority first and
    # distance second. That priority ranking is still useful, but this endpoint
    # is "nearest_safe" and it publishes `nearest_stop` plus a "Nearest safe
    # stop" message - both of which promise the *closest* stop. Ordering the
    # payload by distance makes that promise true and keeps the list
    # self-consistent (stops[0] IS nearest_stop, and no client can be misled
    # by a nearer stop being listed further down). safety_priority stays on
    # every entry so a client can still re-rank by type if it wants to.
    stops_by_distance = sorted(stops, key=lambda stop: stop["distance_m"])
    nearest = stops_by_distance[0]
    return jsonify({
        "location": {"lat": lat, "lon": lon},
        "nearest_stop": nearest,
        "total_found": len(stops),
        "search_radius_m": radius,
        "stops": stops_by_distance[:10],
        "message": f"Nearest safe stop: {nearest['name']} ({nearest['distance_m']}m away)",
    })


@app.route("/risk/compare", methods=["GET"])
def risk_compare():
    point_a, err = parse_coords("lat1", "lon1")
    if err:
        return error(f"Invalid location A: {err[0].get_json()['error']}", 400)
    point_b, err = parse_coords("lat2", "lon2")
    if err:
        return error(f"Invalid location B: {err[0].get_json()['error']}", 400)

    lat1, lon1 = point_a
    lat2, lon2 = point_b

    time_override, err = parse_time_args()
    if err:
        return err
    hour_override, day_override = time_override

    hour, day_of_week, _ = get_time_features(hour=hour_override, day=day_override)

    def location_payload(lat, lon):
        (temperature, rain, wind_speed), weather_source, bars, bars_source, road = _conditions(lat, lon)
        result = score_point(
            lat,
            lon,
            hour,
            day_of_week,
            weather=(temperature, rain, wind_speed),
            bars=bars,
            road=road if road is not None else {},
        )
        warnings = []
        if weather_source == "fallback":
            warnings.append("Live weather unavailable - the model used a missing-value path")
        elif weather_source == "stale":
            warnings.append("Weather refresh failed - using the last cached reading")
        if bars_source == "fallback":
            warnings.append("Nightlife data unavailable - bars treated as 0")
        if result["coverage"] != "model":
            warnings.append("No learned crash surface for this location")
        return {
            "lat": lat,
            "lon": lon,
            "risk_score": result["risk_score"],
            "risk_level": result["risk_level"],
            "reasons": result["reasons"],
            "action": result["action"],
            "risk_breakdown": {
                "model": result["model_points"],
                "nightlife": result["nightlife_points"],
                "total": result["risk_score"],
                "model_max": 60,
                "nightlife_max": 40,
                "percentile": result["percentile"],
                "expected_rate": result["expected_rate"],
                "expected_rate_per_km2_hour": result["expected_rate_per_km2_hour"],
            },
            "expected_rate": result["expected_rate"],
            "expected_rate_per_km2_hour": result["expected_rate_per_km2_hour"],
            "percentile": result["percentile"],
            "model_points": result["model_points"],
            "nightlife_points": result["nightlife_points"],
            "model_version": result["model_version"],
            "cell": result["cell"],
            "place": place_for(lat, lon, result),
            "coverage": result["coverage"],
            "data_quality": {
                "model": "live" if result["model_available"] else "unavailable",
                "weather": weather_source,
                "bars": bars_source,
            },
            "warnings": warnings,
        }

    payload_a = location_payload(lat1, lon1)
    payload_b = location_payload(lat2, lon2)
    score_a = payload_a["risk_score"]
    score_b = payload_b["risk_score"]

    safer = "A" if score_a < score_b else "B" if score_b < score_a else "EQUAL"
    difference = abs(score_a - score_b)
    if safer == "A":
        recommendation = f"Location A is safer by {difference:.1f} points"
    elif safer == "B":
        recommendation = f"Location B is safer by {difference:.1f} points"
    else:
        recommendation = "Both locations have equal risk"

    return jsonify({
        "location_a": payload_a,
        "location_b": payload_b,
        "safer_location": safer,
        "risk_difference": round(difference, 1),
        "recommendation": recommendation,
    })


@app.errorhandler(404)
def not_found(_):
    return error("Not found", 404)


@app.errorhandler(405)
def method_not_allowed(_):
    return error("Method not allowed", 405)


@app.errorhandler(500)
def server_error(_):
    return error("Internal server error", 500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
