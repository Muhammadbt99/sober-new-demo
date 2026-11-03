"""The global context estimate: offline, deterministic, explainable."""
from __future__ import annotations

import pytest

import context_model as cm
from route_engine import annotate_segments, current_route_hint, plan_routes, score_geometry

DRY = {"temperature_c": 20.0, "precipitation_mm": 0.0, "rain": False, "snow": False,
       "fog": False, "wind_kph": 5.0, "visibility_km": 10.0, "provider": "test"}
PRIMARY = {"name": "Main Road", "class": "primary", "country": "GB"}


def score(**overrides):
    args = dict(lat=51.5, lon=-0.12, hour=12, day=2, cell="cell", weather=DRY, bars=0,
                road=PRIMARY, country="GB")
    args.update(overrides)
    lat, lon, hour, day = args.pop("lat"), args.pop("lon"), args.pop("hour"), args.pop("day")
    return cm.estimate(lat, lon, hour, day, **args)


def test_shape_matches_the_model_and_is_labelled_an_estimate():
    result = score()
    assert result["coverage"] == "context"
    assert result["model_version"] == cm.VERSION
    for key in ("risk_score", "risk_level", "percentile", "expected_rate", "model_points",
                "nightlife_points", "reasons", "action", "cell_area_km2"):
        assert key in result
    assert 0 <= result["percentile"] <= 100
    assert result["risk_score"] == pytest.approx(result["model_points"] + result["nightlife_points"])
    assert result["context"]["basis"].startswith("estimate")


def test_every_factor_moves_the_score_the_right_way():
    base = score()["risk_score"]
    assert score(hour=2)["risk_score"] > base  # small hours
    assert score(weather={**DRY, "rain": True, "precipitation_mm": 1.0})["risk_score"] > base
    snow = score(weather={**DRY, "snow": True, "temperature_c": -2.0})["risk_score"]
    rain = score(weather={**DRY, "rain": True, "precipitation_mm": 1.0})["risk_score"]
    assert snow > rain
    assert score(road={**PRIMARY, "class": "residential"})["risk_score"] < base
    assert score(country="SA")["risk_score"] > base  # 35.9 vs 3.2 deaths per 100k
    assert score(bars=40)["risk_score"] > base


def test_different_places_get_different_ratings():
    ratings = {
        score(country=country, road={**PRIMARY, "class": road}, hour=hour)["risk_score"]
        for country, road, hour in [("GB", "residential", 11), ("US", "primary", 17), ("SA", "trunk", 1), ("JP", "secondary", 9)]
    }
    assert len(ratings) == 4


def test_the_local_weekend_decides_which_nights_are_weekend_nights():
    # Thursday 23:00: a weekend night in Saudi Arabia (Fri-Sat weekend), not in Britain.
    assert cm.is_party_night(3, 23, "SA") is True
    assert cm.is_party_night(3, 23, "GB") is False
    # Saturday 23:00: the reverse.
    assert cm.is_party_night(5, 23, "GB") is True
    assert cm.is_party_night(5, 23, "SA") is False


def test_unknown_country_and_missing_weather_degrade_gracefully():
    result = score(country="ZZ", weather=None, road=None)
    assert result["coverage"] == "context"
    assert any("world median" in reason for reason in result["reasons"])
    assert any("assumed dry" in reason for reason in result["reasons"])


def test_routed_speed_and_junctions_count_on_a_route():
    calm = score(road=None, speed_kmh=30.0, junctions_per_km=1.0)["risk_score"]
    busy = score(road=None, speed_kmh=75.0, junctions_per_km=10.0)["risk_score"]
    assert busy > calm


# ---------------------------------------------------------------------------
# Route engine: hints and readable stretch labels
# ---------------------------------------------------------------------------

def _line(start, end, steps=20):
    return [(start[0] + (end[0] - start[0]) * i / steps, start[1] + (end[1] - start[1]) * i / steps)
            for i in range(steps + 1)]


