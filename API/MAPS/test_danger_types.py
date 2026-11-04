"""Danger types: what kind of injury crash is over-represented here, now."""
from __future__ import annotations

import json
import math

import pytest

from crash_model import danger_types as dt
from crash_model.danger_types import Area, DangerTypes, band_for, groups_in, shrink


# --------------------------------------------------------------------------
# Pure pieces
# --------------------------------------------------------------------------


def test_every_hour_of_the_week_has_exactly_one_band():
    seen = {band_for(hour, day) for day in range(7) for hour in range(24)}
    assert seen == set(dt.BAND_IDS)


@pytest.mark.parametrize(
    "hour,day,band",
    [
        (23, 4, "weekend_night"),   # Friday 23:00
        (2, 5, "weekend_night"),    # Saturday 02:00 is Friday night
        (2, 6, "weekend_night"),    # Sunday 02:00 is Saturday night
        (2, 0, "weekday_late"),     # Monday 02:00
        (22, 6, "weekday_evening"), # Sunday 22:00: a school night
        (8, 2, "weekday_morning"),
        (14, 3, "weekday_afternoon"),
        (11, 5, "weekend_day"),
    ],
)
def test_bands_put_late_nights_with_the_night_they_started(hour, day, band):
    assert band_for(hour, day) == band


def test_factor_values_map_to_groups_case_and_space_insensitively():
    assert groups_in([" alcohol involvement", "Unspecified", None]) == {"impaired"}
    assert groups_in(["Fell Asleep", "Unsafe Speed"]) == {"drowsy", "speeding"}
    assert groups_in(["Sedan", "MANHATTAN", "Unspecified"]) == set()


def test_shrinkage_moves_between_the_local_share_and_the_prior():
    assert shrink(10, 100, 0.5, 0) == pytest.approx(0.1)
    assert shrink(0, 0, 0.3, 50) == pytest.approx(0.3)
    assert shrink(10, 100, 0.5, math.inf) == 0.5
    assert 0.1 < shrink(10, 100, 0.5, 100) < 0.5


# --------------------------------------------------------------------------
# Leakage: factors are targets, never inputs
# --------------------------------------------------------------------------


def test_crash_model_features_contain_no_post_crash_attributes():
    from crash_model.dataset import FEATURES

    # Words from the post-crash columns and the danger groups. ("wind_speed" is
    # weather, known before a trip, so "speed" alone is not a forbidden word.)
    forbidden = ("factor", "vehicle", "injur", "kill", "alcohol", "drug", "asleep",
                 "fatigue", "distract", "unsafe", "yield", "impair", "drowsy")
    assert not [name for name in FEATURES if any(word in name.lower() for word in forbidden)]
    assert not set(FEATURES) & set(dt.GROUP_IDS)


def test_a_danger_profile_depends_only_on_place_and_time(tmp_path):
    # Same area and band, different everything else: identical shares.
    model = DangerTypes(_write_artifact(tmp_path, n_nta=5000))
    area = Area("N1", "Somewhere", "MANHATTAN")
    a = model.profile(area, 23, 4, expected_rate=0.01)
    b = model.profile(area, 22, 5, expected_rate=0.01)  # also weekend night
    assert [g.get("share") for g in a["groups"]] == [g.get("share") for g in b["groups"]]


# --------------------------------------------------------------------------
# Behaviour, on a hand-made artifact
# --------------------------------------------------------------------------


def _write_artifact(path, *, n_nta=5000, k_nta=500, status="validated", level="neighbourhood"):
    """A tiny artifact: the city has a 2% share everywhere, area N1 has 10%."""
    groups = {}
    bands = {band: {"n": 100000, "k": {g: 2000 for g in dt.GROUP_IDS}} for band in dt.BAND_IDS}
    for g in dt.GROUP_IDS:
        groups[g] = {
            "level": level,
            "m_borough": 100.0,
            "m_nta": 100.0,
            "status": status,
            "city_type_rate": 0.003 * 0.02,
        }
    artifact = {
        "version": "test",
        "groups": groups,
        "counts": {
            "city": {"all": {"n": 600000, "k": {g: 12000 for g in dt.GROUP_IDS}}, "bands": bands},
            "borough": {"MANHATTAN": {b: {"n": 20000, "k": {g: 400 for g in dt.GROUP_IDS}} for b in dt.BAND_IDS}},
            "nta": {"N1": {b: {"n": n_nta, "k": {g: k_nta for g in dt.GROUP_IDS}} for b in dt.BAND_IDS}},
        },
    }
    (path / dt.ARTIFACT_FILE).write_text(json.dumps(artifact), encoding="utf-8")
    return str(path)


