"""The whole-city surface, place names and local hotspots behind the Explore tab."""
from __future__ import annotations

import math

import pytest

from app import app
from crash_model import risk_model
from crash_model.model import RiskModel

pytestmark = pytest.mark.skipif(
    not risk_model.available, reason="crash model artifact is not loaded"
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


TIMES_SQUARE = (40.7580, -73.9855)


# --------------------------------------------------------------------------
# Batched scoring
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hour,day,weather", [(23, 5, (11.0, 0.0, 14.0)), (3, 1, None), (8, 2, (2.5, 1.0, 30.0))])
def test_batched_surface_matches_single_point_scores(hour, day, weather):
    """One batched prediction must give each cell the number /risk would."""
    batch_model = RiskModel()
    single_model = RiskModel()
    cells = batch_model.cells
    bars = [index % 70 for index in range(len(cells))]
    surface = batch_model.score_cells(hour, day, weather=weather, bars=bars)
    assert len(surface) == len(cells) == batch_model.info.cell_count

    # Every 37th cell plus Times Square: a spread across the boroughs.
    picks = list(range(0, len(surface), 37))
    picks.append(list(cells.index).index(single_model.cell_for(*TIMES_SQUARE)))
    for index in picks:
        cell = surface[index]
        point = single_model.score(cell["lat"], cell["lon"], hour, day, weather=weather, bars=bars[index])
        assert point["cell"] == cell["cell"]
        assert cell["expected_rate"] == pytest.approx(point["expected_rate"], rel=1e-12)
        assert cell["percentile"] == pytest.approx(point["percentile"])
        assert cell["model_points"] == point["model_points"]
        assert cell["nightlife_points"] == point["nightlife_points"]
        assert cell["risk_score"] == point["risk_score"]
        assert cell["risk_level"] == point["risk_level"]


def test_batched_scoring_seeds_the_point_cache():
    model = RiskModel()
    model.score_cells(23, 5, weather=None)
    before = len(model._predictions)
    assert before == model.info.cell_count
    model.score(*TIMES_SQUARE, 23, 5, weather=None, bars=0)
    assert len(model._predictions) == before  # answered from the cache


def test_surface_cells_carry_neighbourhood_names():
    surface = RiskModel().score_cells(12, 2)
    named = [cell for cell in surface if cell["name"]]
    assert len(named) > 0.95 * len(surface)
    times_square = next(cell for cell in surface if cell["cell"] == risk_model.cell_for(*TIMES_SQUARE))
    assert "Times Square" in times_square["name"]
    assert times_square["borough"] == "Manhattan"


# --------------------------------------------------------------------------
# /surface
# --------------------------------------------------------------------------


def test_surface_endpoint_returns_every_model_cell(client):
    response = client.get("/surface?hour=23&day=5")
    assert response.status_code == 200
    data = response.get_json()
    assert data["schema_version"] == "1.0.0"
    assert data["coverage"] == "model"
    assert data["cell_count"] == len(data["cells"]) == risk_model.info.cell_count
    assert len({cell["cell"] for cell in data["cells"]}) == data["cell_count"]
    for cell in data["cells"][:50]:
        assert 0 <= cell["risk_score"] <= 100
        assert cell["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert 0 <= cell["model_points"] <= 60
        assert 0 <= cell["nightlife_points"] <= 40


def test_surface_agrees_with_risk_at_cell_centres(client):
    """The map's hexes and the panel must show the same score for a cell."""
    data = client.get("/surface?hour=23&day=5").get_json()
    by_cell = {cell["cell"]: cell for cell in data["cells"]}
    for lat, lon in [TIMES_SQUARE, (40.7143, -73.9613), (40.6413, -73.7781), (40.8116, -73.9465)]:
        cell_id = risk_model.cell_for(lat, lon)
        if cell_id not in by_cell:
            continue
        centre = by_cell[cell_id]
        point = client.get(f"/risk?lat={centre['lat']}&lon={centre['lon']}&hour=23&day=5").get_json()
        assert point["cell"] == cell_id
        assert point["expected_rate"] == pytest.approx(centre["expected_rate"])
        assert point["risk_score"] == pytest.approx(centre["risk_score"])
        assert point["risk_level"] == centre["risk_level"]


def test_surface_moves_with_the_hour(client):
    night = {c["cell"]: c["expected_rate"] for c in client.get("/surface?hour=3&day=5").get_json()["cells"]}
    evening = {c["cell"]: c["expected_rate"] for c in client.get("/surface?hour=18&day=5").get_json()["cells"]}
    changed = sum(1 for cell in night if not math.isclose(night[cell], evening[cell]))
    assert changed > 0.9 * len(night)


@pytest.mark.parametrize("query", ["hour=24", "hour=x", "day=9"])
def test_surface_rejects_bad_time(client, query):
    response = client.get(f"/surface?{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


# --------------------------------------------------------------------------
# Place names on /risk and /risk/compare
# --------------------------------------------------------------------------


def test_risk_names_the_neighbourhood_inside_the_model(client):
    data = client.get("/risk?lat=40.7580&lon=-73.9855&hour=23&day=5").get_json()
    assert data["coverage"] == "model"
    assert "Times Square" in data["place"]["name"]
    assert data["place"]["area"] == "Manhattan"
    assert data["place"]["source"] == "neighbourhood"


def test_risk_names_road_and_country_outside_nyc(client):
    # conftest's offline stand-ins: road "Test Road", country GB above 49N.
    data = client.get("/risk?lat=51.508&lon=-0.1281&hour=23&day=4").get_json()
    assert data["coverage"] == "context"
    assert data["place"] == {"name": "Test Road", "area": "United Kingdom", "source": "road"}


def test_an_unmodelled_spot_beside_the_city_borrows_the_nearest_name(client):
    # Search a line of points over the Hudson for one with no model cell but a
    # modelled neighbour within 1.2 km; it must be named "Near <neighbourhood>".
    for step in range(12):
        lat, lon = 40.7580, -74.0080 - step * 0.002
        if risk_model.cell_for(lat, lon) in risk_model.cells.index:
            continue
        near = risk_model.nearest_cell_info(lat, lon)
        data = client.get(f"/risk?lat={lat}&lon={lon}&hour=23&day=5").get_json()
        assert data["coverage"] == "rule_based_only"
        if near is None:
            assert data["place"] is None
        else:
            assert data["place"]["name"] == f"Near {near['name']}"
            assert data["place"]["source"] == "nearest_neighbourhood"
            return
    pytest.skip("no unmodelled point beside a modelled cell on this line")


def test_compare_names_both_places(client):
    williamsburg = (40.7143, -73.9613)
    assert risk_model.cell_for(*williamsburg) in risk_model.cells.index
    data = client.get(
        f"/risk/compare?lat1=40.7580&lon1=-73.9855&lat2={williamsburg[0]}&lon2={williamsburg[1]}&hour=23&day=5"
    ).get_json()
    assert "Times Square" in data["location_a"]["place"]["name"]
    assert data["location_b"]["place"]["source"] == "neighbourhood"
    assert data["location_b"]["place"]["area"] == "Brooklyn"


def test_a_place_with_no_model_cell_nearby_has_no_name(client):
    # JFK's terminals have no injury-crash cell within 1.2 km: say nothing
    # rather than borrow a far-away neighbourhood's name.
    assert risk_model.cell_for(40.6413, -73.7781) not in risk_model.cells.index
    data = client.get("/risk?lat=40.6413&lon=-73.7781&hour=23&day=5").get_json()
    assert data["coverage"] == "rule_based_only"
    assert data["place"] is None


# --------------------------------------------------------------------------
# Local hotspots
# --------------------------------------------------------------------------


def test_hotspots_near_a_point_are_local_and_busiest_first(client):
    data = client.get("/hotspots?lat=40.7580&lon=-73.9855&radius=2000&top_k=10").get_json()
    assert data["near"] == {"lat": 40.758, "lon": -73.9855, "radius_m": 2000}
    spots = data["hotspots"]
    assert 0 < len(spots) <= 10
    assert all(spot["distance_m"] <= 2000 for spot in spots)
    counts = [spot["crash_count"] for spot in spots]
    assert counts == sorted(counts, reverse=True)
    assert [spot["rank"] for spot in spots] == list(range(1, len(spots) + 1))


def test_hotspots_near_a_point_outside_nyc_is_empty(client):
    data = client.get("/hotspots?lat=51.508&lon=-0.1281").get_json()
    assert data["hotspots"] == []
    assert data["total_hotspots"] == 0


def test_hotspots_without_a_point_keep_the_city_wide_list(client):
    data = client.get("/hotspots?top_k=5").get_json()
    assert "near" not in data
    assert len(data["hotspots"]) == 5


@pytest.mark.parametrize("query", ["lat=40.75", "lat=abc&lon=-73.98", "lat=40.75&lon=-73.98&radius=-5"])
def test_hotspots_near_rejects_bad_input(client, query):
    response = client.get(f"/hotspots?{query}")
    assert response.status_code == 400
