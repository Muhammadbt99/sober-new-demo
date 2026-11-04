"""The departure planner: which hour to leave, on a route already chosen."""
from __future__ import annotations

import pytest

import app as app_module
from app import app
from route_engine import (
    DEPARTURE_WINDOW_HOURS,
    MEANINGFUL_REDUCTION,
    SMALL_HOURS,
    departure_slots,
    plan_departures,
)

SEGMENTS = [
    {"cell": "a", "midpoint": [40.70, -73.99], "length_m": 1000.0, "expected_rate": 0.02},
    {"cell": "b", "midpoint": [40.71, -73.99], "length_m": 500.0, "expected_rate": 0.01},
]


def rates(by_hour):
    """A scorer whose rate depends only on the hour; the same in every cell."""

    def rate_at(lat, lon, hour, day):
        return {"expected_rate": by_hour.get(hour, 0.01), "cell_area_km2": 1.0}

    return rate_at


def plan(by_hour, hour=18, day=0, **kwargs):
    return plan_departures(SEGMENTS, hour, day, rates(by_hour), **kwargs)


class TestSlots:
    def test_window_is_centred_on_the_chosen_hour(self):
        slots = departure_slots(12, 2)
        assert [slot[0] for slot in slots] == list(
            range(-DEPARTURE_WINDOW_HOURS, DEPARTURE_WINDOW_HOURS + 1)
        )
        assert slots[DEPARTURE_WINDOW_HOURS] == (0, 12, 2)

    def test_crossing_midnight_moves_to_the_next_day(self):
        assert departure_slots(23, 6)[-1] == (3, 2, 0)  # Sunday 23:00 -> Monday 02:00

    def test_crossing_midnight_backwards_moves_to_the_previous_day(self):
        assert departure_slots(1, 0)[0] == (-3, 22, 6)  # Monday 01:00 -> Sunday 22:00


class TestRecommendation:
    def test_recommends_the_lowest_hour_when_the_gain_is_meaningful(self):
        result = plan({15: 0.02, 16: 0.02, 17: 0.02, 18: 0.02, 19: 0.02, 20: 0.01, 21: 0.02})
        assert result["verdict"] == "move"
        assert result["recommended"]["hour"] == 20
        assert result["reduction_percent"] == pytest.approx(50.0)
        assert [slot["hour"] for slot in result["slots"] if slot["is_recommended"]] == [20]

    def test_totals_add_the_way_route_scoring_does(self):
        result = plan({18: 0.02})
        chosen = next(slot for slot in result["slots"] if slot["is_chosen"])
        # 1.0 km and 0.5 km through cells 1 km wide, at a rate of 0.02.
        assert chosen["expected_crashes_total"] == pytest.approx(0.03)

    def test_suppressed_when_the_gain_is_below_the_threshold(self):
        just_under = 0.02 * (1 - MEANINGFUL_REDUCTION + 0.01)
        result = plan({h: 0.02 for h in range(24)} | {20: just_under})
        assert result["verdict"] == "little_difference"
        assert result["recommended"] is None
        assert not any(slot["is_recommended"] for slot in result["slots"])

    def test_the_chosen_hour_is_kept_when_it_is_already_lowest(self):
        result = plan({h: 0.05 for h in range(24)} | {18: 0.01})
        assert result["verdict"] == "little_difference"
        assert result["recommended"] is None

    def test_a_tie_prefers_the_smallest_change_of_plan(self):
        result = plan({h: 0.02 for h in range(24)} | {15: 0.01, 19: 0.01})
        assert result["recommended"]["hour"] == 19

    def test_a_trip_with_no_expected_crashes_has_no_recommendation(self):
        result = plan({h: 0.0 for h in range(24)})
        assert result["verdict"] == "little_difference"


