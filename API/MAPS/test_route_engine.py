"""Pure route-engine tests. No Flask, no network, no real model."""
from __future__ import annotations

import math

import pytest

from route_engine import (
    GeometryError,
    NoRoutesError,
    ProviderError,
    VIA_MAX_CANDIDATES,
    has_backtrack,
    haversine_m,
    plan_routes,
    route_overlap,
    score_geometry,
    synthesize_detours,
    via_candidates,
)


def constant_score(rate=0.01, percentile=80.0):
    def score_at(lat, lon):
        return {
            "cell": "cell",
            "coverage": "model",
            "expected_rate": rate,
            "expected_rate_per_km2_hour": rate / 0.737,
            "percentile": percentile,
            "risk_score": 50.0,
            "risk_level": "HIGH",
            "cell_area_km2": 0.737,
        }

    return score_at


def factory_from(score_at):
    def factory(lat, lon):
        return score_at, {"weather": "offline", "bars": "offline"}

    return factory


def line(start, end, steps=4):
    return [
        (
            start[0] + (end[0] - start[0]) * index / steps,
            start[1] + (end[1] - start[1]) * index / steps,
        )
        for index in range(steps + 1)
    ]


def route(coords, duration=1200.0):
    return {
        "geometry": {"coordinates": [[lon, lat] for lat, lon in coords]},
        "distance": 1000.0,
        "duration": duration,
    }


def cell_by_lat(lat, lon):
    return f"cell-{round(lat, 3)}"


def test_exposure_is_linear_in_route_length_for_a_constant_rate():
    scorer = constant_score(rate=0.01)
    short = score_geometry(
        line((40.70, -74.00), (40.79, -74.00)),
        cell_by_lat,
        scorer,
        duration_min=20.0,
    )
    long = score_geometry(
        line((40.70, -74.00), (40.88, -74.00)),
        cell_by_lat,
        scorer,
        duration_min=40.0,
    )
    assert short["expected_crashes_total"] > 0
    assert long["expected_crashes_total"] == pytest.approx(
        short["expected_crashes_total"] * 2, rel=1e-5
    )
    assert short["expected_crashes_per_10km"] == pytest.approx(
        long["expected_crashes_per_10km"], rel=1e-6
    )
    assert short["coverage"] == "model"


def test_segments_carry_cell_rate_and_bucket():
    result = score_geometry(
        line((40.70, -74.00), (40.79, -74.00)),
        cell_by_lat,
        constant_score(rate=0.02, percentile=92.0),
        duration_min=20.0,
    )
    assert result["segments"]
    for segment in result["segments"]:
        assert segment["cell"]
        assert segment["expected_rate"] == pytest.approx(0.02)
        assert segment["percentile"] == pytest.approx(92.0)
        assert segment["bucket"] == "extreme"
        assert segment["length_m"] > 0


def test_two_route_provider_picks_lower_exposure_as_safest():
    def provider(olat, olon, dlat, dlon):
        return [
            route(line((40.70, -74.00), (40.75, -74.00))),
            route(line((40.70, -73.99), (40.75, -73.99))),
        ], "fake"

    def score_at(lat, lon):
        rate = 0.02 if lon > -73.995 else 0.005
        return {
            "cell": cell_by_lat(lat, lon),
            "coverage": "model",
            "expected_rate": rate,
            "expected_rate_per_km2_hour": rate / 0.737,
            "percentile": 80.0,
            "risk_score": 50.0,
            "risk_level": "HIGH",
            "cell_area_km2": 0.737,
        }

    plan = plan_routes(provider, (40.70, -74.00), (40.75, -74.00), factory_from(score_at), cell_by_lat)
    assert plan.distinct_provider_routes == 2
    assert plan.detour_synthesis["attempted"] is False
    safest = plan.routes[0]
    assert safest["is_safest"] is True
    assert safest["route_type"] == "safest"
    assert plan.routes[1]["expected_crashes_total"] > safest["expected_crashes_total"]


def test_one_route_provider_synthesizes_and_scores_detours():
    def provider(olat, olon, dlat, dlon):
        return [route(line((40.70, -74.00), (40.75, -74.00)))], "fake"

    plan = plan_routes(
        provider,
        (40.70, -74.00),
        (40.75, -74.00),
        factory_from(constant_score()),
        cell_by_lat,
    )
    assert plan.detour_synthesis["attempted"] is True
    assert plan.detour_synthesis["candidate_count"] >= 1
    assert plan.detour_synthesis["scored_count"] >= 1
    assert any(route["synthetic"] for route in plan.routes)
    assert plan.routes[-1]["rank"] == len(plan.routes)


