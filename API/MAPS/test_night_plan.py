"""'Get home safe': every step restates something already scored."""
from __future__ import annotations

import pytest

import app as app_module
from app import app
import main
import night_plan
from night_plan import (
    build_steps,
    choose_pickup,
    danger_on_route,
    direction,
    is_night,
    rest_stop,
    streets_avoided,
)
import route_engine
import traffic


def segment(ratio, street=None, length_m=1000.0, status="ok"):
    return {
        "street": street,
        "length_m": length_m,
        "danger_types": {"band": "weekend_night", "groups": [{"id": "impaired", "status": status, "rate_ratio": ratio}]},
    }


class TestNightHours:
    @pytest.mark.parametrize("hour", [20, 23, 0, 3, 5])
    def test_night(self, hour):
        assert is_night(hour)

    @pytest.mark.parametrize("hour", [6, 12, 19])
    def test_day(self, hour):
        assert not is_night(hour)


class TestDirection:
    @pytest.mark.parametrize(
        "end, word",
        [
            ((1, 0), "north"),
            ((0, 1), "east"),
            ((-1, 0), "south"),
            ((0, -1), "west"),
            ((1, 1), "north-east"),
            ((-1, -1), "south-west"),
        ],
    )
    def test_the_compass(self, end, word):
        assert direction((0, 0), end) == word


class TestDangerOnRoute:
    def test_it_adds_up_the_high_stretches(self):
        found = danger_on_route([segment(3.0, "Bowery"), segment(2.0, "Canal"), segment(1.5, "Grand")])
        assert found == {"km": 2.0, "worst": 3.0, "streets": ["Bowery", "Canal"]}

    def test_no_crash_type_data_is_none_not_zero(self):
        assert danger_on_route([{"length_m": 1000.0}]) is None

    def test_data_with_nothing_high_is_zero(self):
        assert danger_on_route([segment(1.1)])["km"] == 0.0

    def test_a_thin_estimate_does_not_count(self):
        assert danger_on_route([segment(9.0, status="too_thin")])["km"] == 0.0


class TestStreetsAvoided:
    def test_it_names_what_the_chosen_route_stays_off(self):
        fastest = {"segments": [segment(5.0, "Bowery"), segment(3.0, "Canal")]}
        chosen = {"segments": [segment(1.0, "Canal")]}
        assert streets_avoided(fastest, chosen) == ["Bowery"]

    def test_the_fastest_route_avoids_nothing_of_its_own(self):
        route = {"segments": [segment(5.0, "Bowery")]}
        assert streets_avoided(route, route) == []


class TestPickup:
    ORIGIN = (25.0, 55.0)

    def cell(self, lat_offset, percentile):
        return {"lat": 25.0 + lat_offset, "lon": 55.0, "percentile": percentile}

    def test_the_nearest_clearly_lower_place_wins(self):
        pick = choose_pickup(self.ORIGIN, 90.0, [self.cell(0.008, 40.0), self.cell(0.004, 60.0)])
        assert pick["percentile"] == 60.0 and pick["direction"] == "north"
        assert pick["walk_m"] == pytest.approx(445, abs=5)

    def test_a_small_drop_is_not_worth_a_walk(self):
        assert choose_pickup(self.ORIGIN, 90.0, [self.cell(0.004, 80.0)]) is None

    def test_too_far_is_not_offered(self):
        assert choose_pickup(self.ORIGIN, 90.0, [self.cell(0.02, 10.0)]) is None

    def test_without_a_start_percentile_there_is_nothing_to_compare(self):
        assert choose_pickup(self.ORIGIN, None, [self.cell(0.004, 10.0)]) is None


class TestOrdinal:
    @pytest.mark.parametrize(
        "value, word",
        [(81, "81st"), (82.4, "82nd"), (83, "83rd"), (84, "84th"), (11, "11th"), (12, "12th"), (13, "13th"), (100, "99th"), (0, "1st")],
    )
    def test_the_words(self, value, word):
        assert night_plan.ordinal(value) == word


class TestRestStop:
    def test_a_rest_area_beats_a_nearer_car_park(self):
        stops = [{"name": "P", "type": "parking", "distance_m": 10}, {"name": "R", "type": "rest_area", "distance_m": 900}]
        assert rest_stop(stops)["name"] == "R"

    def test_a_hospital_is_not_a_rest_stop(self):
        assert rest_stop([{"name": "H", "type": "hospital", "distance_m": 10}]) is None


