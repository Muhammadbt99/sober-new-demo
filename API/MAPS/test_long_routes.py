"""Long trips: the fast geometry checks must answer exactly as the slow ones did."""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

import route_engine
from route_engine import (
    DETOUR_MAX_ROUTE_KM,
    RouteHints,
    VIA_BACKTRACK_M,
    VIA_OVERLAP_TOLERANCE_M,
    _local_xy,
    _cumulative_m,
    has_backtrack,
    haversine_m,
    plan_routes,
    resample_polyline,
    route_overlap,
    slim_long_trip,
)


def brute_overlap(candidate, other, tolerance_m=VIA_OVERLAP_TOLERANCE_M):
    """The original all-pairs check, kept here as the reference."""
    mine = resample_polyline(candidate, 60.0)
    theirs = resample_polyline(other, 40.0)
    a = _local_xy(mine, mine[0][0])
    b = _local_xy(theirs, mine[0][0])
    distances = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
    return float(np.mean(distances.min(axis=1) <= tolerance_m))


def brute_backtrack(points, tolerance_m=VIA_BACKTRACK_M, min_gap_m=400.0, run=4):
    """The original matrix check, kept here as the reference."""
    cumulative = _cumulative_m(points)
    length = cumulative[-1] if cumulative else 0.0
    if length < 2 * min_gap_m:
        return False
    spacing = max(50.0, length / 400.0)
    samples = resample_polyline(points, spacing)
    n = len(samples)
    if n < run + 1:
        return False
    xy = _local_xy(samples, samples[0][0])
    along = np.asarray([row[2] for row in samples])
    close = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1]) <= tolerance_m
    far = np.abs(along[:, None] - along[None, :]) >= max(min_gap_m, spacing * run)
    hit = close & far
    m = n - (run - 1)
    joined = np.ones((m, m), dtype=bool)
    for k in range(run):
        joined &= hit[k : m + k, (run - 1 - k) : (run - 1 - k) + m]
    return bool(joined.any())


def wander(seed, steps=300, step_deg=0.0006, start=(25.2, 55.3)):
    """A random drive: mostly straight, with turns, like a road."""
    rng = random.Random(seed)
    lat, lon = start
    heading = rng.random() * math.tau
    points = [(lat, lon)]
    for _ in range(steps):
        heading += rng.gauss(0, 0.25)
        lat += math.sin(heading) * step_deg
        lon += math.cos(heading) * step_deg
        points.append((lat, lon))
    return points


def with_spur(points, at, length=12):
    """Drive out along the route's own line and straight back along it."""
    spur = points[at - length : at][::-1]
    return points[:at] + spur + spur[::-1] + points[at:]


class TestRouteOverlap:
    @pytest.mark.parametrize("seed", range(8))
    def test_matches_the_all_pairs_answer(self, seed):
        a = wander(seed)
        b = a[:150] + wander(seed + 100, steps=150, start=a[150])
        assert route_overlap(a, b) == pytest.approx(brute_overlap(a, b), abs=1e-12)

    def test_a_route_fully_overlaps_itself(self):
        route = wander(1)
        assert route_overlap(route, route) == 1.0

    def test_far_apart_routes_do_not_overlap(self):
        assert route_overlap(wander(1), wander(1, start=(26.0, 56.0))) == 0.0


class TestBacktrack:
    @pytest.mark.parametrize("seed", range(12))
    def test_matches_the_matrix_answer_with_no_spur(self, seed):
        route = wander(seed)
        assert has_backtrack(route) == brute_backtrack(route)

    @pytest.mark.parametrize("seed", range(12))
    def test_matches_the_matrix_answer_with_a_spur(self, seed):
        route = with_spur(wander(seed), 150, length=20 + seed * 3)
        assert has_backtrack(route) == brute_backtrack(route)

    def test_a_spur_is_found(self):
        out = [(25.0 + index * 0.0005, 55.0) for index in range(40)]
        back = out[::-1]
        onward = [(25.0, 55.0 + index * 0.0005) for index in range(1, 40)]
        assert has_backtrack(out + back[1:] + onward) is True

    def test_a_straight_road_is_not_a_spur(self):
        assert has_backtrack([(25.0 + index * 0.0005, 55.0) for index in range(200)]) is False


