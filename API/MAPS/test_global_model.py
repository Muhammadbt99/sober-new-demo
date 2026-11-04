"""The global crash model: protocol, serving in the UAE, and the API around it."""
from __future__ import annotations

import numpy as np
import pytest

import app as app_module
from app import app
from global_model import serve
from global_model.serve import GlobalModel, rel_dow

GLOBAL = app_module.GLOBAL_MODEL
needs_model = pytest.mark.skipif(
    GLOBAL is None or not GLOBAL.available, reason="global model artifacts are not built"
)

DOWNTOWN_DUBAI = (25.1972, 55.2744)
SHARJAH = (25.3463, 55.4209)
RIYADH = (24.7136, 46.6753)
DOHA = (25.2854, 51.5310)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


# --------------------------------------------------------------------------- layout


@pytest.mark.parametrize("day,expected", [(5, 0), (6, 1), (0, 2), (4, 6)])
def test_days_count_from_the_start_of_the_weekend(day, expected):
    assert rel_dow(day, 5) == expected


def test_serving_rows_match_the_training_layout():
    """A model trained on one column order and served with another would be silently wrong."""
    from global_model.train_global import design
    import pandas as pd

    table = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]}, index=["x", "y"])
    trained = design(table, ["a", "b"]).reshape(2, 7, 24, -1)
    for hour, day in [(0, 0), (23, 0), (17, 3), (3, 6)]:
        served = serve.design_rows(table[["a", "b"]].to_numpy(np.float32), hour, day)
        np.testing.assert_array_equal(served, trained[:, day, hour, :])


def test_a_missing_model_is_a_degraded_start(tmp_path):
    model = GlobalModel(str(tmp_path))
    assert model.available is False
    assert model.error
    assert model.score(*DOWNTOWN_DUBAI, 23, 5) is None
    assert model.health()["available"] is False


# --------------------------------------------------------------------------- the artifact


@needs_model
def test_metadata_records_the_gate_and_every_held_out_city():
    meta = GLOBAL.metadata
    assert meta["status"] == "approved"
    gate = meta["gate"]
    assert gate["checks"]["passed"] is True
    chosen = meta["feature_set"]
    held_out = gate["per_city"][chosen]
    assert set(meta["training"]) <= set(held_out)
    for city in meta["training"]:
        assert held_out[city]["top_decile_lift"] >= 1.5
    assert "held-out city" in meta["protocol"]["gate"]


# --------------------------------------------------------------------------- serving


@needs_model
def test_dubai_gets_a_labelled_learned_answer():
    result = GLOBAL.score(*DOWNTOWN_DUBAI, 23, 5)
    assert result is not None
    assert result["coverage"] == "context"
    assert result["context"]["method"] == "global_model"
    assert result["model_version"] == GLOBAL.version
    assert 0.0 <= result["percentile"] <= 100.0
    assert result["model_points"] in (0.0, 15.0, 30.0, 50.0, 60.0)
    assert result["risk_score"] == pytest.approx(min(100.0, result["model_points"] + result["nightlife_points"]))
    assert result["expected_rate"] > 0
    assert result["reasons"]


@needs_model
@pytest.mark.parametrize("point", [RIYADH, DOHA], ids=["riyadh", "doha"])
def test_places_outside_every_region_get_no_global_answer(point):
    assert GLOBAL.score(*point, 23, 5) is None


@needs_model
def test_junction_heavy_cells_outrank_empty_ones():
    region = GLOBAL.regions["uae"]
    rates = region.slot(17, rel_dow(2, 5))
    junctions = region.table["junctions"].to_numpy()
    busy = np.median(region.percentile(rates[junctions >= 20]))
    empty = np.median(region.percentile(rates[junctions == 0]))
    assert busy > empty + 20