def stretch(level, length_m=1000.0, street=None):
    return {"risk_level": level, "length_m": length_m, "street": street}


def option(minutes, risk, *segments, **flags):
    return {
        "duration_min": minutes,
        "distance_km": 20.0,
        "expected_crashes_total": risk,
        "segments": list(segments) or [stretch("LOW")],
        **flags,
    }


class TestScan:
    def test_every_road_is_listed_with_the_pick_first(self):
        routes = [option(30, 1.0), option(25, 0.8), option(40, 0.7)]
        options = night_plan.scan_options(routes, 1)
        assert [item["route_index"] for item in options] == [1, 0, 2]
        assert options[0]["is_best"] is True
        assert len(options) == 3

    def test_a_faster_riskier_road_says_so(self):
        text = night_plan.why_not(option(20, 1.5), option(30, 1.0))
        assert text == "10 min faster, but 50% more risk."

    def test_a_slower_safer_road_says_so(self):
        text = night_plan.why_not(option(45, 0.9), option(30, 1.0))
        assert text == "15 min slower, but 10% less risk."

    def test_a_road_worse_on_both_says_so(self):
        text = night_plan.why_not(option(45, 1.2), option(30, 1.0))
        assert text == "15 min slower and 20% more risk."

    def test_near_ties_are_not_dressed_up(self):
        text = night_plan.why_not(option(30.2, 1.002), option(30, 1.0))
        assert text == "About the same time and about the same risk."

    def test_extra_critical_road_is_named(self):
        worse = option(20, 1.5, stretch("CRITICAL", 3000.0))
        assert "3.0 km more critical road" in night_plan.why_not(worse, option(30, 1.0))

    def test_why_the_pick_won(self):
        fastest = option(20, 1.0)
        assert "fastest" in night_plan.why_best(fastest, fastest)
        assert night_plan.why_best(option(26, 0.6), fastest) == "40% less risk than the fastest road, for 6 more minutes."

    def test_roads_use_the_names_on_their_cards(self):
        routes = [option(30, 1.0, is_safest=True), option(25, 1.1, is_fastest=True), option(40, 1.2, rank=3)]
        names = {item["route_index"]: item["label"] for item in night_plan.scan_options(routes, 0)}
        assert names == {0: "Safest", 1: "Fastest", 2: "Option 3"}

    def test_a_road_both_fastest_and_safest_says_both(self):
        route = option(20, 1.0, is_safest=True)
        assert night_plan.why_best(route, route) == "It is both the fastest and the lowest-risk road."


GUIDE = [
    {"offset_m": 0, "message": "Leave from Burj Khalifa", "maneuver": "DEPART"},
    {"offset_m": 1000, "message": "Turn right onto Sheikh Zayed Road", "maneuver": "TURN_RIGHT"},
    {"offset_m": 3000, "message": "Take the exit", "maneuver": "TAKE_EXIT"},
    {"offset_m": 3100, "message": "Keep left", "maneuver": "KEEP_LEFT"},
    {"offset_m": 4000, "message": "You have arrived", "maneuver": "ARRIVE"},
]


class TestLegs:
    ROUTE = option(20, 1.0, stretch("LOW"), stretch("CRITICAL"), stretch("LOW"), stretch("HIGH"))

    def legs(self, *args, **kwargs):
        return night_plan.build_legs(self.ROUTE, *args, **kwargs)

    def test_each_move_says_how_far_and_how_risky(self):
        legs = self.legs(GUIDE, 4000.0)
        turn = next(leg for leg in legs if "Sheikh Zayed" in leg["message"])
        assert turn["km"] == 2.0 and turn["level"] == "CRITICAL" and turn["risky_km"] == 1.0

    def test_a_calm_move_carries_no_level(self):
        assert self.legs(GUIDE, 4000.0)[0]["level"] is None

    def test_a_leg_too_short_to_read_is_dropped_even_if_risky(self):
        guide = [
            {"offset_m": 0, "message": "Go", "maneuver": "DEPART"},
            {"offset_m": 3980, "message": "Keep right", "maneuver": "KEEP_RIGHT"},
            {"offset_m": 4000, "message": "Arrive", "maneuver": "ARRIVE"},
        ]
        legs = night_plan.build_legs(option(20, 1.0, stretch("CRITICAL", 4000.0)), guide, 4000.0)
        assert [leg["message"] for leg in legs] == ["Go"]

    def test_a_short_calm_hop_is_dropped(self):
        route = option(20, 1.0, stretch("LOW", 4000.0))
        legs = night_plan.build_legs(route, GUIDE, 4000.0)
        assert not any(leg["message"] == "Take the exit" for leg in legs)

    def test_arriving_is_not_a_leg(self):
        assert not any("arrived" in leg["message"] for leg in self.legs(GUIDE, 4000.0))

    def test_offsets_are_scaled_to_our_route(self):
        legs = self.legs(GUIDE, 2000.0)
        assert next(leg for leg in legs if "Sheikh Zayed" in leg["message"])["km"] == 4.0

    def test_a_long_trip_keeps_the_risky_moves(self):
        many = [{"offset_m": i * 100, "message": f"Step {i}", "maneuver": "STRAIGHT"} for i in range(40)]
        legs = self.legs(many, 4000.0, limit=4)
        assert len(legs) == 4 and all(leg["level"] for leg in legs)

    def test_without_directions_streets_stand_in(self):
        route = option(20, 1.0, stretch("LOW", street="Main St"), stretch("HIGH", street="Main St"), stretch("LOW", street="Oak Ave"))
        legs = night_plan.build_legs(route, [], None)
        assert [leg["message"] for leg in legs] == ["Continue on Main St", "Continue on Oak Ave"]
        assert legs[0]["level"] == "HIGH"


