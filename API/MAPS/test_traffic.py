"""Traffic: times for all routes or none, rerouting, and the key never leaks."""
from __future__ import annotations

import pytest
import requests

import app as app_module
import main
from app import app
from route_engine import choose_recommended, polyline_length_m
import traffic

KEY = "test-key-do-not-leak"
ROUTES = [
    {"geometry": [[25.0, 55.0], [25.1, 55.1]], "distance_km": 10.0},
    {"geometry": [[25.0, 55.0], [25.2, 55.1]], "distance_km": 20.0},
]


def summary(km, minutes=30.0):
    return {
        "lengthInMeters": km * 1000.0,
        "travelTimeInSeconds": minutes * 60.0,
        "noTrafficTravelTimeInSeconds": 1200.0,
        "historicTrafficTravelTimeInSeconds": 1500.0,
        "trafficDelayInSeconds": 120.0,
    }


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body

    def json(self):
        return self.body


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", KEY)
    traffic.reset()
    yield
    traffic.reset()


def by_length(points, depart):
    return summary(10.0 if points[-1][0] == 25.1 else 20.0)


class TestThin:
    def test_short_routes_are_untouched(self):
        assert traffic.thin([[1, 1], [2, 2]]) == [[1, 1], [2, 2]]

    def test_long_routes_keep_both_ends_and_the_limit(self):
        points = [[i, i] for i in range(5000)]
        kept = traffic.thin(points, 300)
        assert len(kept) == 300
        assert kept[0] == points[0] and kept[-1] == points[-1]


class TestRouteTimes:
    def test_every_route_is_timed(self, monkeypatch):
        monkeypatch.setattr(traffic, "_post", by_length)
        answer = traffic.route_times(ROUTES, date=None, hour=9, live=True)
        assert answer["available"] is True
        assert answer["mode"] == "live" and answer["depart"] == "now"
        assert answer["routes"][0] == {
            "route_index": 0,
            "traffic_min": 30.0,
            "no_traffic_min": 20.0,
            "typical_min": 25.0,
            "delay_min": 2.0,
        }

    def test_a_later_date_is_sent_as_local_time_and_called_typical(self, monkeypatch):
        seen = []
        monkeypatch.setattr(traffic, "_post", lambda p, d: seen.append(d) or by_length(p, d))
        answer = traffic.route_times(ROUTES, date="2026-11-09", hour=8, live=False)
        assert set(seen) == {"2026-11-09T08:00:00"}
        assert answer["mode"] == "typical"

    def test_one_failed_route_fails_the_whole_trip(self, monkeypatch):
        def flaky(points, depart):
            if points[-1][0] == 25.2:
                raise traffic.TrafficError("The traffic service could not time this route")
            return by_length(points, depart)

        monkeypatch.setattr(traffic, "_post", flaky)
        answer = traffic.route_times(ROUTES, date=None, hour=9, live=True)
        assert answer == {"available": False, "reason": "The traffic service could not time this route"}

    def test_a_different_road_is_refused(self, monkeypatch):
        monkeypatch.setattr(traffic, "_post", lambda p, d: summary(13.0))
        answer = traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)
        assert answer["available"] is False
        assert "different road" in answer["reason"]

    def test_a_small_distance_gap_is_fine(self, monkeypatch):
        monkeypatch.setattr(traffic, "_post", lambda p, d: summary(10.5))
        assert traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)["available"] is True

    def test_no_key_says_so_without_calling_out(self, monkeypatch):
        monkeypatch.delenv("TOMTOM_API_KEY")
        monkeypatch.setattr(traffic, "_post", lambda p, d: pytest.fail("called without a key"))
        assert traffic.route_times(ROUTES, date=None, hour=9, live=True)["available"] is False


