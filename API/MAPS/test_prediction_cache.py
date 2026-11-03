"""The crash model's prediction cache must be invisible: same inputs, same numbers."""
from __future__ import annotations

import pytest

from crash_model import risk_model
from crash_model.model import RiskModel, _weather_key

pytestmark = pytest.mark.skipif(
    not risk_model.available, reason="crash model artifact is not loaded"
)

CASES = [
    (40.7580, -73.9855, 23, 5, (None, None, None)),  # Times Square, Saturday night
    (40.7128, -74.0060, 8, 1, (12.0, 0.0, 3.5)),  # Lower Manhattan, Tuesday rush
    (40.6782, -73.9442, 3, 6, None),  # Brooklyn, Sunday small hours
]


def test_a_cached_score_is_identical_to_an_uncached_one():
    cached = RiskModel()
    fresh = RiskModel()
    for lat, lon, hour, day, weather in CASES:
        first = cached.score(lat, lon, hour, day, weather=weather, bars=4)
        again = cached.score(lat, lon, hour, day, weather=weather, bars=4)
        uncached = fresh.score(lat, lon, hour, day, weather=weather, bars=4)
        assert again == first
        assert uncached == first


def test_nightlife_is_not_cached_with_the_prediction():
    # Bars change the nightlife points only; the cached model value must not
    # freeze the total at whatever bar count was seen first.
    model = RiskModel()
    quiet = model.score(40.7580, -73.9855, 23, 5, bars=0)
    busy = model.score(40.7580, -73.9855, 23, 5, bars=60)
    assert quiet["expected_rate"] == busy["expected_rate"]
    assert busy["nightlife_points"] > quiet["nightlife_points"]
    assert busy["risk_score"] > quiet["risk_score"]


def test_the_cache_key_separates_time_and_weather_but_not_missing_values():
    model = RiskModel()
    model.score(40.7580, -73.9855, 23, 5, weather=None, bars=0)
    model.score(40.7580, -73.9855, 23, 5, weather=(None, None, None), bars=0)
    model.score(40.7580, -73.9855, 23, 5, weather=(float("nan"),) * 3, bars=0)
    assert len(model._predictions) == 1  # all three mean "weather missing"
    model.score(40.7580, -73.9855, 23, 5, weather=(20.0, 0.0, 5.0), bars=0)
    model.score(40.7580, -73.9855, 22, 5, weather=None, bars=0)
    model.score(40.7580, -73.9855, 23, 4, weather=None, bars=0)
    assert len(model._predictions) == 4


def test_weather_key_normalises_missing_values():
    assert _weather_key(None) == (None, None, None)
    assert _weather_key((None, float("nan"), 3)) == (None, None, 3.0)
    assert _weather_key((True, 0, 2.5)) == (1.0, 0.0, 2.5)