def test_the_reference_is_taken_among_street_grid_cells_only():
    """Desert hexes (few junctions) would squeeze every city street into the top 2%."""
    import pandas as pd

    class RateIsJunctions:  # a stand-in model: rate = the cell's junction count
        def predict(self, rows):
            return rows[:, 0]

    table = pd.DataFrame(
        {"junctions": [0.0, 1.0, 5.0, 10.0, 20.0], "lat": 0.0, "lon": 0.0,
         "nightlife": 0, "ring_nightlife": 0, "population": 0.0},
        index=list("abcde"),
    )
    region = serve.RegionModel(serve.REGIONS["uae"], table, RateIsJunctions(), ["junctions"])
    reference = region.build_reference()
    assert reference[0] == 5.0 and reference[-1] == 20.0  # the 0- and 1-junction cells are out
    region.reference = reference
    assert region.percentile(4.0) == 0.0
    assert region.percentile(20.0) == 100.0
    assert 0.0 < region.percentile(10.0) < 100.0


@needs_model
def test_the_published_reference_is_sorted_quantiles():
    region = GLOBAL.regions["uae"]
    assert len(region.reference) == serve.REFERENCE_QUANTILES
    assert np.all(np.diff(region.reference) >= 0)
    assert 1000 < region.reference_rows().sum() < len(region.table)


@needs_model
def test_city_streets_are_not_all_pinned_to_the_top():
    """Against every hex (mostly desert) every city street sat in the top 2% at every hour."""
    marina = GLOBAL.score(25.0805, 55.1403, 3, 6)
    assert marina["percentile"] < 90
    downtown = GLOBAL.score(*DOWNTOWN_DUBAI, 17, 2)
    assert downtown["percentile"] > marina["percentile"]