def steps(**overrides):
    values = dict(
        here={"risk_level": "CRITICAL", "percentile": 99.2},
        fastest_danger={"km": 9.8, "worst": 13.5, "streets": ["Bowery"]},
        scanned=5,
        best_label="Safest",
        best={"duration_min": 23.4},
        why="40% less risk than the fastest road, for 6 more minutes.",
        timing={"available": True, "text": "Timing makes little difference."},
        avoided=[],
        legs=[{"message": "Turn right onto Bowery", "km": 2.0, "level": "CRITICAL", "risky_km": 1.5}],
        destination_note="About 8.7 km in all.",
        pickup=None,
        stop=None,
    )
    return build_steps(**{**values, **overrides})


class TestBuildSteps:
    def test_the_order_runs_from_here_to_arriving(self):
        kinds = [step["kind"] for step in steps()]
        assert kinds == ["now", "danger", "best", "when", "care", "drive", "arrive"]

    def test_it_says_how_many_roads_it_scanned(self):
        best = next(step for step in steps() if step["kind"] == "best")
        assert best["title"] == "Best of 5 roads: the safest one, 23 min"

    def test_a_risky_leg_says_what_to_do_there(self):
        leg = next(step for step in steps() if step["kind"] == "drive")
        assert leg["level"] == "CRITICAL"
        assert leg["detail"] == "2.0 km · critical for 1.5 km"

    def test_a_wholly_critical_leg_says_so(self):
        legs = [{"message": "Go", "km": 2.0, "level": "CRITICAL", "risky_km": 2.0}]
        leg = next(step for step in steps(legs=legs) if step["kind"] == "drive")
        assert leg["detail"] == "2.0 km · critical all the way"

    def test_the_advice_is_given_once_with_the_total(self):
        legs = [
            {"message": "A", "km": 2.0, "level": "CRITICAL", "risky_km": 2.0},
            {"message": "B", "km": 3.0, "level": "CRITICAL", "risky_km": 1.5},
        ]
        built = steps(legs=legs)
        assert sum("Slow down" in step["detail"] for step in built) == 1
        care = next(step for step in built if step["kind"] == "care")
        assert care["title"] == "Take extra care on 3.5 km of critical road"
        assert [step["kind"] for step in built].index("care") == [step["kind"] for step in built].index("drive") - 1

    def test_a_calm_drive_gets_no_warning(self):
        legs = [{"message": "A", "km": 2.0, "level": None, "risky_km": 0.0}]
        assert "care" not in [step["kind"] for step in steps(legs=legs)]

    def test_the_danger_line_quotes_the_fastest_road(self):
        line = next(step for step in steps() if step["kind"] == "danger")
        assert "13.5×" in line["title"] and "9.8 km" in line["detail"]

    def test_outside_new_york_no_figure_is_given(self):
        line = next(step for step in steps(fastest_danger=None) if step["kind"] == "danger")
        assert "New York only" in line["title"] and "×" not in line["title"]

    def test_an_avoided_street_is_named(self):
        line = next(step for step in steps(avoided=["Bowery"]) if step["kind"] == "best")
        assert "Bowery" in line["detail"]

    def test_a_missing_chart_drops_its_step(self):
        assert "when" not in [step["kind"] for step in steps(timing={"available": False})]

    def test_pickup_and_stop_appear_only_when_found(self):
        kinds = [
            step["kind"]
            for step in steps(
                stop={"name": "Car park", "distance_km": 1.2},
                pickup={"walk_m": 450, "direction": "north", "percentile": 60.0, "from_percentile": 90.0},
            )
        ]
        assert kinds[-3:] == ["stop", "arrive", "pickup"]

    def test_the_walk_is_labelled_straight_line(self):
        pickup = {"walk_m": 450, "direction": "north", "percentile": 60.0, "from_percentile": 90.0}
        assert "straight line" in steps(pickup=pickup)[-1]["detail"]