class TestAllowance:
    def test_repeat_questions_use_the_cache(self, monkeypatch):
        calls = []
        monkeypatch.setattr(traffic, "_post", lambda p, d: calls.append(1) or by_length(p, d))
        traffic.route_times(ROUTES, date=None, hour=9, live=True)
        traffic.route_times(ROUTES, date=None, hour=9, live=True)
        assert len(calls) == 2

    def test_live_answers_expire(self, monkeypatch):
        calls = []
        monkeypatch.setattr(traffic, "_post", lambda p, d: calls.append(1) or by_length(p, d))
        traffic.route_times(ROUTES, date=None, hour=9, live=True)
        now = traffic.time.time()
        monkeypatch.setattr(traffic.time, "time", lambda: now + traffic.LIVE_TTL_S + 1)
        traffic.route_times(ROUTES, date=None, hour=9, live=True)
        assert len(calls) == 4

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refusal_pauses_further_calls(self, monkeypatch, status):
        calls = []

        def refused(*args, **kwargs):
            calls.append(1)
            return FakeResponse(status)

        monkeypatch.setattr(traffic.requests, "post", refused)
        assert traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)["available"] is False
        answer = traffic.route_times(ROUTES[:1], date=None, hour=10, live=True)
        assert len(calls) == 1
        assert "paused" in answer["reason"]


class TestRateLimit:
    """A 429 means "wait a second". It must not cost five minutes of traffic."""

    @pytest.fixture(autouse=True)
    def no_waiting(self, monkeypatch):
        self.waits = []
        monkeypatch.setattr(traffic.time, "sleep", self.waits.append)

    def test_it_waits_as_asked_then_succeeds(self, monkeypatch):
        answers = [FakeResponse(429), FakeResponse(200, {"routes": [{"summary": summary(10.0)}]})]
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: answers.pop(0))
        answer = traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)
        assert answer["available"] is True
        assert self.waits == [1.0]

    def test_it_never_pauses_traffic_even_when_retries_run_out(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: FakeResponse(429))
        first = traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)
        assert first["available"] is False and "busy" in first["reason"]
        assert len(self.waits) == traffic.RETRIES
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: FakeResponse(200, {"routes": [{"summary": summary(10.0)}]}))
        assert traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)["available"] is True

    def test_a_long_retry_after_is_capped(self, monkeypatch):
        response = FakeResponse(429)
        response.headers = {"Retry-After": "120"}
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: response)
        traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)
        assert max(self.waits) == traffic.MAX_WAIT_S

    def test_a_trip_never_sends_more_than_the_limit_at_once(self):
        assert traffic.WORKERS <= 3