def test_detour_that_does_not_improve_is_not_reported_as_improvement():
    def provider(olat, olon, dlat, dlon):
        return [route(line((40.70, -74.00), (40.75, -74.00)))], "fake"

    plan = plan_routes(
        provider,
        (40.70, -74.00),
        (40.75, -74.00),
        factory_from(constant_score(rate=0.01)),
        cell_by_lat,
    )
    assert plan.detour_synthesis["attempted"] is True
    assert plan.detour_synthesis["improved"] is False
    # A longer synthetic route through the same rate must not beat the
    # original simply for existing.
    assert plan.routes[0]["synthetic"] is False


def test_provider_timeout_is_explicit():
    def provider(olat, olon, dlat, dlon):
        raise TimeoutError("provider timed out")

    with pytest.raises(ProviderError):
        plan_routes(
            provider,
            (40.70, -74.00),
            (40.75, -74.00),
            factory_from(constant_score()),
            cell_by_lat,
        )


def test_no_routes_is_explicit():
    def provider(olat, olon, dlat, dlon):
        return [], "fake"

    with pytest.raises(NoRoutesError):
        plan_routes(
            provider,
            (40.70, -74.00),
            (40.75, -74.00),
            factory_from(constant_score()),
            cell_by_lat,
        )


def test_empty_geometry_is_explicit():
    def provider(olat, olon, dlat, dlon):
        return [
            {"geometry": {"coordinates": []}, "distance": 0, "duration": 0}
        ], "fake"

    with pytest.raises(GeometryError):
        plan_routes(
            provider,
            (40.70, -74.00),
            (40.75, -74.00),
            factory_from(constant_score()),
            cell_by_lat,
        )


def test_uncovered_route_marks_rule_based_only():
    def score_at(lat, lon):
        return {
            "cell": "outside",
            "coverage": "rule_based_only",
            "expected_rate": None,
            "expected_rate_per_km2_hour": None,
            "percentile": None,
            "risk_score": 0,
            "risk_level": "LOW",
            "cell_area_km2": None,
        }

    result = score_geometry(
        line((40.70, -74.00), (40.75, -74.00)),
        cell_by_lat,
        score_at,
        duration_min=20.0,
    )
    assert result["coverage"] == "rule_based_only"
    assert result["expected_crashes_total"] == 0
    assert all(segment["bucket"] == "uncovered" for segment in result["segments"])


