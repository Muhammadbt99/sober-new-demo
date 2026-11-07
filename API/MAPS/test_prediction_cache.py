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


# --- Crash-risk training gate: baseline tuning, deciles, and the model card ---


def _train_module():
    """crash_model/train.py imports its sibling modules by bare name."""
    import os
    import sys

    crash_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_model")
    if crash_dir not in sys.path:
        sys.path.insert(0, crash_dir)
    import train

    return train


def test_calibration_deciles_carry_equal_weight_when_weights_are_uneven():
    import numpy as np

    train = _train_module()
    rng = np.random.default_rng(7)
    n = 4000
    w = 10 ** rng.uniform(0, 4, n)  # cell-hour weights spanning four orders of magnitude
    mu = rng.uniform(size=n)
    y = rng.poisson(mu).astype(float)

    rows = train.calibration_table(y, mu, w)

    assert len(rows) == 10
    share = np.array([row["weight"] for row in rows]) / w.sum()
    assert np.all(np.abs(share - 0.1) < 0.01), share


def test_calibration_never_returns_empty_deciles_on_a_tiny_sample():
    import numpy as np

    train = _train_module()
    rows = train.calibration_table(
        np.array([1.0, 0.0, 2.0]), np.array([0.1, 0.5, 0.9]), np.array([1.0, 1.0, 1.0])
    )
    assert [row["decile"] for row in rows] == [1, 2, 3]
    assert all(row["weight"] > 0 for row in rows)


def test_tune_baseline_picks_the_shrink_that_does_best_on_validation():
    import numpy as np
    import pandas as pd

    train_mod = _train_module()
    rng = np.random.default_rng(3)
    cells = [f"c{i}" for i in range(20)]

    def frame(counts):
        return pd.DataFrame(
            {
                "cell": cells,
                "borough": "BROOKLYN",
                "day_of_week": 0,
                "hour": 0,
                "count": counts,
                "weight": 1.0,
            }
        )

    # Every cell has the same true rate, so a heavily smoothed lookup is best on
    # validation, while scoring on the training rows (which the lookup memorises)
    # would prefer the least smoothing.
    train = frame(rng.poisson(1.0, len(cells)).astype(float))
    validation = frame(rng.poisson(1.0, len(cells)).astype(float))

    baseline, shrink = train_mod.tune_baseline(train, validation)

    assert shrink == 500.0
    assert baseline.shrink_hours == shrink
    assert baseline.cell is not None  # the returned baseline was fit on train


def test_model_card_prefers_the_fair_baseline_when_present():
    from crash_model.model import RiskModel

    def metrics(deviance, lift):
        return {"poisson_deviance": deviance, "top_decile_lift": lift, "monotonic_deciles": True, "calibration": []}

    old = {"temporal_test": metrics(0.0459, 2.9), "holdout": metrics(0.0556, 1.7)}
    fair = {
        "shrink_hours": 500.0,
        "temporal_test": {
            "poisson_deviance": 0.0370,
            "top_decile_lift": 2.8,
            "model_top_decile_lift": 3.5,
            "model_calibration": [
                {"decile": 1, "predicted_rate": 0.1, "observed_rate": 0.1, "lift": 0.5},
                {"decile": 2, "predicted_rate": 0.2, "observed_rate": 0.4, "lift": 1.0},
                {"decile": 3, "predicted_rate": 0.9, "observed_rate": 0.9, "lift": 3.5},
            ],
        },
        "holdout": {"poisson_deviance": 0.0540, "top_decile_lift": 1.6, "model_top_decile_lift": 1.9},
    }
    model = RiskModel.__new__(RiskModel)  # card reads metadata only; skip loading artifacts
    model.metadata = {
        "model_metrics": {"temporal_test": metrics(0.0359, 3.9), "holdout": metrics(0.0548, 2.19)},
        "baseline": {**old, "fair": fair},
    }

    card = model.model_card()
    assert card["temporal_test"]["baseline_deviance"] == 0.0370
    assert card["temporal_test"]["baseline_top_decile_lift"] == 2.8
    assert card["temporal_test"]["top_decile_lift"] == 3.5
    assert card["temporal_test"]["poisson_deviance"] == 0.0359
    # Deciles come from the fair block's weighted table, not the old row-count one.
    assert [row["lift"] for row in card["temporal_test"]["deciles"]] == [0.5, 1.0, 3.5]
    assert card["temporal_test"]["deciles"][-1]["lift"] == card["temporal_test"]["top_decile_lift"]
    assert card["temporal_test"]["monotonic_deciles"] is True
    assert card["holdout"]["baseline_deviance"] == 0.0540
    assert card["holdout"]["top_decile_lift"] == 1.9

    model.metadata["baseline"].pop("fair")
    legacy = model.model_card()
    assert legacy["temporal_test"]["baseline_deviance"] == 0.0459
    assert legacy["temporal_test"]["top_decile_lift"] == 3.9