class TestTheKeyNeverLeaks:
    def test_a_network_error_quoting_the_url_is_not_repeated(self, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError(f"failed: https://api.tomtom.com/x?key={KEY}")

        monkeypatch.setattr(traffic.requests, "post", boom)
        answer = traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)
        assert answer["available"] is False
        assert KEY not in str(answer)

    def test_the_exception_chain_is_cut_too(self, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.Timeout(f"key={KEY}")

        monkeypatch.setattr(traffic.requests, "post", boom)
        with pytest.raises(traffic.TrafficError) as caught:
            traffic._post([[1.0, 1.0], [2.0, 2.0]], "now")
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True

    def test_no_answer_holds_the_key(self, monkeypatch):
        monkeypatch.setattr(traffic, "_post", by_length)
        assert KEY not in str(traffic.route_times(ROUTES, date=None, hour=9, live=True))


def road(end_lat, end_lon, meters, seconds, **extra):
    """A route in the routers' shape, bent through its own middle."""
    mid = [(-73.9855 + end_lon) / 2 + 0.002, (40.7580 + end_lat) / 2]
    return {
        "geometry": {"coordinates": [[-73.9855, 40.7580], mid, [end_lon, end_lat]]},
        "distance": float(meters),
        "duration": float(seconds),
        **extra,
    }


USUAL = road(40.7300, -73.9800, 3200, 600)
TOMTOM = road(40.7300, -73.9800, 3300, 500, live_reroute=True)
TOMTOM["geometry"]["coordinates"][1] = [-73.9990, 40.7450]
TRIP = "olat=40.7580&olon=-73.9855&dlat=40.7300&dlon=-73.9800&hour=9&day=1"


def timed(minutes):
    """TomTom's timing, by which road it was handed."""

    def post(points, depart):
        west = any(lon < -73.99 for _, lon in points)
        return summary(polyline_length_m(points) / 1000.0, minutes[1] if west else minutes[0])

    return post


class TestTrafficInTheRouteAnswer:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(app_module, "get_routes", lambda *a: ([USUAL], "fake"))
        monkeypatch.setattr(app_module, "get_via_route", lambda *a: ([], "fake"))
        monkeypatch.setattr(traffic, "route_options", lambda *a: [])
        monkeypatch.setattr(traffic, "_post", timed((30.0, 30.0)))
        main.active_routes.clear()
        with app.test_client() as client:
            yield client
        main.active_routes.clear()

    def test_the_first_answer_already_holds_traffic_times(self, client):
        body = client.get(f"/safe_route?{TRIP}&live=1").get_json()
        assert body["routes"][0]["duration_min"] == 30.0
        assert body["routes"][0]["traffic_min_added"] == 10.0
        assert body["traffic"]["mode"] == "live" and "routes" not in body["traffic"]

    def test_a_date_gives_typical_traffic(self, client):
        assert client.get(f"/safe_route?{TRIP}&date=2026-11-10").get_json()["traffic"]["mode"] == "typical"

    def test_a_bad_date_is_refused(self, client):
        assert client.get(f"/safe_route?{TRIP}&date=soon").status_code == 400

    def test_a_plain_request_never_calls_the_traffic_service(self, client, monkeypatch):
        monkeypatch.setattr(traffic, "route_options", lambda *a: pytest.fail("called"))
        monkeypatch.setattr(traffic, "_post", lambda *a: pytest.fail("called"))
        body = client.get(f"/safe_route?{TRIP}").get_json()
        assert "traffic" not in body and body["routes"][0]["duration_min"] == 10.0

    def test_a_faster_road_in_traffic_joins_the_list_and_is_scored(self, client, monkeypatch):
        monkeypatch.setattr(traffic, "route_options", lambda *a: [TOMTOM])
        monkeypatch.setattr(traffic, "_post", timed((40.0, 20.0)))
        routes = client.get(f"/safe_route?{TRIP}&live=1").get_json()["routes"]
        found = next(route for route in routes if route.get("live_reroute"))
        assert found["is_fastest"] is True and found["duration_min"] == 20.0
        assert found["expected_crashes_total"] > 0

    def test_traffic_can_change_which_route_is_fastest_and_the_sentence_follows(self, client, monkeypatch):
        monkeypatch.setattr(traffic, "route_options", lambda *a: [TOMTOM])
        monkeypatch.setattr(traffic, "_post", timed((40.0, 20.0)))
        body = client.get(f"/safe_route?{TRIP}&live=1").get_json()
        usual = next(route for route in body["routes"] if not route.get("live_reroute"))
        assert usual["is_fastest"] is False
        assert [route["rank"] for route in body["routes"]] == [1, 2]

    def test_a_slow_small_gain_says_take_the_fastest(self, client, monkeypatch):
        monkeypatch.setattr(traffic, "route_options", lambda *a: [TOMTOM])
        monkeypatch.setattr(traffic, "_post", timed((60.0, 20.0)))
        body = client.get(f"/safe_route?{TRIP}&live=1").get_json()
        pick = body["routes"][body["recommended_route"]]
        assert pick["is_fastest"] is True
        assert "fastest" in body["recommendation"]["text"].lower()

    def test_tomtom_still_answers_when_the_routers_are_down(self, client, monkeypatch):
        def down(*args):
            raise TimeoutError("router down")

        monkeypatch.setattr(app_module, "get_routes", down)
        monkeypatch.setattr(traffic, "route_options", lambda *a: [TOMTOM])
        response = client.get(f"/safe_route?{TRIP}&live=1")
        assert response.status_code == 200
        assert any(route.get("live_reroute") for route in response.get_json()["routes"])

    def test_routes_survive_a_traffic_failure_and_it_is_not_kept(self, client, monkeypatch):
        def broken(*args):
            raise traffic.TrafficError("The traffic service could not be reached")

        monkeypatch.setattr(traffic, "_post", broken)
        body = client.get(f"/safe_route?{TRIP}&live=1").get_json()
        assert body["routes"][0]["duration_min"] == 10.0
        assert body["traffic"] == {"available": False, "reason": "The traffic service could not be reached"}
        monkeypatch.setattr(traffic, "_post", timed((30.0, 30.0)))
        assert client.get(f"/safe_route?{TRIP}&live=1").get_json()["traffic"]["available"] is True

    def test_live_answers_are_reused_briefly_then_refreshed(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(traffic, "route_options", lambda *a: calls.append(1) or [])
        client.get(f"/safe_route?{TRIP}&live=1")
        client.get(f"/safe_route?{TRIP}&live=1")
        assert len(calls) == 1
        for entry in main.active_routes.values():
            entry["last_updated"] -= app_module.LIVE_ROUTE_TTL_S + 1
        client.get(f"/safe_route?{TRIP}&live=1")
        assert len(calls) == 2

    def test_the_departure_chart_keeps_the_list_on_screen(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(traffic, "route_options", lambda *a: calls.append(1) or [])
        client.get(f"/safe_route?{TRIP}&live=1")
        for entry in main.active_routes.values():
            entry["last_updated"] -= app_module.LIVE_ROUTE_TTL_S + 1
        assert client.get(f"/safe_route/departures?{TRIP}&live=1").status_code == 200
        assert len(calls) == 1


class TestRouteOptions:
    ANSWER = {
        "routes": [
            {
                "summary": {"lengthInMeters": 1000, "travelTimeInSeconds": 120},
                "legs": [{"points": [{"latitude": 25.0, "longitude": 55.0}, {"latitude": 25.1, "longitude": 55.1}]}],
            }
        ]
    }

    def test_an_answer_becomes_a_router_shaped_road(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: FakeResponse(200, self.ANSWER))
        raw = traffic.route_options((25.0, 55.0), (25.1, 55.1), "now")[0]
        assert raw["geometry"]["coordinates"] == [[55.0, 25.0], [55.1, 25.1]]
        assert raw["distance"] == 1000.0 and raw["duration"] == 120.0 and raw["live_reroute"] is True

    def test_failure_means_no_options_and_no_leak(self, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError(f"key={KEY}")

        monkeypatch.setattr(traffic.requests, "get", boom)
        assert traffic.route_options((25.0, 55.0), (25.1, 55.1), "now") == []

    def test_a_refusal_pauses_both_kinds_of_call(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: FakeResponse(403))
        assert traffic.route_options((25.0, 55.0), (25.1, 55.1), "now") == []
        assert traffic.route_times(ROUTES[:1], date=None, hour=9, live=True)["available"] is False

    def test_without_a_key_nothing_is_asked(self, monkeypatch):
        monkeypatch.delenv("TOMTOM_API_KEY")
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: pytest.fail("called"))
        assert traffic.route_options((25.0, 55.0), (25.1, 55.1), "now") == []


def option(minutes, risk):
    return {"duration_min": minutes, "expected_crashes_total": risk}


class TestWhichRouteToRecommend:
    def test_a_tiny_risk_cut_is_not_worth_fourteen_minutes(self):
        assert choose_recommended([option(38, 0.413), option(24, 0.416)]) == 1

    def test_a_big_cut_for_a_few_minutes_is(self):
        assert choose_recommended([option(113, 0.92), option(109, 1.39)]) == 0

    def test_equal_times_go_to_the_lower_risk_either_way_round(self):
        assert choose_recommended([option(30, 0.95), option(30, 1.0)]) == 0
        assert choose_recommended([option(30, 1.0), option(30, 0.95)]) == 1

    def test_a_good_cut_for_a_minute_or_two_is_worth_it(self):
        # Times Square to Brooklyn: 9% less risk for 1.2 more minutes.
        assert choose_recommended([option(22.6, 0.167), option(21.4, 0.183)]) == 0

    def test_a_cut_too_small_to_matter_loses_even_for_a_minute(self):
        # Burj Khalifa to Marina: 1.5% less risk.
        assert choose_recommended([option(25.4, 0.458), option(24.9, 0.465)]) == 1

    def test_it_must_buy_enough_risk_per_minute(self):
        # 15% less risk for 20 more minutes: 0.75% a minute, under the 1% asked.
        assert choose_recommended([option(120, 0.85), option(100, 1.0)]) == 1

    def test_a_trip_far_longer_loses_however_much_risk_it_saves(self):
        # 45% less risk, but 42% longer.
        assert choose_recommended([option(146, 0.55), option(103, 1.0)]) == 1

    def test_the_lowest_risk_of_the_worthwhile_routes_wins(self):
        routes = [option(146, 0.55), option(125, 0.60), option(120, 0.70), option(103, 1.0)]
        assert choose_recommended(routes) == 1

    def test_when_the_fastest_is_safest_it_wins(self):
        assert choose_recommended([option(20, 0.5), option(30, 0.9)]) == 0

    def test_no_durations_keeps_the_first(self):
        assert choose_recommended([option(None, 0.5), option(None, 0.9)]) == 0


class TestGuidance:
    ANSWER = {
        "routes": [
            {
                "summary": {"lengthInMeters": 3200},
                "guidance": {
                    "instructions": [
                        {"routeOffsetInMeters": 0, "message": "Leave from A", "maneuver": "DEPART"},
                        {"routeOffsetInMeters": 900, "message": "Turn right onto Sheikh Zayed Road", "maneuver": "TURN_RIGHT", "street": "Sheikh Zayed Road"},
                        {"routeOffsetInMeters": 3200, "maneuver": "ARRIVE"},
                    ]
                },
            }
        ]
    }

    def test_steps_come_back_in_order_with_their_offsets(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: FakeResponse(200, self.ANSWER))
        answer = traffic.route_guidance(ROUTES[0], "now")
        assert answer["available"] is True and answer["length_m"] == 3200.0
        assert [step["offset_m"] for step in answer["instructions"]] == [0.0, 900.0, 3200.0]
        assert answer["instructions"][1]["street"] == "Sheikh Zayed Road"

    def test_a_step_without_words_still_reads_as_something(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: FakeResponse(200, self.ANSWER))
        assert traffic.route_guidance(ROUTES[0], "now")["instructions"][2]["message"] == "Continue"

    def test_it_follows_the_scored_road_and_asks_for_english(self, monkeypatch):
        seen = {}

        def post(url, params, json, timeout):
            seen.update(params=params, json=json)
            return FakeResponse(200, self.ANSWER)

        monkeypatch.setattr(traffic.requests, "post", post)
        traffic.route_guidance(ROUTES[0], "now")
        assert seen["params"]["language"] == "en-GB"
        assert len(seen["json"]["supportingPoints"]) == 2

    def test_asking_twice_costs_one_request(self, monkeypatch):
        calls = []
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: calls.append(1) or FakeResponse(200, self.ANSWER))
        traffic.route_guidance(ROUTES[0], "now")
        traffic.route_guidance(ROUTES[0], "now")
        assert len(calls) == 1

    def test_a_failure_says_so_without_the_key(self, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError(f"key={KEY}")

        monkeypatch.setattr(traffic.requests, "post", boom)
        answer = traffic.route_guidance(ROUTES[0], "now")
        assert answer["available"] is False and KEY not in str(answer)

    def test_no_key_says_so(self, monkeypatch):
        monkeypatch.delenv("TOMTOM_API_KEY")
        assert traffic.route_guidance(ROUTES[0], "now")["available"] is False

    def test_the_endpoint_checks_the_route_number(self, monkeypatch):
        monkeypatch.setattr(app_module, "get_routes", lambda *a: ([USUAL], "fake"))
        monkeypatch.setattr(app_module, "get_via_route", lambda *a: ([], "fake"))
        monkeypatch.setattr(traffic.requests, "post", lambda *a, **k: FakeResponse(200, self.ANSWER))
        main.active_routes.clear()
        with app.test_client() as client:
            assert client.get(f"/safe_route/guidance?{TRIP}&route=0").get_json()["available"] is True
            assert client.get(f"/safe_route/guidance?{TRIP}&route=9").status_code == 400
            assert client.get(f"/safe_route/guidance?{TRIP}&route=x").status_code == 400
        main.active_routes.clear()


class TestSatellite:
    class Tile(FakeResponse):
        content = b"jpeg-bytes"

    def test_a_tile_comes_through(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: self.Tile(200))
        with app.test_client() as client:
            response = client.get("/tiles/satellite/17/87000/56000.jpg")
        assert response.status_code == 200
        assert response.data == b"jpeg-bytes"
        assert response.mimetype == "image/jpeg"
        assert "max-age" in response.headers["Cache-Control"]

    def test_the_key_is_not_in_what_the_browser_gets(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: self.Tile(200))
        with app.test_client() as client:
            response = client.get("/tiles/satellite/17/87000/56000.jpg")
        assert KEY.encode() not in response.data
        assert KEY not in str(response.headers)

    def test_a_tile_is_fetched_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: calls.append(1) or self.Tile(200))
        traffic.satellite_tile(10, 5, 5)
        traffic.satellite_tile(10, 5, 5)
        assert len(calls) == 1

    def test_tiles_outside_the_world_are_refused_without_a_call(self, monkeypatch):
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: pytest.fail("called"))
        with app.test_client() as client:
            assert client.get("/tiles/satellite/3/8/0.jpg").status_code == 404
            assert client.get("/tiles/satellite/3/0/8.jpg").status_code == 404
            assert client.get("/tiles/satellite/20/0/0.jpg").status_code == 404

    def test_no_key_says_unavailable(self, monkeypatch):
        monkeypatch.delenv("TOMTOM_API_KEY")
        with app.test_client() as client:
            assert client.get("/tiles/satellite/3/1/1.jpg").status_code == 503

    def test_a_failure_does_not_leak_the_key(self, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError(f"key={KEY}")

        monkeypatch.setattr(traffic.requests, "get", boom)
        with app.test_client() as client:
            response = client.get("/tiles/satellite/3/1/1.jpg")
        assert response.status_code == 503 and KEY.encode() not in response.data

    def test_the_tile_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(traffic, "MAX_CACHED_TILES", 3)
        monkeypatch.setattr(traffic.requests, "get", lambda *a, **k: self.Tile(200))
        for x in range(10):
            traffic.satellite_tile(10, x, 1)
        assert len(traffic._tiles) <= 3


class TestAnIncompleteTripIsStillReused:
    @pytest.fixture
    def client(self, monkeypatch):
        calls = []
        monkeypatch.setattr(app_module, "get_routes", lambda *a: calls.append(1) or ([USUAL], "fake"))
        monkeypatch.setattr(app_module, "get_via_route", lambda *a: ([], "fake"))
        monkeypatch.setattr(traffic, "route_options", lambda *a: [])

        def broken(*args):
            raise traffic.TrafficError("down")

        monkeypatch.setattr(traffic, "_post", broken)
        main.active_routes.clear()
        with app.test_client() as client:
            client.calls = calls
            yield client
        main.active_routes.clear()

    def test_the_chart_does_not_redo_the_trip(self, client):
        client.get(f"/safe_route?{TRIP}&live=1")
        assert client.get(f"/safe_route/departures?{TRIP}&live=1").status_code == 200
        assert len(client.calls) == 1

    def test_asking_for_the_trip_again_does_try_again(self, client):
        client.get(f"/safe_route?{TRIP}&live=1")
        client.get(f"/safe_route?{TRIP}&live=1")
        assert len(client.calls) == 2