class TestNoLeavingInThePast:
    SEGMENTS = [{"cell": "a", "midpoint": [40.7, -73.9], "length_m": 1000.0, "expected_rate": 0.02}]

    def rates(self, by_hour):
        return lambda lat, lon, hour, day: {"expected_rate": by_hour.get(hour, 0.02), "cell_area_km2": 1.0}

    def test_a_live_trip_is_never_told_to_leave_earlier(self):
        plan = route_engine.plan_departures(
            self.SEGMENTS, 21, 1, self.rates({19: 0.01}), can_leave_earlier=False
        )
        assert plan["recommended"] is None
        assert any(slot["is_past"] for slot in plan["slots"])

    def test_a_planned_trip_still_can_be(self):
        plan = route_engine.plan_departures(self.SEGMENTS, 21, 1, self.rates({19: 0.01}))
        assert plan["recommended"]["hour"] == 19

    def test_a_later_hour_is_still_offered_to_a_live_trip(self):
        plan = route_engine.plan_departures(
            self.SEGMENTS, 18, 1, self.rates({20: 0.01}), can_leave_earlier=False
        )
        assert plan["recommended"]["hour"] == 20


def road(meters, seconds):
    return {
        "geometry": {"coordinates": [[-73.9855, 40.7580], [-73.9840, 40.7450], [-73.9800, 40.7300]]},
        "distance": float(meters),
        "duration": float(seconds),
    }


NIGHT = "olat=40.7580&olon=-73.9855&dlat=40.7300&dlon=-73.9800&hour=23&day=5"


class TestEndpoint:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(app_module, "get_routes", lambda *a: ([road(3200, 600)], "fake"))
        monkeypatch.setattr(app_module, "get_via_route", lambda *a: ([], "fake"))
        monkeypatch.setattr(app_module, "get_nearest_safe", lambda *a: [])
        monkeypatch.setattr(traffic, "route_options", lambda *a: [])
        monkeypatch.setattr(traffic, "route_times", lambda *a, **k: {"available": False, "reason": "off"})
        monkeypatch.setattr(traffic, "route_guidance", lambda *a: {"available": False})
        main.active_routes.clear()
        with app.test_client() as client:
            yield client
        main.active_routes.clear()

    def test_a_night_trip_gets_a_plan_built_from_the_scored_route(self, client):
        body = client.get(f"/night_plan?{NIGHT}&date=2026-11-07").get_json()
        assert body["applies"] is True
        kinds = [step["kind"] for step in body["steps"]]
        assert kinds[:3] == ["now", "danger", "best"] and "arrive" in kinds
        assert body["scanned"] == len(body["options"]) >= 1

    def test_its_route_is_the_recommended_one(self, client):
        route = client.get(f"/safe_route?{NIGHT}&date=2026-11-07").get_json()
        body = client.get(f"/night_plan?{NIGHT}&date=2026-11-07").get_json()
        assert body["best_index"] == route["recommended_route"]

    def test_a_daytime_trip_has_no_plan(self, client):
        day = NIGHT.replace("hour=23", "hour=12")
        body = client.get(f"/night_plan?{day}&date=2026-11-07").get_json()
        assert body["applies"] is False and body["steps"] == []

    def test_the_driver_state_is_never_a_parameter(self):
        source = open(night_plan.__file__, encoding="utf-8").read().split('"""', 2)[2]
        assert "alcohol" not in source and "awake" not in source

    def test_bad_input_is_refused(self, client):
        assert client.get("/night_plan?olat=40.7").status_code == 400

    def test_a_slow_stop_search_does_not_hold_the_plan_up(self, client, monkeypatch):
        def slow(*args):
            raise TimeoutError("stops are down")

        monkeypatch.setattr(app_module, "get_nearest_safe", slow)
        assert client.get(f"/night_plan?{NIGHT}&date=2026-11-07").status_code == 200