def test_route_hints_reach_the_scorer_while_a_route_is_scored():
    points = _line((51.50, -0.12), (51.55, -0.12))
    raw = {
        "geometry": {"coordinates": [[lon, lat] for lat, lon in points]},
        "distance": 5500.0,
        "duration": 600.0,
        "speeds_kmh": [40.0] * 10 + [80.0] * 10,
        "junctions": [[51.51, -0.12], [51.511, -0.12]],
    }
    seen = []

    def score_at(lat, lon):
        seen.append(current_route_hint(lat, lon))
        return {"cell": f"c{round(lat, 2)}", "coverage": "model", "expected_rate": 0.01,
                "expected_rate_per_km2_hour": 0.01, "percentile": 60.0, "risk_score": 40.0,
                "risk_level": "MEDIUM", "cell_area_km2": 0.737}

    def provider(*_):
        return [raw], "fake"

    # No offset detours: those are synthetic geometry with no router data, by design.
    plan_routes(provider, points[0], points[-1], lambda lat, lon: (score_at, {}),
                lambda lat, lon: f"c{round(lat, 2)}", max_detours=0)
    assert seen and all(hint and "speed_kmh" in hint for hint in seen)
    assert {hint["speed_kmh"] for hint in seen} == {40.0, 80.0}
    assert current_route_hint(51.5, -0.12) is None  # cleared once scoring ends


def test_stretches_are_labelled_with_street_and_position():
    points = _line((51.50, -0.12), (51.56, -0.12), steps=30)
    exposure = score_geometry(
        points,
        lambda lat, lon: f"c{round(lat, 2)}",
        lambda lat, lon: {"expected_rate": 0.01, "percentile": 50.0, "risk_score": 30.0,
                          "risk_level": "MEDIUM", "coverage": "model", "cell_area_km2": 0.737},
    )
    annotate_segments(exposure, {"steps": [{"name": "First Street", "distance": 3000.0},
                                           {"name": "", "distance": 300.0},
                                           {"name": "Second Avenue", "distance": 3400.0}]})
    segments = exposure["segments"]
    assert segments[0]["from_km"] == 0.0
    assert segments[-1]["to_km"] == pytest.approx(sum(s["length_m"] for s in segments) / 1000.0, abs=0.01)
    assert segments[0]["street"] == "First Street"
    assert segments[-1]["street"] == "Second Avenue"
    assert all(segment["street"] for segment in segments)  # the unnamed step borrows a name


def test_country_lookup_works_offline_for_cities_on_every_continent():
    import providers

    expected = {(40.758, -73.9855): "US", (51.5074, -0.1278): "GB", (25.2048, 55.2708): "AE",
                (24.7136, 46.6753): "SA", (35.6595, 139.7005): "JP", (-33.8568, 151.2153): "AU",
                (6.4541, 3.3947): "NG", (-23.5505, -46.6333): "BR"}
    for (lat, lon), iso2 in expected.items():
        assert providers.country_offline(lat, lon) == iso2
    assert providers.country_offline(0.0, -140.0) is None  # mid-Pacific


def _step(kind, modifier, side="right", name="High Street", at=(51.5, -0.12)):
    return {"name": name, "distance": 100.0, "driving_side": side,
            "maneuver": {"type": kind, "modifier": modifier, "location": [at[1], at[0]]},
            "intersections": [{"location": [at[1], at[0]]}]}


def test_router_manoeuvres_are_read_from_the_steps():
    from routing import _osrm_details

    raw = {"legs": [{"steps": [
        _step("depart", "left"),
        _step("turn", "left"),
        _step("turn", "sharp right"),
        _step("turn", "slight left"),  # a bear, not a turn
        _step("roundabout", "right"),
        _step("on ramp", "slight right"),
        _step("continue", "uturn"),
        _step("new name", "straight"),
        _step("arrive", "left"),
    ]}]}
    found = _osrm_details(raw, 2)["maneuvers"]
    assert [item["kind"] for item in found] == ["turn", "turn", "roundabout", "merge", "uturn"]
    assert [item.get("turn") for item in found[:2]] == ["left", "right"]
    assert found[0]["street"] == "High Street"


def test_the_country_decides_which_turn_crosses_oncoming_traffic():
    # The public routers say "right-hand traffic" even in London, so the
    # country decides: a right turn crosses traffic in Britain and Japan, a left
    # turn does in France and the US.
    right_turn = {"kind": "turn", "turn": "right", "street": "Whitehall"}
    assert cm.maneuver_effect(right_turn, cm.driving_side("GB"))[0] == "across"
    assert cm.maneuver_effect(right_turn, cm.driving_side("JP"))[0] == "across"
    assert cm.maneuver_effect(right_turn, cm.driving_side("FR"))[0] == "kerbside"
    assert cm.maneuver_effect({"kind": "turn", "turn": "left"}, cm.driving_side("US"))[0] == "across"