def test_synthesized_geometries_differ_from_the_original():
    points = line((40.70, -74.00), (40.75, -74.00), steps=20)
    detours = synthesize_detours(points, points[len(points) // 2])
    assert detours
    for detour in detours:
        assert len(detour) == len(points)
        assert any(
            abs(a[0] - b[0]) > 1e-7 or abs(a[1] - b[1]) > 1e-7
            for a, b in zip(points, detour)
        )


# ---------------------------------------------------------------------------
# Alternatives through waypoints ("via" routes)
# ---------------------------------------------------------------------------

VIA_ORIGIN = (40.70, -74.00)
VIA_DEST = (40.80, -74.00)  # about 11 km due north


def _fastest():
    return route(line(VIA_ORIGIN, VIA_DEST, steps=40), duration=1200.0)


def _plan_with_via(via_provider, **kwargs):
    def provider(olat, olon, dlat, dlon):
        return [_fastest()], "fake"

    return plan_routes(
        provider,
        VIA_ORIGIN,
        VIA_DEST,
        factory_from(constant_score()),
        cell_by_lat,
        via_provider=via_provider,
        **kwargs,
    )


def _through(origin, via, dest, duration=1500.0):
    return route(line(origin, via, 20) + line(via, dest, 20)[1:], duration=duration)


def test_via_candidates_flank_the_route_and_stay_apart():
    pts = line(VIA_ORIGIN, VIA_DEST, steps=20)
    vias = via_candidates(pts)
    assert 2 <= len(vias) <= VIA_MAX_CANDIDATES
    assert any(v[1] < -74.00 for v in vias), "no candidate west of the route"
    assert any(v[1] > -74.00 for v in vias), "no candidate east of the route"
    for index, a in enumerate(vias):
        for b in vias[index + 1 :]:
            assert haversine_m(a[0], a[1], b[0], b[1]) >= 350.0


def test_via_candidates_skip_trips_too_short_for_an_alternative():
    assert via_candidates(line((40.70, -74.00), (40.705, -74.00))) == []


def test_via_candidates_prefer_vetted_points_and_fall_back_when_none_pass():
    pts = line(VIA_ORIGIN, VIA_DEST, steps=20)
    east = via_candidates(pts, keep=lambda p: p[1] > -74.00)
    assert east and all(p[1] > -74.00 for p in east)
    assert len(via_candidates(pts, keep=lambda p: False)) >= 2


def test_has_backtrack_flags_a_spur_but_not_a_straight_road_or_a_crossing():
    assert has_backtrack(line(VIA_ORIGIN, VIA_DEST, steps=40)) is False
    out = line(VIA_ORIGIN, (40.72, -74.00), steps=20)
    spur = out + list(reversed(out))[1:] + line(VIA_ORIGIN, VIA_DEST, steps=40)[1:]
    assert has_backtrack(spur) is True
    crossing = [(40.70, -74.01), (40.75, -73.99), (40.70, -73.99), (40.75, -74.01)]
    assert has_backtrack(crossing) is False


def test_route_overlap_measures_shared_road():
    a = line(VIA_ORIGIN, VIA_DEST, steps=30)
    assert route_overlap(a, a) == pytest.approx(1.0)
    assert route_overlap(line((40.70, -73.95), (40.80, -73.95), steps=30), a) == 0.0
    half = line(VIA_ORIGIN, (40.75, -74.00), 15) + line((40.75, -74.00), (40.80, -73.95), 15)[1:]
    assert 0.3 < route_overlap(half, a) < 0.7


def test_via_alternatives_extend_a_single_provider_route():
    plan = _plan_with_via(lambda origin, via, dest: ([_through(origin, via, dest)], "fake"))
    info = plan.via_alternatives
    assert info["enabled"] is True and info["attempted"] is True
    assert info["added"] >= 2
    assert len(plan.routes) == 1 + info["added"]
    assert plan.distinct_provider_routes == 1
    added = [r for r in plan.routes if r.get("via")]
    assert len(added) == info["added"]
    assert all(r["provider"] == "fake+via" and r["synthetic"] is False for r in added)
    # Real alternatives make the offset-polyline fallback unnecessary.
    assert plan.detour_synthesis["attempted"] is False
    assert [r["rank"] for r in plan.routes] == list(range(1, len(plan.routes) + 1))


def test_via_alternatives_stop_at_the_target_count():
    plan = _plan_with_via(
        lambda origin, via, dest: ([_through(origin, via, dest)], "fake"), target_routes=2
    )
    assert len(plan.routes) == 2
    assert plan.via_alternatives["added"] == 1


def test_via_alternatives_reject_a_copy_of_an_existing_route():
    plan = _plan_with_via(lambda origin, via, dest: ([_fastest()], "fake"))
    info = plan.via_alternatives
    assert info["added"] == 0
    assert info["rejected"]["duplicate"] == info["returned"] > 0
    assert info["reason"] == "no_distinct_alternative"
    # With nothing real to add, the offset detours still run as before.
    assert plan.detour_synthesis["attempted"] is True


def test_via_alternatives_reject_a_route_that_drives_out_and_back():
    out = line(VIA_ORIGIN, (40.72, -74.00), steps=20)
    spur = out + list(reversed(out))[1:] + line(VIA_ORIGIN, VIA_DEST, steps=40)[1:]
    plan = _plan_with_via(lambda origin, via, dest: ([route(spur, 1500.0)], "fake"))
    assert plan.via_alternatives["added"] == 0
    assert plan.via_alternatives["rejected"]["backtrack"] > 0


def test_via_alternatives_reject_an_absurdly_long_detour():
    loop = line(VIA_ORIGIN, (40.95, -73.75), 30) + line((40.95, -73.75), VIA_DEST, 30)[1:]
    plan = _plan_with_via(lambda origin, via, dest: ([route(loop, 3000.0)], "fake"))
    assert plan.via_alternatives["added"] == 0
    assert plan.via_alternatives["rejected"]["too_long"] > 0


def test_a_failing_via_provider_never_breaks_the_comparison():
    def broken(origin, via, dest):
        raise RuntimeError("upstream down")

    plan = _plan_with_via(broken)
    assert plan.via_alternatives["added"] == 0
    assert plan.via_alternatives["reason"] == "provider_returned_nothing"
    assert len(plan.routes) >= 1


def test_without_a_via_provider_behaviour_is_unchanged():
    plan = _plan_with_via(None)
    assert plan.via_alternatives["enabled"] is False
    assert plan.via_alternatives["attempted"] is False
    assert plan.detour_synthesis["attempted"] is True