class TestLongRouteCost:
    def long_route(self):
        """Dubai to Makkah's shape: 1,700 km with a vertex every 33 m."""
        count = 51_000
        return [
            (25.2 - 3.8 * index / count, 55.3 - 15.4 * index / count + 0.02 * math.sin(index / 700))
            for index in range(count)
        ]

    def test_overlap_and_backtrack_stay_cheap(self):
        import time

        route = self.long_route()
        other = [(lat + 0.0002, lon) for lat, lon in route]
        started = time.time()
        assert route_overlap(route, other) > 0.9
        assert has_backtrack(route) is False
        assert time.time() - started < 10.0


class TestWeekMeans:
    @pytest.fixture(scope="class")
    def region(self):
        import app

        if app.GLOBAL_MODEL is None or not app.GLOBAL_MODEL.available:
            pytest.skip("global crash model artifacts not present")
        return next(iter(app.GLOBAL_MODEL.regions.values()))

    def test_a_batch_equals_cell_by_cell(self, region):
        rows = [0, 17, 2048, 9000]
        batch = region.weeks(rows)
        for index, row in enumerate(rows):
            design = np.repeat(region.static[row : row + 1], 168, axis=0)
            rel_day = np.repeat(np.arange(7), 24).astype(np.float32)
            hour = np.tile(np.arange(24), 7).astype(np.float32)
            single = np.column_stack([design, hour, rel_day, (rel_day < 2).astype(np.float32)])
            expected = np.clip(region.model.predict(single), 1e-9, None).reshape(7, 24)
            np.testing.assert_allclose(batch[index], expected, rtol=1e-12)

    def test_the_mean_is_kept_and_reused(self, region):
        calls = []
        original = region.weeks
        region.weeks = lambda rows: calls.append(list(rows)) or original(rows)
        try:
            region._week_means.pop(33, None)
            first = region.week_mean(33)
            second = region.week_mean(33)
        finally:
            del region.weeks
        assert first == second
        assert calls == [[33]]

    def test_a_scored_route_prepares_its_cells_once(self):
        calls = []

        def score_at(lat, lon):
            return {"coverage": "context", "expected_rate": 0.01, "percentile": 50.0, "risk_score": 10.0,
                    "risk_level": "LOW", "cell_area_km2": 0.7}

        score_at.prepare = lambda points: calls.append(len(points))
        route_engine.score_geometry(
            [(25.0, 55.0), (25.0, 55.02)], lambda lat, lon: f"{lon:.3f}", score_at
        )
        assert len(calls) == 1 and calls[0] > 1


class TestRouteHints:
    """Speed, junctions and turns near a point: same answers, without the slow search."""

    def hints(self):
        road = wander(7, steps=400)
        raw = {
            "speeds_kmh": [30.0 + index % 50 for index in range(len(road) - 1)],
            "junctions": [road[index] for index in range(0, len(road), 9)],
        }
        return road, RouteHints(road, raw)

    def expected(self, hints, lat, lon):
        scale = math.cos(math.radians(lat))
        index = min(
            range(len(hints.points) - 1),
            key=lambda i: (hints.points[i][0] - lat) ** 2 + ((hints.points[i][1] - lon) * scale) ** 2,
        )
        near = sum(1 for jlat, jlon in hints.junctions if haversine_m(lat, lon, jlat, jlon) <= 250.0)
        return {"speed_kmh": hints.speeds[index], "junctions_per_km": near / 0.5}

    def test_matches_the_plain_search(self):
        road, hints = self.hints()
        rng = random.Random(3)
        for _ in range(200):
            lat, lon = rng.choice(road)
            lat, lon = lat + rng.gauss(0, 0.002), lon + rng.gauss(0, 0.002)
            assert hints.at(lat, lon) == pytest.approx(self.expected(hints, lat, lon))

    def test_thousands_of_stretches_on_a_long_route_stay_cheap(self):
        import time

        count = 50_000
        road = [(25.0 - 4 * i / count, 55.0 - 15 * i / count) for i in range(count)]
        raw = {"speeds_kmh": [90.0] * (count - 1), "junctions": road[::50]}
        hints = RouteHints(road, raw)
        started = time.time()
        for index in range(0, count, 20):
            hints.at(*road[index])
        assert time.time() - started < 5.0