class TestSmallHours:
    """Few crashes at 03:00 mostly means few cars: never advice to wait for it."""

    def test_never_recommends_the_small_hours(self):
        result = plan({h: 0.02 for h in range(24)} | {0: 0.001, 1: 0.001}, hour=22)
        assert result["recommended"] is None
        assert result["verdict"] == "only_small_hours"

    def test_small_hours_are_marked_on_their_slots(self):
        result = plan({}, hour=22)
        for slot in result["slots"]:
            assert slot["small_hours"] is (slot["hour"] in SMALL_HOURS)

    def test_a_better_daytime_hour_still_wins_over_a_lower_small_hour(self):
        result = plan({h: 0.02 for h in range(24)} | {21: 0.01, 0: 0.001}, hour=22)
        assert result["recommended"]["hour"] == 21
        assert result["verdict"] == "move"

    def test_a_driver_already_leaving_in_the_small_hours_can_be_pointed_later(self):
        result = plan({h: 0.02 for h in range(24)} | {6: 0.01}, hour=4)
        assert result["recommended"]["hour"] == 6


class TestWording:
    def test_crash_model_trips_speak_of_expected_crashes(self):
        result = plan({20: 0.001}, basis="expected_injury_crashes")
        assert "expected" in result["text"] and "crashes" in result["text"]

    def test_estimated_trips_do_not_claim_a_crash_count(self):
        result = plan({20: 0.001}, basis="relative_risk_estimate")
        assert "crashes" not in result["text"]
        assert "risk" in result["text"]


class TestScoringEconomy:
    def test_each_cell_is_scored_once_per_hour(self):
        calls = []

        def rate_at(lat, lon, hour, day):
            calls.append((lat, hour))
            return {"expected_rate": 0.01, "cell_area_km2": 1.0}

        repeat = SEGMENTS + [dict(SEGMENTS[0])]
        plan_departures(repeat, 12, 1, rate_at)
        assert len(calls) == 2 * (2 * DEPARTURE_WINDOW_HOURS + 1)

    def test_a_stretch_with_no_coverage_counts_for_nothing(self):
        segments = [dict(SEGMENTS[0], expected_rate=None), SEGMENTS[1]]

        def rate_at(lat, lon, hour, day):
            return {"expected_rate": None, "cell_area_km2": 1.0} if lat == 40.70 else {
                "expected_rate": 0.02,
                "cell_area_km2": 1.0,
            }

        result = plan_departures(segments, 12, 1, rate_at)
        assert result["slots"][0]["expected_crashes_total"] == pytest.approx(0.01)


def fake_routes(olat, olon, dlat, dlon):
    route = {
        "geometry": {"coordinates": [[-73.9855, 40.7580], [-73.9840, 40.7450], [-73.9800, 40.7300]]},
        "distance": 3200.0,
        "duration": 600.0,
    }
    return [route], "fake"


QUERY = "olat=40.7580&olon=-73.9855&dlat=40.7300&dlon=-73.9800"


class TestEndpoint:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(app_module, "get_routes", fake_routes)
        app_module.app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_the_chosen_slot_matches_the_route_card_exactly(self, client):
        route = client.get(f"/safe_route?{QUERY}&hour=18&day=2").get_json()
        body = client.get(f"/safe_route/departures?{QUERY}&hour=18&day=2").get_json()
        chosen = next(slot for slot in body["slots"] if slot["is_chosen"])
        assert chosen["expected_crashes_total"] == pytest.approx(
            route["routes"][0]["expected_crashes_total"], abs=2e-6
        )

    def test_the_curve_changes_with_the_hour(self, client):
        body = client.get(f"/safe_route/departures?{QUERY}&hour=14&day=5").get_json()
        assert body["available"] is True
        assert len({slot["expected_crashes_total"] for slot in body["slots"]}) > 1
        assert body["basis"] == "expected_injury_crashes"
        assert body["note"]

    def test_invalid_input_is_refused(self, client):
        assert client.get(f"/safe_route/departures?{QUERY}&hour=99").status_code == 400
        assert client.get("/safe_route/departures?olat=40.7").status_code == 400
        assert client.get(f"/safe_route/departures?{QUERY}&route=x").status_code == 400

    def test_a_route_index_past_the_end_is_refused(self, client):
        assert client.get(f"/safe_route/departures?{QUERY}&route=9").status_code == 400

    def test_a_trip_with_no_learned_model_says_so(self, client, monkeypatch):
        route = {
            "routes": [
                {
                    "segments": [
                        {"cell": "x", "midpoint": [48.8, 2.3], "length_m": 900.0,
                         "expected_rate": 0.01, "coverage": "context"}
                    ],
                    "data_quality": {},
                    "coverage": "context",
                }
            ],
            "coverage": "context",
        }
        monkeypatch.setattr(app_module, "_route_response", lambda: (route, None))
        body = client.get("/safe_route/departures?olat=48.8&olon=2.3&dlat=48.9&dlon=2.4&hour=12&day=1").get_json()
        assert body["available"] is False
        assert body["slots"] == []
        assert body["reason"]

    def test_an_estimated_trip_note_does_not_claim_a_crash_count(self, client, monkeypatch):
        body = dict(BASE_ROUTE_RELATIVE)
        monkeypatch.setattr(app_module, "_route_response", lambda: (body, None))
        monkeypatch.setattr(
            app_module, "score_point",
            lambda *args, **kwargs: {"expected_rate": 0.01, "cell_area_km2": 1.0},
        )
        answer = client.get("/safe_route/departures?olat=25.2&olon=55.3&dlat=25.3&dlon=55.4&hour=12&day=1").get_json()
        assert answer["basis"] == "relative_risk_estimate"
        assert "crashes" not in answer["note"]