def test_well_supported_local_share_is_reported_with_its_evidence(tmp_path):
    model = DangerTypes(_write_artifact(tmp_path, n_nta=5000, k_nta=500))
    profile = model.profile(Area("N1", "Somewhere", "MANHATTAN"), 23, 4, expected_rate=0.006)
    group = profile["groups"][0]
    assert group["status"] == "ok"
    assert group["evidence"] == "neighbourhood"
    assert group["share"] == pytest.approx(shrink(500, 5000, shrink(400, 20000, (2000.5 / 100001), 100), 100), rel=1e-3)
    assert group["share_low"] < group["share"] < group["share_high"]
    assert group["share_ratio"] == pytest.approx(group["share"] / 0.02, rel=1e-2)
    # Against the city in the same band of the week, not the city overall.
    assert group["band_share_ratio"] == pytest.approx(
        group["share"] / group["city_band_share"], rel=1e-2
    )
    # Twice a typical cell-hour's rate, times a ~5x share.
    assert group["rate_ratio"] == pytest.approx(0.006 * group["share"] / (0.003 * 0.02), rel=1e-2)
    assert "Somewhere" in group["reason"]


def test_thin_local_data_falls_back_to_the_borough_as_evidence(tmp_path):
    model = DangerTypes(_write_artifact(tmp_path, n_nta=20, k_nta=10))
    group = model.profile(Area("N1", "Somewhere", "MANHATTAN"), 23, 4, 0.006)["groups"][0]
    # 20 crashes against a pseudo-count of 100: the neighbourhood is not the evidence.
    assert group["evidence"] == "borough"
    assert group["status"] == "ok"


def test_no_multiplier_when_too_few_events_could_be_expected(tmp_path):
    path = _write_artifact(tmp_path, level="time")
    artifact = json.loads((tmp_path / dt.ARTIFACT_FILE).read_text())
    for band in artifact["counts"]["city"]["bands"].values():
        band["n"], band["k"] = 100, {g: 1 for g in dt.GROUP_IDS}  # 100 x 1% = 1 event
    (tmp_path / dt.ARTIFACT_FILE).write_text(json.dumps(artifact))
    group = DangerTypes(path).profile(Area("N1", "Somewhere", "MANHATTAN"), 3, 1, 0.01)["groups"][0]
    assert group["status"] == "too_thin"
    assert "share_ratio" not in group and "rate_ratio" not in group
    assert "band_share_ratio" not in group
    assert "Not enough" in group["reason"]


def test_a_group_that_failed_its_gate_shows_nothing(tmp_path):
    model = DangerTypes(_write_artifact(tmp_path, status="not_validated"))
    group = model.profile(Area("N1", "Somewhere", "MANHATTAN"), 23, 4, 0.006)["groups"][0]
    assert group == {"id": group["id"], "label": group["label"], "status": "not_validated"}


def test_missing_artifact_is_a_stated_state(tmp_path):
    model = DangerTypes(str(tmp_path))
    assert model.available is False and model.error
    assert model.profile(Area("N1", "x", "MANHATTAN"), 1, 1, 0.01) is None


# --------------------------------------------------------------------------
# The trained artifact and the API
# --------------------------------------------------------------------------

REAL = DangerTypes()
needs_artifact = pytest.mark.skipif(not REAL.available, reason="danger_types.json not trained")


@needs_artifact
def test_artifact_counts_add_up_and_every_group_was_gated():
    meta = REAL.meta
    city = meta["counts"]["city"]
    assert sum(band["n"] for band in city["bands"].values()) == city["all"]["n"]
    for group in dt.GROUP_IDS:
        spec = meta["groups"][group]
        assert spec["status"] in ("validated", "not_validated")
        assert set(spec["gate"]) >= {"beats_city_constant", "top_decile_lift_at_least_1_5"}
        passed = spec["gate"]["beats_city_constant"] and spec["gate"]["top_decile_lift_at_least_1_5"]
        assert (spec["status"] == "validated") == passed
        assert spec["level"] in ("time", "borough", "neighbourhood")
    assert meta["data"]["split"]["test"] > 0
    assert "factor" not in " ".join(meta["inputs"]).lower()


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