class TestNoNudgedDetoursOnLongTrips:
    def plan(self, km):
        degrees = km / 111.2
        path = [(10.0 + degrees * index / 40, 40.0) for index in range(41)]
        raw = {
            "geometry": {"coordinates": [[lon, lat] for lat, lon in path]},
            "distance": km * 1000.0,
            "duration": km * 40.0,
        }

        def score_at(lat, lon):
            return {
                "coverage": "context",
                "expected_rate": 0.01,
                "percentile": 80.0,
                "risk_score": 50.0,
                "risk_level": "HIGH",
                "cell_area_km2": 0.7,
            }

        return plan_routes(
            lambda *args: ([raw], "fake"),
            path[0],
            path[-1],
            lambda lat, lon: (score_at, {}),
            lambda lat, lon: f"{round(lat, 2)}",
        )

    def test_a_long_trip_gets_no_nudged_copies_of_itself(self):
        plan = self.plan(DETOUR_MAX_ROUTE_KM + 50)
        assert plan.detour_synthesis["attempted"] is False
        assert len(plan.routes) == 1

    def test_a_shorter_trip_still_tries_a_detour(self):
        assert self.plan(40).detour_synthesis["attempted"] is True


class TestSlimLongTrip:
    def road(self, km, minutes, risk, level="LOW", **flags):
        return {
            "distance_km": km,
            "duration_min": minutes,
            "expected_crashes_total": risk,
            "rank": 9,
            "segments": [
                {"risk_level": level, "reasons": ["why"], "action": "x", "model_points": 1, "danger_types": {}, "length_m": 100.0}
            ],
            **flags,
        }

    def test_a_short_trip_is_untouched(self):
        routes = [self.road(50, 30, 1.0, is_fastest=True), self.road(60, 35, 1.1), self.road(70, 40, 0.9, is_safest=True)]
        assert slim_long_trip(routes) is routes
        assert "reasons" in routes[0]["segments"][0]

    def test_a_long_trip_keeps_the_pick_the_fastest_and_the_safest(self):
        routes = [
            self.road(1900, 1000, 0.8, is_safest=True),
            self.road(1950, 1010, 1.0),
            self.road(1880, 990, 1.1, is_fastest=True),
            self.road(2000, 1100, 1.2),
            self.road(2600, 1400, 1.3),
        ]
        kept = slim_long_trip(routes)
        assert len(kept) == 2 and kept[0]["is_safest"] and kept[1]["is_fastest"]
        assert [route["rank"] for route in kept] == [1, 2]

    def test_the_pick_survives_when_it_is_neither(self):
        routes = [
            self.road(1900, 1300, 0.5, is_safest=True),
            self.road(1950, 1020, 0.75),
            self.road(1880, 1000, 1.0, is_fastest=True),
        ]
        kept = slim_long_trip(routes)
        assert len(kept) == 3

    def test_calm_stretches_lose_their_notes_and_risky_ones_keep_them(self):
        calm = self.road(1900, 1000, 0.8, is_fastest=True, is_safest=True)
        risky = self.road(1900, 1000, 0.8, level="CRITICAL")
        slim_long_trip([calm, risky])
        assert "reasons" not in calm["segments"][0]
        assert calm["segments"][0]["risk_level"] == "LOW" and calm["segments"][0]["length_m"] == 100.0

    def test_a_risky_stretch_keeps_its_notes(self):
        road = self.road(1900, 1000, 0.8, level="CRITICAL", is_fastest=True, is_safest=True)
        slim_long_trip([road])
        assert road["segments"][0]["reasons"] == ["why"]

    def test_the_risk_totals_do_not_change(self):
        road = self.road(1900, 1000, 0.8, is_fastest=True, is_safest=True)
        assert slim_long_trip([road])[0]["expected_crashes_total"] == 0.8
