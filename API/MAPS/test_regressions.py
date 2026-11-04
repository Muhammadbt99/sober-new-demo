"""Regression tests for the hardened API/MAPS contract.

Run from the API/MAPS directory:

    python -m pytest -q test_regressions.py

The suite is deliberately offline: the app is imported with the network
stubbed out, every test runs with sockets disabled, and the known
network-backed helpers are replaced with deterministic fakes.
"""

import inspect
import os
import socket
import sys
import time

import pytest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Requests in this module come from the same test-client address. The shared
# ``client`` fixture disables the limiter; the dedicated rate-limit test
# re-enables it after resetting the in-memory counters.


# ---------------------------------------------------------------------------
# Import the Flask app without letting its bootstrap code hit the network.
# app.py calls init_db() / check_weather_alerts() / start_alert_poller() at
# import time.  We import main first, neutralise the alert bootstrap, and make
# requests fail fast for the (short) duration of the app import.
# ---------------------------------------------------------------------------
main_module = None
app_module = None
app = None
APP_IMPORT_ERROR = None

try:
    import requests as _requests

    _real_get, _real_post = _requests.get, _requests.post

    def _offline_request(*args, **kwargs):
        raise _requests.RequestException("network disabled while importing app")

    _requests.get = _offline_request
    _requests.post = _offline_request
    try:
        import main as main_module

        _real_check_alerts = main_module.check_weather_alerts
        _real_alert_poller = main_module.start_alert_poller
        for _bootstrap_name in ("check_weather_alerts", "start_alert_poller"):
            if hasattr(main_module, _bootstrap_name):
                setattr(main_module, _bootstrap_name, lambda *a, **k: [])

        import app as app_module

        app = app_module.app

        # Restore the real implementations for tests; app.py only needed the
        # stubs while its import-time bootstrap ran.
        main_module.check_weather_alerts = _real_check_alerts
        main_module.start_alert_poller = _real_alert_poller
    finally:
        _requests.get, _requests.post = _real_get, _real_post
except Exception as exc:  # pragma: no cover - surfaced by the tests
    APP_IMPORT_ERROR = exc