@needs_model
def test_surface_lists_cells_around_a_point():
    surface = GLOBAL.surface(*DOWNTOWN_DUBAI, 4.0, 23, 5)
    assert surface["region"] == "uae"
    assert len(surface["cells"]) > 20
    for cell in surface["cells"]:
        assert abs(cell["lat"] - DOWNTOWN_DUBAI[0]) < 0.08
        assert cell["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# --------------------------------------------------------------------------- API


@needs_model
def test_risk_in_the_uae_uses_the_global_model_offline(client, monkeypatch):
    def no_live_lookup(*args, **kwargs):
        raise AssertionError("a global-model answer must not wait on a live venue lookup")

    monkeypatch.setattr(app_module, "get_bars_ex", no_live_lookup)
    data = client.get(f"/risk?lat={SHARJAH[0]}&lon={SHARJAH[1]}&hour=23&day=3").get_json()
    assert data["coverage"] == "context"
    assert data["context"]["method"] == "global_model"
    assert data["model_version"] == GLOBAL.version
    assert data["data_quality"]["bars"] == "offline"
    assert any("global crash model" in warning for warning in data["warnings"])


@needs_model
def test_riyadh_keeps_the_context_estimate(client):
    data = client.get(f"/risk?lat={RIYADH[0]}&lon={RIYADH[1]}&hour=23&day=3").get_json()
    assert data["coverage"] == "context"
    assert (data["context"] or {}).get("method") != "global_model"
    assert data["model_version"] == "context-2"


@needs_model
def test_the_global_model_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(app_module, "GLOBAL_ENABLED", False)
    data = client.get(f"/risk?lat={SHARJAH[0]}&lon={SHARJAH[1]}&hour=23&day=3").get_json()
    assert data["model_version"] == "context-2"


@needs_model
def test_health_reports_the_global_model(client):
    check = client.get("/health").get_json()["checks"]["global_model"]
    assert check["available"] is True
    assert check["gate_passed"] is True
    assert check["regions"]["uae"]["cells"] > 1000


@needs_model
def test_global_model_card(client):
    card = client.get("/global_model").get_json()
    assert card["version"] == GLOBAL.version
    assert card["gate"]["checks"]["passed"] is True
    assert set(card["training"]) <= set(card["gate"]["held_out"])
    assert all("deciles" not in row for row in card["gate"]["held_out"].values())


@needs_model
def test_global_surface_endpoint(client):
    data = client.get(
        f"/surface/global?lat={DOWNTOWN_DUBAI[0]}&lon={DOWNTOWN_DUBAI[1]}&radius_km=5&hour=23&day=5"
    ).get_json()
    assert data["region"] == "uae"
    assert data["cell_count"] == len(data["cells"]) > 20
    assert data["model_version"] == GLOBAL.version


@needs_model
@pytest.mark.parametrize(
    "query,status",
    [
        ("lat=25.1972&lon=55.2744&radius_km=60", 400),
        ("lat=25.1972&lon=55.2744&radius_km=x", 400),
        ("lat=40.7580&lon=-73.9855&radius_km=5", 404),
        ("lat=24.7136&lon=46.6753&radius_km=5", 404),
    ],
)
def test_global_surface_rejects_bad_requests(client, query, status):
    assert client.get(f"/surface/global?{query}").status_code == status


@needs_model
def test_uae_routes_are_scored_by_the_global_model_offline(client, monkeypatch):
    def fake_routes(olat, olon, dlat, dlon):
        line = [[55.2744, 25.1972], [55.2900, 25.2150], [55.3100, 25.2400], [55.3260, 25.2654]]
        return [{"geometry": {"coordinates": line}, "distance": 9800.0, "duration": 900.0}], "fake"

    def no_live_lookup(*args, **kwargs):
        raise AssertionError("a trip inside a global-model region needs no live venue lookup")

    monkeypatch.setattr(app_module, "get_routes", fake_routes)
    monkeypatch.setattr(app_module, "get_cached_route", lambda route_id: None)
    monkeypatch.setattr(app_module, "cache_route", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "get_bars_ex", no_live_lookup)
    monkeypatch.setattr(app_module, "_trip_venues", no_live_lookup)
    data = client.get(
        "/safe_route?olat=25.1972&olon=55.2744&dlat=25.2654&dlon=55.3260&hour=23&day=5"
    ).get_json()
    assert data["coverage"] == "context"
    assert data["model_version"] == GLOBAL.version
    segments = data["routes"][0]["segments"]
    assert segments and all(segment["method"] == "global_model" for segment in segments)
    assert any("global crash model" in warning for warning in data["warnings"])


@needs_model
def test_global_model_card_is_strict_json(client):
    """NaN is valid for Python's json module but not for a browser's JSON.parse."""
    import json

    def reject(constant):
        raise ValueError(f"non-JSON constant {constant}")

    body = client.get("/global_model").get_data(as_text=True)
    json.loads(body, parse_constant=reject)


@needs_model
def test_the_card_reports_the_dubai_test(client):
    row = client.get("/global_model").get_json()["dubai_validation"]
    assert row["crashes"] > 10_000
    assert row["top_decile_lift"] > row["baseline_top_decile_lift"]
    assert row["top_decile_lift"] >= 1.5
    assert "deciles" not in row
    assert "Dubai" in row["protocol"]


@needs_model
def test_dubai_is_a_training_city_and_its_rules_are_recorded(client):
    assert "dubai" in GLOBAL.metadata["training"]
    assert "Dubai" in GLOBAL.training_summary()
    retrain = client.get("/global_model").get_json()["dubai_validation"]["retrain"]
    assert retrain["adopted"] is True and all(retrain["rules"].values())


@needs_model
def test_percentiles_are_taken_among_the_recorded_dubai_hexes():
    region = GLOBAL.regions["uae"]
    assert region.reference_rows().sum() == GLOBAL.metadata["training"]["dubai"]["cells"]


class TestCrashOnlyCritical:
    def test_the_top_percent_is_critical_with_no_nightlife(self):
        from crash_model.model import risk_band
        from global_model.serve import score_total

        assert risk_band(score_total(99.0, 60.0, 0.0)) == "CRITICAL"

    def test_just_below_the_line_stays_high(self):
        from crash_model.model import risk_band
        from global_model.serve import score_total

        assert risk_band(score_total(98.9, 60.0, 0.0)) == "HIGH"

    def test_the_line_is_the_number_people_see(self):
        from crash_model.model import risk_band
        from global_model.serve import score_total

        assert risk_band(score_total(98.97, 50.0, 8.0)) == "CRITICAL"  # shown as 99.0

    def test_nightlife_still_adds_on_top(self):
        from global_model.serve import score_total

        assert score_total(99.5, 60.0, 16.0) == 76.0
        assert score_total(60.0, 30.0, 16.0) == 46.0
