"""Test bootstrap.

The real NYC crash CSV *is* committed, so a normal checkout already has it and
the tests exercise the real dataset. It is large and dataset-specific, so CI
and fresh clones still need a fallback: if ``crashes.csv`` is absent we write a
small deterministic synthetic fixture rather than skipping, because
``geo.CrashIndex`` and ``/hotspots`` read the CSV at import time and the test
suite wants to exercise those same code paths.
"""

import csv
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRASHES_PATH = os.path.join(BASE_DIR, "crashes.csv")


def _write_fixture():
    if os.path.exists(CRASHES_PATH):
        return
    rows = []
    # One dense cluster (top hotspot, >20 rows => HIGH) plus enough distinct
    # rounded coordinates for the top-10 tests.
    clusters = [(40.7580, -73.9855, 60)]
    for index in range(14):
        clusters.append((40.60 + index * 0.02, -74.10 + index * 0.03, 5))

    for lat, lon, count in clusters:
        for index in range(count):
            rows.append({
                "latitude": lat + (index % 5) * 0.00001,
                "longitude": lon + (index % 7) * 0.00001,
                "number_of_persons_injured": 1 if index % 9 == 0 else 0,
                "number_of_persons_killed": 1 if index % 29 == 0 else 0,
            })

    with open(CRASHES_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[conftest] wrote synthetic crash fixture: {CRASHES_PATH}")


_write_fixture()


# ---------------------------------------------------------------------------
# Offline stand-ins for the live providers (weather, places, road type).
# The suite must not depend on Open-Meteo or Photon being up, and calling them
# for every request made it five times slower. Tests that want the real thing
# can request the `live_providers` fixture instead.
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

import providers as _providers  # noqa: E402

FAKE_WEATHER = {
    "temperature_c": 22.0,
    "precipitation_mm": 0.0,
    "rain": False,
    "snow": False,
    "fog": False,
    "wind_kph": 10.0,
    "visibility_km": 10.0,
    "provider": "test-weather",
}


def _fake_country(lat, lon):
    return "AE" if 22 <= lat <= 27 and 51 <= lon <= 57 else ("GB" if lon > -10 and lat > 49 else "US")


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch, request):
    if "live_providers" in request.fixturenames:
        return
    monkeypatch.setattr(_providers, "current_weather", lambda lat, lon: dict(FAKE_WEATHER))
    monkeypatch.setattr(
        _providers,
        "road_at",
        lambda lat, lon: {"name": "Test Road", "class": "primary", "country": _fake_country(lat, lon)},
    )
    monkeypatch.setattr(_providers, "country_at", _fake_country)
    monkeypatch.setattr(_providers, "places", lambda *args, **kwargs: [])
    monkeypatch.setattr(_providers, "nightlife_count", lambda *args, **kwargs: None)
    monkeypatch.setattr(_providers, "nightlife_counts", lambda *args, **kwargs: None)
    monkeypatch.setattr(_providers, "nightlife_venues", lambda *args, **kwargs: None)


@pytest.fixture
def live_providers():
    """Opt out of the offline stand-ins (network required)."""
    return _providers
