"""Capture the frozen API contract fixtures from the real Flask app.

The route fixtures inject a deterministic fake provider so the contract gate
never depends on a live routing service. Everything else is the real endpoint,
the real model and the real offline indexes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import main  # noqa: E402
import app as app_module  # noqa: E402

FIXTURES = (
    Path(r"D:\S.O.B.E.R NEW DEMO")
    / "app-v2"
    / "src"
    / "lib"
    / "contract-fixtures"
)

TIMES_SQUARE = (40.7580, -73.9855)
BARCLAYS = (40.6829, -73.9776)


def _route(coords, distance, duration):
    return {
        "geometry": {"coordinates": coords},
        "distance": distance,
        "duration": duration,
    }


TWO_ROUTES = [
    _route(
        [
            [-73.9855, 40.7580],
            [-73.9920, 40.7440],
            [-73.9970, 40.7300],
            [-73.9930, 40.7150],
            [-73.9850, 40.7020],
            [-73.9776, 40.6829],
        ],
        8500,
        1500,
    ),
    _route(
        [
            [-73.9855, 40.7580],
            [-73.9800, 40.7480],
            [-73.9750, 40.7350],
            [-73.9720, 40.7200],
            [-73.9750, 40.7050],
            [-73.9776, 40.6829],
        ],
        9200,
        1650,
    ),
]

ONE_ROUTE = [
    _route(
        [
            [-73.9855, 40.7580],
            [-73.9900, 40.7440],
            [-73.9940, 40.7300],
            [-73.9900, 40.7150],
            [-73.9830, 40.7000],
            [-73.9780, 40.6830],
        ],
        8700,
        1560,
    )
]


def fake_two(olat, olon, dlat, dlon):
    return TWO_ROUTES, "captured-fake-two"


def fake_one(olat, olon, dlat, dlon):
    return ONE_ROUTE, "captured-fake-one"


def fake_timeout(olat, olon, dlat, dlon):
    raise TimeoutError("captured provider timeout")


def fake_empty(olat, olon, dlat, dlon):
    return [], "captured-fake-empty"


def fake_bad_geometry(olat, olon, dlat, dlon):
    return (
        [{"geometry": {"coordinates": []}, "distance": 0, "duration": 0}],
        "captured-fake-bad-geometry",
    )


def write(client, name, path, expected_status=200):
    response = client.get(path)
    body = response.get_json()
    if response.status_code != expected_status:
        raise SystemExit(
            f"{name}: expected {expected_status}, got {response.status_code}: {body}"
        )
    (FIXTURES / f"{name}.json").write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {name}.json ({response.status_code})")


def safe_route(client, name, fake, origin, destination, expected_status=200):
    app_module.get_routes = fake
    main.active_routes.clear()
    path = (
        "/safe_route"
        f"?olat={origin[0]}&olon={origin[1]}"
        f"&dlat={destination[0]}&dlon={destination[1]}"
        "&hour=23&day=5"
    )
    write(client, name, path, expected_status)


def main_capture():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        write(client, "risk_times_square", "/risk?lat=40.7580&lon=-73.9855&hour=23&day=5")
        # Dubai: the global crash model. Riyadh: outside every region, the estimate.
        write(client, "risk_out_of_area", "/risk?lat=25.2048&lon=55.2708&hour=23&day=5")
        write(client, "risk_context", "/risk?lat=24.7136&lon=46.6753&hour=23&day=5")
        write(
            client,
            "heatmap",
            "/heatmap?lat1=40.70&lon1=-74.02&lat2=40.72&lon2=-73.98&grid=2&hour=23&day=5",
        )
        write(
            client,
            "risk_trend",
            "/risk/trend?lat=40.7580&lon=-73.9855&day=5",
        )
        write(
            client,
            "risk_compare",
            "/risk/compare?lat1=40.7580&lon1=-73.9855&lat2=40.6829&lon2=-73.9776&hour=23&day=5",
        )
        write(client, "health", "/health")
        write(client, "stats", "/stats")
        write(client, "surface", "/surface?hour=23&day=5&danger=impaired")
        write(client, "danger_types", "/danger_types")
        write(client, "model_card", "/model_card")
        write(client, "global_model", "/global_model")
        write(client, "surface_global", "/surface/global?lat=25.1972&lon=55.2744&radius_km=2&hour=23&day=5")
        write(client, "hotspots", "/hotspots?top_k=10")
        write(client, "nearest_safe", "/nearest_safe?lat=40.7580&lon=-73.9855&radius=3000")
        write(client, "alerts", "/alerts")
        write(
            client,
            "alerts_check_location",
            "/alerts/check_location?lat=40.7580&lon=-73.9855",
        )

        safe_route(client, "safe_route", fake_two, TIMES_SQUARE, BARCLAYS)
        write(
            client,
            "departures",
            "/safe_route/departures"
            f"?olat={TIMES_SQUARE[0]}&olon={TIMES_SQUARE[1]}"
            f"&dlat={BARCLAYS[0]}&dlon={BARCLAYS[1]}&hour=23&day=5",
        )
        safe_route(
            client,
            "safe_route_single",
            fake_one,
            TIMES_SQUARE,
            (40.6830, -73.9780),
        )
        safe_route(
            client,
            "err_provider_timeout",
            fake_timeout,
            (40.7600, -73.9900),
            (40.6900, -73.9700),
            expected_status=504,
        )
        # The helper above expects a success; capture failures directly.
        for name, fake, destination in [
            ("err_no_routes", fake_empty, (40.6910, -73.9710)),
            ("err_geometry", fake_bad_geometry, (40.6920, -73.9720)),
        ]:
            app_module.get_routes = fake
            main.active_routes.clear()
            path = (
                "/safe_route"
                f"?olat=40.7600&olon=-73.9900"
                f"&dlat={destination[0]}&dlon={destination[1]}"
                "&hour=23&day=5"
            )
            response = client.get(path)
            body = response.get_json()
            if response.status_code not in (502, 504):
                raise SystemExit(f"{name}: unexpected {response.status_code}: {body}")
            (FIXTURES / f"{name}.json").write_text(
                json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"wrote {name}.json ({response.status_code})")

        write(client, "err_missing_lat", "/risk?lon=-73.98", 400)
        write(client, "err_bad_lat", "/risk?lat=999&lon=-73.98", 400)
        write(client, "err_hour_range", "/risk?lat=40.75&lon=-73.98&hour=99", 400)
        write(client, "err_day_range", "/risk?lat=40.75&lon=-73.98&day=9", 400)
        write(
            client,
            "err_heatmap_span",
            "/heatmap?lat1=40.0&lon1=-74.5&lat2=41.0&lon2=-73.0",
            400,
        )
        write(client, "err_not_found", "/not-a-route", 404)
        write(client, "err_negative_radius", "/nearest_safe?lat=40.75&lon=-73.98&radius=-5", 400)
        write(client, "err_negative_top_k", "/hotspots?top_k=-1", 400)

    for stale in [
        "risk_night.json",
        "risk_conditions_fallback.json",
        "safe_route_outside.json",
    ]:
        path = FIXTURES / stale
        if path.exists():
            path.unlink()
            print(f"removed stale {stale}")


if __name__ == "__main__":
    main_capture()