def test_a_turn_across_traffic_raises_the_estimate_and_says_why():
    plain = score(road=None, speed_kmh=40.0)
    turn = score(road=None, speed_kmh=40.0, maneuvers=[
        {"kind": "turn", "turn": "right", "street": "Whitehall"}])
    kerb = score(road=None, speed_kmh=40.0, maneuvers=[
        {"kind": "turn", "turn": "left", "street": "Whitehall"}])
    assert turn["percentile"] > kerb["percentile"] > plain["percentile"]
    assert any("Right turn across oncoming traffic onto Whitehall" in r for r in turn["reasons"])
    assert turn["context"]["maneuvers"] == ["across"]
    pileup = score(road=None, speed_kmh=40.0, maneuvers=[{"kind": "uturn"}] * 6)
    assert pileup["context"]["road_factor"] == pytest.approx(
        plain["context"]["road_factor"] * cm.MANEUVER_CAP, rel=1e-3)


def test_route_hints_summarise_each_stretch_not_one_point():
    from route_engine import RouteHints

    points = _line((51.50, -0.12), (51.55, -0.12))
    raw = {
        "speeds_kmh": [30.0] * 10 + [90.0] * 10,
        "junctions": [[51.501, -0.12], [51.502, -0.12], [51.548, -0.12]],
        "maneuvers": [{"lat": 51.5015, "lon": -0.12, "kind": "turn", "turn": "right",
                       "street": "A Road"}],
    }
    hints = RouteHints(points, raw, lambda lat, lon: f"c{round(lat, 2)}")
    first = hints.at(51.501, -0.12)
    assert first["speed_kmh"] == 30.0
    assert first["maneuvers"] == [{"kind": "turn", "turn": "right", "street": "A Road"}]
    assert first["junctions_per_km"] == pytest.approx(2 / 0.556, rel=0.05)
    last = hints.at(51.549, -0.12)
    assert last["speed_kmh"] == 90.0
    assert "maneuvers" not in last
    # Without a cell function the hints fall back to what is near the point.
    near = RouteHints(points, raw).at(51.5016, -0.12)
    assert near["maneuvers"][0]["turn"] == "right"


def test_a_route_scored_only_by_the_estimate_is_labelled_context():
    points = _line((51.50, -0.12), (51.55, -0.12))
    exposure = score_geometry(
        points,
        lambda lat, lon: f"c{round(lat, 2)}",
        lambda lat, lon: {"expected_rate": 0.004, "percentile": 40.0, "risk_score": 30.0,
                          "risk_level": "MEDIUM", "coverage": "context", "cell_area_km2": 0.737},
    )
    assert exposure["coverage"] == "context"


# ---------------------------------------------------------------------------
# Nightlife counts and DNS resilience
# ---------------------------------------------------------------------------

def test_regional_overpass_servers_are_only_asked_about_their_region():
    import providers

    london = [name for name, _, _ in providers._overpass_servers(51.51, -0.13)]
    paris = [name for name, _, _ in providers._overpass_servers(48.86, 2.35)]
    tokyo = [name for name, _, _ in providers._overpass_servers(35.66, 139.70)]
    assert london[:2] == ["overpass-europe", "overpass-britain"]
    assert "overpass-britain" not in paris and paris[0] == "overpass-europe"
    assert all(name not in tokyo for name in ("overpass-europe", "overpass-britain"))
    assert "overpass" in tokyo  # the global servers cover everywhere


def test_venue_grid_counts_within_the_radius_and_knows_its_edges():
    import providers

    venues = [(51.5000, -0.1000), (51.5050, -0.1000), (51.5080, -0.1000), (51.5300, -0.1000)]
    grid = providers.VenueGrid(venues, (51.45, -0.20, 51.55, 0.00))
    assert grid.count(51.5000, -0.1000, 1000.0) == 3  # 0 m, 556 m and 890 m away; not 3.3 km
    assert grid.count(51.5000, -0.1000, 500.0) == 1
    assert grid.covers(51.50, -0.10, 1000.0)
    assert not grid.covers(51.545, -0.10, 1000.0)  # the circle would cross the box edge