@needs_artifact
def test_risk_reports_danger_types_inside_the_model(client):
    data = client.get("/risk?lat=40.7580&lon=-73.9855&hour=23&day=5").get_json()
    danger = data["danger_types"]
    assert danger["band"]["id"] == "weekend_night"
    assert [g["id"] for g in danger["groups"]] == dt.GROUP_IDS
    for group in danger["groups"]:
        assert group["status"] in ("ok", "too_thin", "not_validated")
        if group["status"] == "ok":
            assert group["share_low"] <= group["share"] <= group["share_high"]
            assert group["share_ratio"] > 0


@needs_artifact
def test_danger_types_never_change_the_score(client):
    import app as app_module

    with_types = client.get("/risk?lat=40.7580&lon=-73.9855&hour=23&day=5").get_json()
    original = app_module.DANGER_TYPES
    app_module.DANGER_TYPES = DangerTypes("/nonexistent")
    try:
        without = client.get("/risk?lat=40.7580&lon=-73.9855&hour=23&day=5").get_json()
    finally:
        app_module.DANGER_TYPES = original
    assert without["danger_types"] is None
    assert with_types["risk_score"] == without["risk_score"]
    assert with_types["risk_level"] == without["risk_level"]


def test_no_danger_types_outside_the_crash_model(client):
    london = client.get("/risk?lat=51.508&lon=-0.1281&hour=23&day=4").get_json()
    newark = client.get("/risk?lat=40.7357&lon=-74.1724&hour=23&day=5").get_json()
    assert london["danger_types"] is None
    assert newark["danger_types"] is None


@needs_artifact
def test_route_segments_carry_a_compact_danger_profile(client, monkeypatch):
    import app as app_module

    def fake_routes(olat, olon, dlat, dlon):
        line = [[-73.9900, 40.7500], [-73.9855, 40.7580], [-73.9800, 40.7650]]
        return [{"geometry": {"coordinates": line}, "distance": 1800.0, "duration": 720.0}], "fake"

    monkeypatch.setattr(app_module, "get_routes", fake_routes)
    monkeypatch.setattr(app_module, "get_cached_route", lambda route_id: None)
    monkeypatch.setattr(app_module, "cache_route", lambda *args, **kwargs: None)
    data = client.get("/safe_route?olat=40.75&olon=-73.99&dlat=40.765&dlon=-73.98&hour=23&day=5").get_json()
    modelled = [s for s in data["routes"][0]["segments"] if s["coverage"] == "model"]
    assert modelled
    for segment in modelled:
        groups = segment["danger_types"]["groups"]
        assert [g["id"] for g in groups] == dt.GROUP_IDS
        assert all("reason" not in g for g in groups)  # compact


@needs_artifact
def test_surface_danger_layer_agrees_with_risk_for_the_same_cell(client):
    from crash_model import risk_model

    layer = client.get("/surface?hour=23&day=5&danger=impaired").get_json()
    assert layer["danger_layer"]["group"] == "impaired"
    cells = {cell["cell"]: cell for cell in layer["cells"]}
    assert all("danger_rate_ratio" in cell and "danger_status" in cell for cell in cells.values())
    centre = cells[risk_model.cell_for(40.7580, -73.9855)]
    point = client.get(f"/risk?lat={centre['lat']}&lon={centre['lon']}&hour=23&day=5").get_json()
    impaired = next(g for g in point["danger_types"]["groups"] if g["id"] == "impaired")
    if impaired["status"] == "ok":
        assert centre["danger_rate_ratio"] == pytest.approx(impaired["rate_ratio"], rel=1e-3)


@needs_artifact
def test_switched_off_group_has_an_empty_layer(client):
    off = [g for g, spec in REAL.meta["groups"].items() if spec["status"] != "validated"]
    if not off:
        pytest.skip("every group validated")
    layer = client.get(f"/surface?hour=23&day=5&danger={off[0]}").get_json()
    assert layer["danger_layer"]["status"] == "not_validated"
    assert all(cell["danger_rate_ratio"] is None for cell in layer["cells"])


def test_surface_rejects_an_unknown_danger_group(client):
    response = client.get("/surface?hour=23&day=5&danger=gremlins")
    assert response.status_code == 400


@needs_artifact
def test_model_card_endpoint_serves_metrics_but_not_counts(client):
    data = client.get("/danger_types").get_json()
    assert set(data["groups"]) == set(dt.GROUP_IDS)
    for spec in data["groups"].values():
        assert spec["status"] in ("validated", "not_validated")
        assert set(spec["test"]) == {"city", "time", "borough", "neighbourhood"}
        assert len(spec["test_deciles"]) == 10
    assert "counts" not in data
    assert data["data"]["split"]["test"] > 0
