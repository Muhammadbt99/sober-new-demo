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


# --------------------------------------------------------------------------- no road here

EMPTY_QUARTER = (23.5, 55.0)
OPEN_GULF = (25.6, 55.2)
PARIS = (48.85, 2.35)


@pytest.fixture
def offline_lookups(monkeypatch):
    """No network: a private road cache, and each point's road lookup outcome.

    ``"empty"`` is a finished lookup that found no road, ``"fail"`` a failed one
    (nothing cached), a dict a road found.
    """
    import providers

    outcomes = {}

    def fake_road_at(lat, lon, fallback=True):
        key = (round(lat, 4), round(lon, 4))
        outcome = outcomes.get(key, "fail")
        if outcome == "empty":
            providers._road_cache.put(key, {})
            return None
        if isinstance(outcome, dict):
            providers._road_cache.put(key, outcome)
            return outcome
        return None

    monkeypatch.setattr(providers, "_road_cache", providers.TTLCache(3600))
    monkeypatch.setattr(providers, "road_at", fake_road_at)
    monkeypatch.setattr(providers, "country_at", lambda lat, lon: providers.country_offline(lat, lon))
    monkeypatch.setattr(providers, "current_weather", lambda lat, lon: None)
    monkeypatch.setattr(app_module, "get_weather_ex", lambda lat, lon: ((20, 0, 0), "live"))
    monkeypatch.setattr(app_module, "get_bars_ex", lambda lat, lon, radius=1000: (0, "live"))
    return outcomes


def _key(point):
    return (round(point[0], 4), round(point[1], 4))


def _risk(client, point, hour=23, day=3):
    return client.get(f"/risk?lat={point[0]}&lon={point[1]}&hour={hour}&day={day}").get_json()


@needs_model
def test_empty_quarter_has_no_score(client, offline_lookups):
    data = _risk(client, EMPTY_QUARTER)
    assert data["coverage"] == "rule_based_only"
    assert data["coverage_reason"] == "no_road_here"
    assert data["risk_score"] == 0
    assert data["percentile"] is None
    assert data["context"] is None


@needs_model
def test_open_gulf_with_a_finished_empty_road_lookup_has_no_score(client, offline_lookups, monkeypatch):
    import providers

    offline_lookups[_key(OPEN_GULF)] = "empty"
    monkeypatch.setattr(providers, "country_offline", lambda lat, lon, reach_deg=0.6: None)
    data = _risk(client, OPEN_GULF)
    assert data["coverage"] == "rule_based_only"
    assert data["coverage_reason"] == "no_road_here"
    assert data["risk_score"] == 0


@needs_model
def test_open_gulf_with_a_failed_road_lookup_keeps_the_estimate(client, offline_lookups, monkeypatch):
    import providers

    offline_lookups[_key(OPEN_GULF)] = "fail"
    monkeypatch.setattr(providers, "country_offline", lambda lat, lon, reach_deg=0.6: None)
    data = _risk(client, OPEN_GULF)
    assert data["coverage_reason"] != "no_road_here"
    assert data["coverage"] == "context"
    assert data["context"] is not None


@needs_model
def test_a_dubai_point_in_the_table_keeps_the_global_model(client, offline_lookups):
    data = _risk(client, DOWNTOWN_DUBAI)
    assert data["context"]["method"] == "global_model"
    assert data["coverage_reason"] != "no_road_here"


@needs_model
def test_paris_with_no_road_found_keeps_the_estimate(client, offline_lookups):
    offline_lookups[_key(PARIS)] = "empty"
    data = _risk(client, PARIS)
    assert data["coverage"] == "context"
    assert data["coverage_reason"] != "no_road_here"
    assert (data["context"] or {}).get("method") != "global_model"


def test_road_lookups_that_fail_are_not_taken_as_no_road(monkeypatch):
    import providers

    monkeypatch.setattr(providers, "_road_cache", providers.TTLCache(3600))
    assert providers.no_road_found(*OPEN_GULF) is False
    providers._road_cache.put(_key(OPEN_GULF), {})
    assert providers.no_road_found(*OPEN_GULF) is True
    providers._road_cache.put(_key(OPEN_GULF), {"name": "x", "class": "primary", "country": "AE"})
    assert providers.no_road_found(*OPEN_GULF) is False


@needs_model
def test_fast_road_flag_follows_the_motorway_and_trunk_length():
    table = GLOBAL.regions["uae"].table
    major = (table["km_motorway"] + table["km_trunk"]).fillna(0)
    fast = table[major >= 0.3].iloc[0]
    slow = table[major < 0.3].iloc[0]
    assert GLOBAL.score(float(fast["lat"]), float(fast["lon"]), 23, 5)["context"]["fast_road"] is True
    assert GLOBAL.score(float(slow["lat"]), float(slow["lon"]), 23, 5)["context"]["fast_road"] is False


@pytest.mark.parametrize("lat, lon", [(26.0, 54.5), (25.6, 55.2), (26.8, 52.0), (27.0, 50.5), (36.0, -40.0)])
def test_open_water_is_off_the_land_map_even_where_a_country_outline_reaches(lat, lon):
    import providers

    assert providers.on_land(lat, lon) is False


@pytest.mark.parametrize(
    "lat, lon",
    [(25.2, 55.27), (35.7, 51.4), (29.6, 52.5), (25.28, 51.52), (48.85, 2.35), (-33.9, 151.2), (23.5, 55.0)],
)
def test_cities_and_desert_are_on_the_land_map(lat, lon):
    import providers

    assert providers.on_land(lat, lon) is True


def test_gulf_water_inside_a_coarse_outline_has_no_score(client, offline_lookups, monkeypatch):
    """(26.0, 54.5) is sea, but the Iran outline reaches it, so the country check said land."""
    import providers

    assert providers.country_offline(26.0, 54.5) == "IR"

    def finished_empty(lat, lon, fallback=True):
        providers._road_cache.put((round(lat, 4), round(lon, 4)), {})

    monkeypatch.setattr(providers, "road_at", finished_empty)
    data = client.get("/risk?lat=26.0&lon=54.5&hour=18&day=4").get_json()
    assert data["coverage_reason"] == "no_road_here"
    assert data["risk_score"] == 0


def test_bulk_scoring_at_sea_needs_no_lookup_but_a_sampled_road_keeps_the_estimate():
    import app as api

    assert api.nothing_here(26.0, 54.5, {}, False, None) is True
    assert api.nothing_here(26.0, 54.5, {}, False, {"class": "primary"}) is False
    assert api.nothing_here(26.0, 54.5, {"speed_kmh": 40}, False, None) is False


@pytest.mark.parametrize(
    "answer, no_road",
    [
        ({"category": "boundary", "type": "administrative", "name": "A county"}, True),
        ({"category": "natural", "type": "bay", "name": "A bay"}, True),
        ({"error": "Unable to geocode"}, True),
        ({"category": "highway", "type": "pedestrian", "name": "Calle del Nonzolo"}, False),
        ({"category": "building", "type": "yes", "name": "A building"}, False),
    ],
)
def test_the_backup_lookup_only_counts_as_no_road_when_nothing_is_at_the_point(monkeypatch, answer, no_road):
    import providers

    monkeypatch.setattr(providers, "_road_cache", providers.TTLCache(60, limit=8))
    monkeypatch.setattr(providers, "_nominatim", lambda *args, **kwargs: answer)
    providers._road_nominatim(26.0, 54.5, (26.0, 54.5))
    assert providers.no_road_found(26.0, 54.5) is no_road