def test_many_nightlife_counts_come_from_one_request(monkeypatch, live_providers):
    providers = live_providers  # the real nightlife_counts, with the network faked below
    sent = []

    def fake_overpass(query, lat, lon, budget_s=4.0, attempt_s=None):
        sent.append(query)
        return {"elements": [{"type": "count", "tags": {"total": str(n)}} for n in (3, 0, 51)]}

    monkeypatch.setattr(providers, "overpass", fake_overpass)
    monkeypatch.setattr(providers, "_nightlife_cache", providers.TTLCache(60))
    points = [(51.5, -0.1), (51.51, -0.11), (51.52, -0.12)]
    assert providers.nightlife_counts(points) == [3, 0, 51]
    assert len(sent) == 1 and sent[0].count("out count;") == 3
    # A reply that does not line up with the points is rejected, not misassigned.
    monkeypatch.setattr(providers, "_nightlife_cache", providers.TTLCache(60))
    monkeypatch.setattr(providers, "overpass", lambda *a, **k: {"elements": [{"type": "count", "tags": {"total": "1"}}]})
    assert providers.nightlife_counts(points) is None


def test_a_failed_dns_lookup_reuses_the_last_good_answer(monkeypatch):
    import socket

    import providers

    answers = [[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))]]

    def flaky(host, port, *args, **kwargs):
        if answers:
            return answers.pop()
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(providers, "_resolve", flaky)
    monkeypatch.setattr(providers, "_resolved", {})
    first = providers._getaddrinfo("photon.example", 443)
    assert providers._getaddrinfo("photon.example", 443) == first  # resolver down: last good answer
    with pytest.raises(socket.gaierror):
        providers._getaddrinfo("never-seen.example", 443)


def test_offline_city_list_finds_major_cities_without_accents_or_case():
    import gazetteer

    assert gazetteer.search("bangkok")[0]["name"] == "Bangkok"
    assert gazetteer.search("SAO")[0]["name"] == "Sao Paulo"
    assert gazetteer.search("rio")[0]["display_name"] == "Rio de Janeiro, Brazil"
    assert gazetteer.search("abu")[0]["name"] == "Abu Dhabi"
    assert all(item["source"] == "offline" for item in gazetteer.search("san"))
    assert gazetteer.search("x") == []


@pytest.fixture
def client():
    import app as app_module

    app_module.limiter.enabled = False
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_place_search_falls_back_to_the_offline_list(monkeypatch, client):
    import providers

    monkeypatch.setattr(providers, "_search_photon", lambda text, limit: None)
    monkeypatch.setattr(providers, "_search_nominatim", lambda text, limit: None)
    monkeypatch.setattr(providers, "_search_cache", providers.TTLCache(60))
    response = client.get("/geocode?q=Bangkok&limit=6")
    assert response.status_code == 200
    first = response.get_json()[0]
    assert first["name"] == "Bangkok" and first["source"] == "offline"
    # Nothing online and nothing offline: an explicit 503, not an empty success.
    assert client.get("/geocode?q=Qqzzxx&limit=6").status_code == 503


def test_place_search_prefers_a_live_geocoder(monkeypatch, client):
    import providers

    live = [{"place_id": 1, "display_name": "Abu Shagara, Sharjah, United Arab Emirates",
             "name": "Abu Shagara", "lat": "25.33", "lon": "55.39", "type": "suburb", "source": "photon"}]
    monkeypatch.setattr(providers, "_search_photon", lambda text, limit: live)
    monkeypatch.setattr(providers, "_search_cache", providers.TTLCache(60))
    assert client.get("/geocode?q=Abu+Shagara").get_json() == live


def test_search_ranking_puts_the_city_people_mean_first():
    import providers

    online = [
        {"name": "Bangkok", "type": "village", "lat": "-7.1", "lon": "110.4", "display_name": "Bangkok, Central Java, Indonesia"},
        {"name": "Bangkok Bank", "type": "bank", "lat": "13.72", "lon": "100.52", "display_name": "Bangkok Bank"},
        {"name": "Bangkok", "type": "city", "lat": "13.7525", "lon": "100.4935", "display_name": "Bangkok, Thailand"},
    ]
    ranked = providers._rank_places("bangkok", online, 6)
    assert ranked[0]["display_name"] == "Bangkok, Thailand"  # the online city, matched to the major one
    assert ranked[1]["type"] == "village" and ranked[-1]["name"] == "Bangkok Bank"
    # A major city the online answer missed is added from the offline list.
    assert providers._rank_places("bangkok", online[:2], 6)[0]["source"] == "offline"


def test_a_leading_the_does_not_hide_an_exact_match():
    import providers

    online = [
        {"name": "Burj Khalifa / Dubai Mall", "type": "station", "lat": "25.20", "lon": "55.27"},
        {"name": "The Dubai Mall", "type": "mall", "lat": "25.197", "lon": "55.279"},
    ]
    assert providers._rank_places("Dubai Mall", online, 6)[0]["name"] == "The Dubai Mall"