BASE_ROUTE_RELATIVE = {
    "recommendation": {"basis": "relative_risk_estimate"},
    "routes": [
        {
            "segments": [
                {"cell": "x", "midpoint": [25.2, 55.3], "length_m": 900.0,
                 "expected_rate": 0.01, "coverage": "context", "method": "global_model"}
            ],
            "data_quality": {},
        }
    ],
}


class TestBusyHours:
    """A route that only falls as far as the whole city has shown nothing."""

    def city(self, by_hour):
        return lambda hour, day: by_hour.get(hour, 1.0)

    def test_a_drop_the_whole_city_shares_is_not_suggested(self):
        result = plan({h: 0.02 for h in range(24)} | {20: 0.01}, city_rate=self.city({20: 0.5}))
        assert result["verdict"] == "busy_hours_only"
        assert result["recommended"] is None
        assert "city" in result["text"]

    def test_a_drop_beyond_the_city_is_suggested_and_measured(self):
        result = plan({h: 0.02 for h in range(24)} | {20: 0.01}, city_rate=self.city({20: 0.8}))
        assert result["verdict"] == "move"
        assert result["reduction_percent"] == pytest.approx(50.0)
        assert result["adjusted_reduction_percent"] == pytest.approx(37.5)
        assert "beyond the citywide change" in result["text"]

    def test_each_slot_reports_the_city_change(self):
        result = plan({}, city_rate=self.city({20: 0.8}))
        slot = next(item for item in result["slots"] if item["hour"] == 20)
        assert slot["city_change_percent"] == pytest.approx(-20.0)


class TestAnchor:
    """After taking a suggestion, the window must not walk the trip later."""

    def walk(self):
        return {h: 0.02 for h in range(24)} | {19: 0.015, 20: 0.012, 21: 0.01, 22: 0.008, 23: 0.006}

    def test_without_an_anchor_the_window_would_move(self):
        assert plan(self.walk(), hour=21)["recommended"]["hour"] > 21

    def test_the_window_stays_on_the_original_plan(self):
        result = plan(self.walk(), hour=21, anchor=(18, 0))
        assert [slot["hour"] for slot in result["slots"]] == list(range(15, 22))
        assert result["verdict"] == "already_best"
        assert result["recommended"] is None
        assert "18:00" in result["text"]

    def test_a_better_hour_earlier_in_the_window_is_still_offered(self):
        rates_now = {h: 0.02 for h in range(24)} | {21: 0.015, 20: 0.01}
        assert plan(rates_now, hour=21, anchor=(18, 0))["recommended"]["hour"] == 20

    def test_an_anchor_out_of_reach_is_ignored(self):
        result = plan({}, hour=12, anchor=(18, 0))
        assert [slot["hour"] for slot in result["slots"]] == list(range(9, 16))

    def test_the_chosen_slot_is_marked_inside_the_window(self):
        result = plan({}, hour=20, anchor=(18, 0))
        assert [slot["hour"] for slot in result["slots"] if slot["is_chosen"]] == [20]