def _loaded_modules():
    return [module for module in (main_module, app_module) if module is not None]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail any accidental socket use immediately, and skip retry sleeps."""

    class _BlockedSocket(socket.socket):
        def connect(self, *args, **kwargs):
            raise OSError("network access disabled in regression tests")

        def connect_ex(self, *args, **kwargs):
            raise OSError("network access disabled in regression tests")

    def _blocked_create_connection(*args, **kwargs):
        raise OSError("network access disabled in regression tests")

    monkeypatch.setattr(socket, "socket", _BlockedSocket)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    monkeypatch.setattr(time, "sleep", lambda *_: None)


@pytest.fixture
def offline_deps(monkeypatch):
    """Replace known network-backed helpers with deterministic offline values."""
    fakes = {
        "get_weather": lambda *args, **kwargs: (20.0, 0, 0.0),
        "get_weather_ex": lambda *args, **kwargs: ((20.0, 0, 0.0), "fallback"),
        "get_cached_overpass": lambda *args, **kwargs: (0, 0),
        "get_cached_overpass_ex": lambda *args, **kwargs: (
            (0, 0),
            {"bars": "fallback", "crash_density": "unavailable"},
        ),
        "get_bars": lambda *args, **kwargs: 0,
        "get_accident_density": lambda *args, **kwargs: 0,
    }
    for module in _loaded_modules():
        for name, fake in fakes.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, fake, raising=False)
    yield


@pytest.fixture
def client(offline_deps):
    if app is None:
        pytest.skip(f"app.py is not importable yet: {APP_IMPORT_ERROR!r}")
    limiter = getattr(app_module, "limiter", None)
    if limiter is not None:
        limiter.enabled = False
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture(scope="module")
def features_module():
    return pytest.importorskip("features", reason="features.py is not available yet")


@pytest.fixture(scope="module")
def crash_index():
    geo = pytest.importorskip("geo", reason="geo.py is not available yet")
    index_cls = getattr(geo, "CrashIndex", None)
    if index_cls is None:
        pytest.skip("geo.CrashIndex is not available yet")
    try:
        return index_cls()
    except TypeError:
        # Older/newer constructor that requires an explicit crash CSV path.
        return index_cls(os.path.join(BASE_DIR, "crashes.csv"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Query validation: always HTTP 400 + JSON error, never 500/HTML
# ---------------------------------------------------------------------------

INVALID_QUERIES = [
    pytest.param("/heatmap", "", id="heatmap-missing-all"),
    pytest.param("/heatmap", "?lat1=40.70", id="heatmap-missing-lon1"),
    pytest.param(
        "/heatmap",
        "?lat1=abc&lon1=-74.02&lat2=40.72&lon2=-73.98",
        id="heatmap-non-numeric-lat1",
    ),
    pytest.param(
        "/heatmap",
        "?lat1=40.70&lon1=abc&lat2=40.72&lon2=-73.98",
        id="heatmap-non-numeric-lon1",
    ),
    pytest.param("/risk/trend", "", id="trend-missing-all"),
    pytest.param("/risk/trend", "?lat=40.7580", id="trend-missing-lon"),
    pytest.param("/risk/trend", "?lat=abc&lon=-73.9855", id="trend-non-numeric"),
    pytest.param("/risk/trend", "?lat=999&lon=-73.9855", id="trend-out-of-range"),
    pytest.param("/nearest_safe", "", id="nearest-safe-missing-all"),
    pytest.param("/nearest_safe", "?lat=40.7580", id="nearest-safe-missing-lon"),
    pytest.param("/nearest_safe", "?lat=abc&lon=-73.9855", id="nearest-safe-non-numeric"),
    pytest.param("/risk/compare", "", id="compare-missing-all"),
    pytest.param(
        "/risk/compare",
        "?lat1=40.7580&lon1=-73.9855&lat2=40.6892",
        id="compare-missing-lon2",
    ),
    pytest.param(
        "/risk/compare",
        "?lat1=999&lon1=-73.9855&lat2=40.6892&lon2=-74.0445",
        id="compare-out-of-range",
    ),
    pytest.param("/safe_route", "", id="safe-route-missing-all"),
    pytest.param(
        "/safe_route", "?olat=40.7580&olon=-73.9855", id="safe-route-missing-dest"
    ),
    pytest.param(
        "/safe_route",
        "?olat=40.7580&olon=-73.9855&dlat=999&dlon=-74.0445",
        id="safe-route-out-of-range",
    ),
]


@pytest.mark.parametrize("path,query", INVALID_QUERIES)
def test_invalid_query_returns_400(client, path, query):
    response = client.get(f"{path}{query}")
    assert response.status_code == 400


@pytest.mark.parametrize("path,query", INVALID_QUERIES)
def test_invalid_query_returns_json_error(client, path, query):
    response = client.get(f"{path}{query}")
    assert response.is_json, f"{path}{query} returned {response.content_type}"
    assert "error" in response.get_json()


# ---------------------------------------------------------------------------
# /heatmap clamping, area limit and partial-failure resilience
# ---------------------------------------------------------------------------

HEATMAP_AREA = "lat1=40.700&lon1=-74.020&lat2=40.720&lon2=-73.980"


@pytest.mark.parametrize(
    "grid,expected",
    [(20, 8), (9, 8), (8, 8), (2, 2), (1, 2), (0, 2), (-3, 2)],
)
def test_heatmap_clamps_grid_size_to_2_8(client, grid, expected):
    response = client.get(f"/heatmap?{HEATMAP_AREA}&grid_size={grid}")
    assert response.status_code == 200
    assert response.get_json()["grid_size"] == expected


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("lat1=40.0&lon1=-74.5&lat2=40.5&lon2=-74.0", id="both-sides-0.5"),
        pytest.param(
            "lat1=40.70&lon1=-74.30&lat2=40.72&lon2=-73.90", id="lon-side-0.4"
        ),
        pytest.param(
            "lat1=40.70&lon1=-74.02&lat2=41.00&lon2=-73.98", id="lat-side-0.3"
        ),
    ],
)
def test_heatmap_rejects_oversized_area(client, query):
    response = client.get(f"/heatmap?{query}")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Negative counts/radii are rejected instead of silently clamped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["top_k=-5", "top_k=-1"])
def test_hotspots_rejects_negative_top_k(client, query):
    response = client.get(f"/hotspots?{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.parametrize("query", ["radius=-1", "radius=-500"])
def test_nearest_safe_rejects_negative_radius(client, query):
    response = client.get(f"/nearest_safe?lat=40.7580&lon=-73.9855&{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_nearest_safe_zero_radius_is_not_an_error(client):
    """radius=0 is a legal (if useless) request: it means "nothing nearby"."""
    response = client.get("/nearest_safe?lat=40.7580&lon=-73.9855&radius=0")
    assert response.status_code == 200
    assert response.get_json()["stops"] == []


def test_nearest_safe_reports_the_actually_nearest_stop(client):
    """`nearest_stop` must be the closest stop, not just the first by priority."""
    response = client.get("/nearest_safe?lat=40.7580&lon=-73.9855&radius=3000")
    if response.status_code != 200:
        pytest.skip("no safe stops available in this environment")
    data = response.get_json()
    stops = data.get("stops") or []
    if not stops:
        pytest.skip("no safe stops available in this environment")
    nearest = data["nearest_stop"]
    assert nearest["distance_m"] == min(stop["distance_m"] for stop in stops)
    assert data["total_found"] >= len(stops)


def test_hotspots_top_k_zero_still_returns_an_empty_list(client):
    response = client.get("/hotspots?top_k=0")
    assert response.status_code == 200
    assert response.get_json()["hotspots"] == []


def test_heatmap_returns_every_grid_cell(client):
    response = client.get(f"/heatmap?{HEATMAP_AREA}&grid_size=2")
    assert len(response.get_json()["heatmap"]) == 4


def test_heatmap_survives_a_failed_grid_cell(client, monkeypatch):
    if app_module is None or not hasattr(app_module, "risk_model"):
        pytest.skip("app.py does not expose the risk model yet")

    real_score = app_module.risk_model.score
    calls = {"count": 0}

    def flaky_score(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated grid-cell failure")
        return real_score(*args, **kwargs)

    monkeypatch.setattr(app_module.risk_model, "score", flaky_score)

    response = client.get(f"/heatmap?{HEATMAP_AREA}&grid_size=2")
    assert response.status_code == 200
    cells = response.get_json()["heatmap"]
    assert len(cells) == 4, "a failed cell must stay in the grid response"
    assert any(cell.get("error") for cell in cells), "failure was not reported"


# ---------------------------------------------------------------------------
# No process-killing timer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", ["app", "main"])
def test_source_does_not_use_signal_alarm(module_name):
    module = {"app": app_module, "main": main_module}.get(module_name)
    if module is None:
        pytest.skip(f"{module_name}.py is not importable yet")

    source = inspect.getsource(module)
    assert "signal.alarm" not in source


# ---------------------------------------------------------------------------
# features.py: single source of truth for engineered features
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hour,expected",
    [
        (17, (2, 0, 1)),  # 17:00 is evening, not night, and is rush hour
        (3, (3, 1, 0)),
        (0, (3, 1, 0)),
        (5, (0, 1, 0)),
        (8, (0, 0, 1)),
        (12, (1, 0, 0)),
        (19, (2, 0, 1)),
        (22, (3, 1, 0)),
    ],
)
def test_time_features(features_module, hour, expected):
    assert features_module.time_features(hour) == expected


@pytest.mark.parametrize(
    "lat,lon,expected",
    [
        pytest.param(40.7580, -73.9855, True, id="times-square"),
        pytest.param(40.4, -74.5, True, id="south-west-corner"),
        pytest.param(41.0, -73.5, True, id="north-east-corner"),
        pytest.param(25.3463, 55.4209, False, id="sharjah"),
        pytest.param(39.9, -73.9, False, id="south-of-bbox"),
    ],
)
def test_in_model_area(features_module, lat, lon, expected):
    assert features_module.in_model_area(lat, lon) is expected


# ---------------------------------------------------------------------------
# geo.py: offline crash index
# ---------------------------------------------------------------------------


def test_nyc_crash_density_is_positive(crash_index):
    if not getattr(crash_index, "available", False):
        pytest.skip("crashes.csv is not present")
    assert crash_index.density(40.7580, -73.9855) > 0


def test_out_of_coverage_crash_density_is_zero_or_rejected(crash_index):
    try:
        density = crash_index.density(25.3463, 55.4209)
    except (ValueError, LookupError):
        return
    assert density in (0, 0.0, None)


def test_crash_density_is_numeric(crash_index):
    if not getattr(crash_index, "available", False):
        pytest.skip("crashes.csv is not present")
    density = crash_index.density(40.7580, -73.9855)
    assert isinstance(density, (int, float))


# ---------------------------------------------------------------------------
# /risk coverage reporting
# ---------------------------------------------------------------------------

NYC_QUERY = "lat=40.7580&lon=-73.9855"
SHARJAH_QUERY = "lat=25.3463&lon=55.4209"
# Outside every global-model region (the UAE has one), so the context estimate answers.
RIYADH_QUERY = "lat=24.7136&lon=46.6753"


def test_risk_outside_nyc_is_a_labelled_context_estimate(client):
    """Outside NYC the API estimates instead of shrugging, and says it estimated."""
    response = client.get(f"/risk?{RIYADH_QUERY}&hour=23&day=3")
    assert response.status_code == 200
    data = response.get_json()
    assert data["coverage"] == "context"
    assert data["model_version"] == "context-2"
    assert 0 < data["risk_score"] <= 100
    assert data["context"]["country"] == "SA"
    assert any("Saudi Arabia" in reason for reason in data["reasons"])
    assert any("not a trained crash model" in message for message in data["warnings"])


def test_context_estimate_can_be_switched_off(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "CONTEXT_ENABLED", False)
    data = client.get(f"/risk?{RIYADH_QUERY}").get_json()
    assert data["coverage"] == "rule_based_only"
    assert data["model_points"] == 0


def test_risk_outside_coverage_returns_warnings(client):
    response = client.get(f"/risk?{SHARJAH_QUERY}")
    warnings = response.get_json().get("warnings")
    assert isinstance(warnings, list) and warnings


def test_risk_outside_coverage_has_no_ml_prediction(client):
    response = client.get(f"/risk?{SHARJAH_QUERY}")
    data = response.get_json()
    # Option C: /risk does not expose the severity model at all (any coverage).
    assert data.get("ml_prediction") is None
    assert data["coverage"] == "context"


def test_risk_does_not_expose_experimental_ml(client):
    data = client.get(f"/risk?{NYC_QUERY}").get_json()
    assert "ml_prediction" not in data


def test_risk_inside_coverage_reports_offline_mode(client):
    response = client.get(f"/risk?{NYC_QUERY}")
    assert response.status_code == 200
    coverage = response.get_json().get("coverage")
    assert isinstance(coverage, str) and coverage
    assert coverage != "rule_based_only"


def test_risk_inside_coverage_reports_data_quality(client):
    response = client.get(f"/risk?{NYC_QUERY}")
    assert "data_quality" in response.get_json()


# ---------------------------------------------------------------------------
# /stats privacy: no raw user coordinates
# ---------------------------------------------------------------------------


def test_stats_returns_json(client):
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.is_json


def test_stats_does_not_expose_location_history(client):
    data = client.get("/stats").get_json()
    assert "recent_requests" not in data
    assert "highest_risk_location" not in data
    assert "latitude" not in data
    assert "longitude" not in data


def test_request_log_schema_has_no_coordinate_columns():
    if app_module is None:
        pytest.skip("app.py is not importable")
    import sqlite3

    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        risk_columns = {row[1] for row in conn.execute("PRAGMA table_info(risk_logs)")}
        route_columns = {row[1] for row in conn.execute("PRAGMA table_info(route_logs)")}
    finally:
        conn.close()
    assert "latitude" not in risk_columns
    assert "longitude" not in risk_columns
    assert not {"origin_lat", "origin_lon", "dest_lat", "dest_lon"} & route_columns


# ---------------------------------------------------------------------------
# /health and deep health checks
# ---------------------------------------------------------------------------


def test_health_status_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_health_deep_includes_weather_and_overpass_checks(client):
    response = client.get("/health?deep=1")
    assert response.status_code == 200
    checks = response.get_json().get("checks") or {}
    providers = checks.get("providers") or {}
    assert {"weather", "overpass"} <= set(providers)


# ---------------------------------------------------------------------------
# Client-local time, static-risk cap, provenance and resilience
# ---------------------------------------------------------------------------


def test_client_hour_and_day_override_server_clock(client):
    day = client.get("/risk?lat=40.7580&lon=-73.9855&hour=14&day=0").get_json()
    night = client.get("/risk?lat=40.7580&lon=-73.9855&hour=23&day=5").get_json()
    # The learned surface does not assume night is always worse. What it must
    # do is honor the requested local time and answer for that hour.
    assert night["local_time"]["hour"] == 23
    assert night["local_time"]["day_of_week"] == 5
    assert night["local_time"]["is_weekend"] is True
    assert day["local_time"] == {"hour": 14, "day_of_week": 0, "is_weekend": False}
    assert night["expected_rate"] != day["expected_rate"]


@pytest.mark.parametrize("query", ["hour=24", "hour=-1", "hour=abc", "day=7", "day=-1", "day=abc"])
def test_invalid_client_time_is_rejected(client, query):
    response = client.get(f"/risk?lat=40.7580&lon=-73.9855&{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_weather_key_is_redacted(monkeypatch):
    if main_module is None:
        pytest.skip("main.py is not importable")
    monkeypatch.setattr(main_module, "WEATHER_API_KEY", "super-secret-key")
    redacted = main_module._redact_key(
        "https://api.weatherapi.com/v1/current.json?key=super-secret-key&q=1,2"
    )
    assert "super-secret-key" not in redacted
    assert "***" in redacted


def test_alert_poller_keeps_last_alerts_when_weather_falls_back(monkeypatch):
    if main_module is None:
        pytest.skip("main.py is not importable")
    monkeypatch.setattr(
        main_module, "get_weather_ex", lambda *a, **k: ((20.0, 0, 0.0), "fallback")
    )
    with main_module.alert_lock:
        main_module.active_alerts.clear()
        main_module.active_alerts.append({"type": "LAST_KNOWN", "severity": "HIGH"})
    main_module.check_weather_alerts(
        [{"name": "Test", "lat": 40.7580, "lon": -73.9855}]
    )
    assert any(a.get("type") == "LAST_KNOWN" for a in main_module.active_alerts)
    assert main_module.alert_status["data_quality"] == "fallback"
    assert main_module.alert_status["warning"]


def test_offline_bars_index_counts_times_square():
    bars = pytest.importorskip("bars")
    if not os.path.exists(os.path.join(BASE_DIR, "bars_nyc.csv")):
        pytest.skip("bars_nyc.csv is not present")
    index = bars.BarsIndex()
    if not index.available:
        pytest.skip("bars index unavailable")
    assert index.count(40.7580, -73.9855, radius_km=1.0) > 50


def test_offline_safe_stops_index_returns_prioritised_stops():
    safe_stops = pytest.importorskip("safe_stops")
    if not os.path.exists(os.path.join(BASE_DIR, "safe_stops_nyc.csv")):
        pytest.skip("safe_stops_nyc.csv is not present")
    index = safe_stops.SafeStopsIndex()
    if not index.available:
        pytest.skip("safe stops index unavailable")
    stops = index.nearest(40.7580, -73.9855, radius_km=3.0, limit=5)
    assert stops
    for stop in stops:
        assert {"name", "type", "lat", "lon", "distance_m", "safety_priority"} <= set(stop)


def test_heatmap_rate_limit_returns_429():
    if app_module is None:
        pytest.skip("app.py is not importable")
    limiter = app_module.limiter
    limit_per_minute = int(app_module.HEATMAP_RATE_LIMIT.split("/", 1)[0])
    limiter.enabled = True
    limiter.reset()
    try:
        with app.test_client() as test_client:
            codes = [
                test_client.get(
                    "/heatmap?lat1=40.70&lon1=-74.02&lat2=40.71&lon2=-74.01&grid=2"
                ).status_code
                for _ in range(limit_per_minute + 1)
            ]
        assert 429 in codes
        assert codes[-1] == 429
    finally:
        limiter.enabled = False
        limiter.reset()

def test_risk_exposes_breakdown_and_local_time(client):
    data = client.get("/risk?lat=40.7580&lon=-73.9855&hour=12&day=2").get_json()
    assert {
        "model",
        "nightlife",
        "total",
        "model_max",
        "nightlife_max",
        "percentile",
        "expected_rate",
        "expected_rate_per_km2_hour",
    } <= set(data["risk_breakdown"])
    assert data["risk_breakdown"]["model"] <= 60
    assert data["risk_breakdown"]["nightlife"] <= 40
    assert data["risk_breakdown"]["total"] <= 100
    assert data["local_time"] == {"hour": 12, "day_of_week": 2, "is_weekend": False}


def test_risk_outside_model_area_has_no_crash_points(client):
    data = client.get("/risk?lat=40.7357&lon=-74.1724&hour=23&day=5").get_json()
    assert data["coverage"] == "rule_based_only"
    assert data["model_points"] == 0
    assert data["expected_rate"] is None
    assert data["percentile"] is None
    assert any("No learned crash surface" in message for message in data["warnings"])


def test_heatmap_agrees_with_risk_for_same_cell(client):
    response = client.get(
        "/heatmap?lat1=40.757&lon1=-73.986&lat2=40.759&lon2=-73.984"
        "&grid_size=2&hour=23&day=5"
    )
    assert response.status_code == 200
    for cell in response.get_json()["heatmap"]:
        point = client.get(
            f"/risk?lat={cell['lat']}&lon={cell['lon']}&hour=23&day=5"
        ).get_json()
        assert cell["cell"] == point["cell"]
        assert cell["expected_rate"] == pytest.approx(point["expected_rate"])
        assert cell["model_points"] == pytest.approx(point["model_points"])
        assert cell["risk_score"] == pytest.approx(point["risk_score"])


def test_health_lists_every_served_route(client):
    # The list used to be typed by hand and said 15 after /geocode made it 16.
    # It is now read from the router, so it must match what Flask serves.
    from app import app as flask_app

    served = {rule.rule for rule in flask_app.url_map.iter_rules() if rule.endpoint != "static"}
    data = client.get("/health").get_json()
    assert set(data["endpoints"]) == served
    assert data["endpoint_count"] == len(served) == len(data["endpoints"])
    for route in ("/health", "/geocode", "/surface", "/risk", "/safe_route", "/hotspots"):
        assert route in data["endpoints"]
    assert "crash_risk_model" in data["checks"]
    assert data["checks"]["crash_risk_model"]["gate_passed"] is True


def test_model_points_alone_cannot_reach_critical():
    from crash_model import points_from_percentile, risk_band

    assert points_from_percentile(0) == 0
    assert points_from_percentile(100) == 60
    assert risk_band(points_from_percentile(100)) != "CRITICAL"


def test_alerts_exposes_data_quality_metadata(client):
    data = client.get("/alerts").get_json()
    assert "data_quality" in data
    assert "last_checked" in data
    assert "warning" in data
