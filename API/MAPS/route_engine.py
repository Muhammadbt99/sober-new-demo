"""Route exposure on top of the crash-risk model.

The model answers a cell-hour question. A route is a line through many cells, so
the route surface has to do three things explicitly:

1. Walk the geometry at a fixed spacing and group consecutive samples by H3 cell.
2. Convert each cell's expected crashes per cell-hour into a distance exposure by
   dividing by the cell's width. Crossing one cell width therefore consumes one
   cell-hour of exposure. This keeps the integration linear in route length for a
   constant rate, which is the property the route tests assert.
3. Report every segment with its cell, rate, percentile and colour bucket so the
   map can explain the total rather than just print it.

Nothing here calls the network. The provider, cell lookup and point scorer are
injected, which makes the planner testable with a fake provider and a fake model.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import numpy as np
from scipy.spatial import cKDTree

from crash_model import risk_band

Point = tuple[float, float]  # (lat, lon)
ScoreAt = Callable[[float, float], dict]
ScoreFactory = Callable[[float, float], tuple[ScoreAt, dict]]
CellFor = Callable[[float, float], str]
Provider = Callable[[float, float, float, float], tuple[list[dict], str]]
# (origin, via, destination) -> (provider routes, provider id). One road-snapped
# route that is forced through ``via``.
ViaProvider = Callable[[Point, Point, Point], tuple[list[dict], str]]

EARTH_RADIUS_M = 6_371_000.0
SAMPLE_SPACING_M = 100.0
DETOUR_OFFSETS_M = (350.0, -350.0, 700.0)
DETOUR_WINDOW_M = 900.0
SIMILARITY_SAMPLES = 5
SIMILARITY_THRESHOLD_M = 100.0
MIN_DETOUR_COVERAGE = 0.5

# Alternatives by waypoint. A provider's own "alternatives" are the few routes
# its algorithm happens to prefer, and in a dense street grid that is often one
# or two. Forcing a route through a point beside the fastest route yields a real,
# drivable alternative, and a point beside the riskiest stretch asks the question
# the app exists to answer: is there a way round it?
VIA_TARGET_ROUTES = 5          # stop looking once this many distinct routes exist
VIA_MAX_CANDIDATES = 8         # waypoints tried per request
VIA_MIN_ROUTE_M = 900.0        # shorter trips have no meaningful alternative
VIA_MIN_SEPARATION_M = 350.0   # two waypoints closer than this give the same route
VIA_MAX_DISTANCE_RATIO = 1.8   # reject an alternative this much longer than fastest
VIA_MAX_DURATION_RATIO = 2.0
VIA_OVERLAP_TOLERANCE_M = 70.0  # "on the same road" when closer than this
VIA_MAX_OVERLAP = 0.85         # must run on different roads for >= 15% of its length
VIA_BACKTRACK_M = 25.0         # a route that returns this close to itself doubled back
VIA_DEADLINE_S = 9.0
VIA_WORKERS = 4
# A detour is a nudge of DETOUR_OFFSETS_M. Past this length a nudge is the same
# trip scored again, not an alternative, so none are made.
DETOUR_MAX_ROUTE_KM = 300.0

# Past this length a trip keeps three routes and calm stretches lose their notes.
LONG_TRIP_KM = 600.0
SLIM_FIELDS = (
    "reasons",
    "action",
    "model_points",
    "nightlife_points",
    "danger_types",
)

# When a slower route is worth recommending over the fastest. Design choices, not
# findings: the lowest-risk road is no use if nobody would ever drive it.
RECOMMEND_MIN_CUT_PERCENT = 5.0
RECOMMEND_CUT_PER_EXTRA_MINUTE = 1.0
RECOMMEND_MAX_EXTRA_TIME = 0.30

# The departure planner. The crash model changes by the hour, so slots are whole
# hours: finer ones would be false precision.
DEPARTURE_WINDOW_HOURS = 3
# A design choice, shown to the user: smaller gains are not worth changing a plan.
MEANINGFUL_REDUCTION = 0.15
# The model counts crashes per place and hour, not per car. Before 06:00 (the
# danger model's small-hours bands) few crashes mostly means few cars, and that
# is when impaired and drowsy crashes peak, so these hours are never advised.
SMALL_HOURS = frozenset(range(0, 6))


class RoutePlanningError(Exception):
    """Base class for explicit route failures."""

    status = 502


class ProviderError(RoutePlanningError):
    status = 504


class NoRoutesError(RoutePlanningError):
    status = 502


class GeometryError(RoutePlanningError):
    status = 502


@dataclass
class RoutePlan:
    routes: list[dict]
    provider_source: str
    total_provider_routes: int
    distinct_provider_routes: int
    detour_synthesis: dict
    warnings: list[str]
    via_alternatives: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "routes": self.routes,
            "provider_source": self.provider_source,
            "total_provider_routes": self.total_provider_routes,
            "distinct_provider_routes": self.distinct_provider_routes,
            "detour_synthesis": self.detour_synthesis,
            "via_alternatives": self.via_alternatives,
            "warnings": self.warnings,
        }


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def polyline_length_m(points: Sequence[Point]) -> float:
    return sum(
        haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:])
    )


def _interpolate(a: Point, b: Point, fraction: float) -> Point:
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def resample_polyline(
    points: Sequence[Point], spacing_m: float = SAMPLE_SPACING_M
) -> list[tuple[float, float, float]]:
    """Return ``(lat, lon, cumulative_m)`` samples along a polyline."""
    if len(points) < 2:
        raise GeometryError("Route geometry needs at least two points")
    spacing = max(10.0, float(spacing_m))
    samples: list[tuple[float, float, float]] = [(points[0][0], points[0][1], 0.0)]
    cumulative = 0.0
    for start, end in zip(points, points[1:]):
        segment = haversine_m(start[0], start[1], end[0], end[1])
        if segment <= 0:
            continue
        steps = max(1, int(math.ceil(segment / spacing)))
        for index in range(1, steps + 1):
            fraction = index / steps
            lat, lon = _interpolate(start, end, fraction)
            samples.append((lat, lon, cumulative + segment * fraction))
        cumulative += segment
    if samples[-1][0] != points[-1][0] or samples[-1][1] != points[-1][1]:
        samples.append((points[-1][0], points[-1][1], cumulative))
    return samples


def percentile_bucket(percentile: float | None) -> str:
    if percentile is None:
        return "uncovered"
    if percentile < 50:
        return "low"
    if percentile < 75:
        return "medium"
    if percentile < 90:
        return "high"
    return "extreme"


def _cell_width_km(score: dict) -> float:
    area = score.get("cell_area_km2")
    if not area or area <= 0:
        return 0.86  # H3 res 8 average; only used when the cell area is missing.
    return math.sqrt(float(area))


def _weighted_average(values: Iterable[tuple[float, float]]) -> float:
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in values) / total_weight


def score_geometry(
    points: Sequence[Point],
    cell_for: CellFor,
    score_at: ScoreAt,
    *,
    duration_min: float | None = None,
    model_version: str | None = None,
    context: dict | None = None,
) -> dict:
    """Score a lat/lon polyline against the model surface."""
    points = [(float(lat), float(lon)) for lat, lon in points]
    if len(points) < 2:
        raise GeometryError("Route geometry needs at least two points")

    samples = resample_polyline(points)
    total_length_m = samples[-1][2]
    if total_length_m <= 0:
        raise GeometryError("Route geometry has zero length")

    # Group consecutive samples by cell. A route that leaves and re-enters a
    # cell produces two segments, because the map should draw both.
    runs: list[dict] = []
    current = None
    score_cache: dict[str, dict] = {}
    for start, end in zip(samples, samples[1:]):
        mid_lat = (start[0] + end[0]) / 2.0
        mid_lon = (start[1] + end[1]) / 2.0
        segment_m = haversine_m(start[0], start[1], end[0], end[1])
        if segment_m <= 0:
            continue
        cell = cell_for(mid_lat, mid_lon)
        if current is None or current["cell"] != cell:
            current = {
                "cell": cell,
                "start": [start[0], start[1]],
                "end": [end[0], end[1]],
                "length_m": 0.0,
                "mid_lat": mid_lat,
                "mid_lon": mid_lon,
            }
            runs.append(current)
        current["end"] = [end[0], end[1]]
        current["length_m"] += segment_m
        current.setdefault("pieces", []).append((mid_lat, mid_lon))

    for run in runs:
        pieces = run.pop("pieces", None)
        if pieces:
            run["mid_lat"], run["mid_lon"] = pieces[len(pieces) // 2]

    # A scorer may batch work for the whole route up front.
    prepare = getattr(score_at, "prepare", None)
    if prepare is not None:
        prepare([(run["mid_lat"], run["mid_lon"]) for run in runs])

    segments = []
    covered_length_m = 0.0
    risk_weighted: list[tuple[float, float]] = []
    percentile_weighted: list[tuple[float, float]] = []
    for run in runs:
        score = score_cache.get(run["cell"])
        if score is None:
            score = score_at(run["mid_lat"], run["mid_lon"])
            score_cache[run["cell"]] = score
        rate = score.get("expected_rate")
        percentile = score.get("percentile")
        covered = rate is not None
        if covered:
            covered_length_m += run["length_m"]
        width_km = _cell_width_km(score)
        expected = (
            float(rate) * (run["length_m"] / 1000.0) / width_km if covered else 0.0
        )
        segment = {
            "cell": run["cell"],
            "start": [round(run["start"][0], 6), round(run["start"][1], 6)],
            "end": [round(run["end"][0], 6), round(run["end"][1], 6)],
            "midpoint": [round(run["mid_lat"], 6), round(run["mid_lon"], 6)],
            "length_m": round(run["length_m"], 1),
            "expected_rate": rate,
            "expected_rate_per_km2_hour": score.get("expected_rate_per_km2_hour"),
            "expected_crashes": round(expected, 8),
            "percentile": percentile,
            "bucket": percentile_bucket(percentile),
            "risk_score": score.get("risk_score"),
            "risk_level": score.get("risk_level"),
            "model_points": score.get("model_points"),
            "nightlife_points": score.get("nightlife_points"),
            "reasons": score.get("reasons", []),
            "action": score.get("action"),
            "coverage": score.get("coverage"),
        }
        if score.get("danger_types") is not None:
            # What kind of crash is over-represented on this stretch now.
            segment["danger_types"] = score["danger_types"]
        method = (score.get("context") or {}).get("method")
        if method:
            # Which model scored a stretch outside NYC (the global crash model).
            segment["method"] = method
        segments.append(segment)
        if covered:
            risk_value = score.get("risk_score")
            if risk_value is not None:
                risk_weighted.append((float(risk_value), run["length_m"]))
            if percentile is not None:
                percentile_weighted.append((float(percentile), run["length_m"]))

    total_expected = sum(segment["expected_crashes"] for segment in segments)
    distance_km = total_length_m / 1000.0
    average_risk = round(_weighted_average(risk_weighted), 1)
    average_percentile = (
        round(_weighted_average(percentile_weighted), 1)
        if percentile_weighted
        else None
    )
    coverage_ratio = (
        covered_length_m / total_length_m if total_length_m > 0 else 0.0
    )
    # A scored stretch is either the crash model's or the global context
    # estimate's. A route scored only by the estimate is labelled as such.
    covered_by = {segment.get("coverage") for segment in segments if segment.get("expected_rate") is not None}
    if coverage_ratio >= 0.999 and covered_by == {"context"}:
        coverage = "context"
    elif coverage_ratio >= 0.999 and "context" not in covered_by:
        coverage = "model"
    elif coverage_ratio > 0:
        coverage = "mixed"
    else:
        coverage = "rule_based_only"

    waypoints = []
    if segments:
        stride = max(1, len(segments) // 12)
        for segment in segments[::stride]:
            waypoints.append(
                {
                    "lat": segment["midpoint"][0],
                    "lon": segment["midpoint"][1],
                    "cell": segment["cell"],
                    "risk_score": segment["risk_score"],
                    "risk_level": segment["risk_level"],
                    "expected_rate": segment["expected_rate"],
                    "percentile": segment["percentile"],
                    "bucket": segment["bucket"],
                }
            )

    highest = max(
        segments,
        key=lambda item: (
            item["percentile"] if item["percentile"] is not None else -1,
            item["expected_rate"] if item["expected_rate"] is not None else -1,
        ),
        default=None,
    )

    return {
        "expected_crashes_total": round(total_expected, 6),
        "expected_crashes_per_10km": round(
            total_expected / distance_km * 10.0, 6
        )
        if distance_km > 0
        else 0.0,
        "average_risk": average_risk,
        "risk_level": risk_band(average_risk),
        "average_percentile": average_percentile,
        "max_percentile": highest["percentile"] if highest else None,
        "distance_km": round(distance_km, 2),
        "duration_min": round(float(duration_min), 1) if duration_min else None,
        "segments": segments,
        "waypoints": waypoints,
        "geometry": [[round(lat, 6), round(lon, 6)] for lat, lon in points],
        "max_waypoint_risk": max(
            (segment["risk_score"] for segment in segments if segment["risk_score"] is not None),
            default=0,
        ),
        "coverage": coverage,
        "covered_length_m": round(covered_length_m, 1),
        "uncovered_length_m": round(total_length_m - covered_length_m, 1),
        "highest_risk_segment": highest,
        "model_version": model_version,
        "data_quality": {
            "model": "live" if coverage != "rule_based_only" else "unavailable",
            "coverage_ratio": round(coverage_ratio, 3),
        },
        **({"context": context} if context else {}),
    }


def _bearing(a: Point, b: Point) -> float:
    lat1 = math.radians(a[0])
    lat2 = math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y))


def _offset_point(point: Point, bearing_deg: float, offset_m: float) -> Point:
    lat, lon = point
    north = offset_m * math.cos(math.radians(bearing_deg))
    east = offset_m * math.sin(math.radians(bearing_deg))
    dlat = north / 111_320.0
    dlon = east / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return (lat + dlat, lon + dlon)


def _nearest_index(points: Sequence[Point], target: Point) -> int:
    return min(
        range(len(points)),
        key=lambda index: haversine_m(
            points[index][0], points[index][1], target[0], target[1]
        ),
    )


def _offset_detour(
    points: Sequence[Point], center: Point, offset_m: float, window_m: float = DETOUR_WINDOW_M
) -> list[Point]:
    center_index = _nearest_index(points, center)
    before = points[max(0, center_index - 1)]
    after = points[min(len(points) - 1, center_index + 1)]
    bearing = _bearing(before, after) + 90.0
    cumulative = [0.0]
    for start, end in zip(points, points[1:]):
        cumulative.append(
            cumulative[-1] + haversine_m(start[0], start[1], end[0], end[1])
        )
    center_distance = cumulative[center_index]
    candidate = []
    for point, distance in zip(points, cumulative):
        delta = distance - center_distance
        if abs(delta) >= window_m:
            candidate.append(point)
            continue
        weight = math.cos(0.5 * math.pi * delta / window_m)
        candidate.append(_offset_point(point, bearing, offset_m * weight))
    return candidate


def geometries_similar(
    geometry_a: Sequence[Point],
    geometry_b: Sequence[Point],
    threshold_m: float = SIMILARITY_THRESHOLD_M,
) -> bool:
    if not geometry_a or not geometry_b:
        return False
    count = min(SIMILARITY_SAMPLES, len(geometry_a), len(geometry_b))
    for index in range(count):
        lat_a, lon_a = geometry_a[
            min(len(geometry_a) - 1, round(index * (len(geometry_a) - 1) / max(1, count - 1)))
        ]
        lat_b, lon_b = geometry_b[
            min(len(geometry_b) - 1, round(index * (len(geometry_b) - 1) / max(1, count - 1)))
        ]
        if haversine_m(lat_a, lon_a, lat_b, lon_b) > threshold_m:
            return False
    return True


def synthesize_detours(
    points: Sequence[Point],
    center: Point,
    count: int = 3,
    offsets_m: Sequence[float] = DETOUR_OFFSETS_M,
) -> list[list[Point]]:
    candidates: list[list[Point]] = []
    for offset in list(offsets_m)[:count]:
        candidate = _offset_detour(points, center, offset)
        if len(candidate) < 2:
            continue
        if polyline_length_m(candidate) <= 0:
            continue
        if geometries_similar(points, candidate):
            continue
        if any(geometries_similar(candidate, other) for other in candidates):
            continue
        candidates.append(candidate)
    return candidates


def _cumulative_m(points: Sequence[Point]) -> list[float]:
    cumulative = [0.0]
    for start, end in zip(points, points[1:]):
        cumulative.append(
            cumulative[-1] + haversine_m(start[0], start[1], end[0], end[1])
        )
    return cumulative


def _local_xy(samples: Sequence[Sequence[float]], lat0: float) -> np.ndarray:
    """Metres east/north of the origin: flat-earth maths, fine at city scale."""
    arr = np.asarray([(row[0], row[1]) for row in samples], dtype=float)
    scale = math.cos(math.radians(lat0))
    x = np.radians(arr[:, 1]) * EARTH_RADIUS_M * scale
    y = np.radians(arr[:, 0]) * EARTH_RADIUS_M
    return np.column_stack([x, y])


def via_candidates(
    points: Sequence[Point],
    anchor: Point | None = None,
    count: int = VIA_MAX_CANDIDATES,
    min_separation_m: float = VIA_MIN_SEPARATION_M,
    keep: Callable[[Point], bool] | None = None,
) -> list[Point]:
    """Waypoints beside a route, each of which should pull in a different road.

    Candidates sit to either side of the route at a distance proportional to its
    length (a long trip needs a wider berth than a short one). When the riskiest
    stretch is known its two flanks come first, so the first alternatives tried
    are the ones that route around it.

    ``keep`` vets a waypoint. A waypoint in a river or on a pier snaps to a
    dead-end road, and the route then drives out and back; callers pass a test
    for "over land the model knows". If too few waypoints pass it the unvetted
    ones fill the gap, so open country still gets alternatives.
    """
    if len(points) < 2:
        return []
    cumulative = _cumulative_m(points)
    length = cumulative[-1]
    if length < VIA_MIN_ROUTE_M:
        return []
    base = min(2200.0, max(450.0, 0.18 * length))

    def beside(index: int, offset_m: float) -> Point:
        before = points[max(0, index - 1)]
        after = points[min(len(points) - 1, index + 1)]
        return _offset_point(points[index], _bearing(before, after) + 90.0, offset_m)

    def index_at(fraction: float) -> int:
        target = length * fraction
        return min(range(len(points)), key=lambda i: abs(cumulative[i] - target))

    raw: list[Point] = []
    if anchor is not None:
        anchor_index = _nearest_index(points, anchor)
        raw += [beside(anchor_index, 0.85 * base), beside(anchor_index, -0.85 * base)]
    for fraction in (0.5, 0.33, 0.67):
        index = index_at(fraction)
        raw += [beside(index, base), beside(index, -base)]
    # A closer ring: a couple of avenues over rather than a different side of town.
    middle = index_at(0.5)
    raw += [beside(middle, 0.5 * base), beside(middle, -0.5 * base)]

    def pick(vetted: bool, chosen: list[Point]) -> list[Point]:
        for candidate in raw:
            if len(chosen) >= count:
                break
            if vetted and keep is not None and not keep(candidate):
                continue
            if all(
                haversine_m(candidate[0], candidate[1], other[0], other[1]) >= min_separation_m
                for other in chosen
            ):
                chosen.append(candidate)
        return chosen

    chosen = pick(True, [])
    if keep is not None and len(chosen) < min(count, 3):
        chosen = pick(False, chosen)
    return chosen


def route_overlap(
    candidate: Sequence[Point],
    other: Sequence[Point],
    tolerance_m: float = VIA_OVERLAP_TOLERANCE_M,
) -> float:
    """Share of ``candidate`` (0..1) that runs within ``tolerance_m`` of ``other``."""
    mine = resample_polyline(candidate, 60.0)
    theirs = resample_polyline(other, 40.0)
    lat0 = mine[0][0]
    a = _local_xy(mine, lat0)
    b = _local_xy(theirs, lat0)
    # A tree, not a distance matrix: Dubai to Makkah is 30,000 x 40,000 samples.
    # The bound is exclusive, so step just past it to keep "<= tolerance".
    nearest, _ = cKDTree(b).query(a, distance_upper_bound=np.nextafter(tolerance_m, np.inf))
    return float(np.mean(np.isfinite(nearest)))


def has_backtrack(
    points: Sequence[Point],
    tolerance_m: float = VIA_BACKTRACK_M,
    min_gap_m: float = 400.0,
    run: int = 4,
) -> bool:
    """True when the route drives out along a road and straight back along it.

    That spur is what a waypoint snapped to a dead-end street produces. A route
    that merely crosses itself (an overpass) touches for one or two samples; a
    spur runs side by side for ``run`` samples in opposite directions.
    """
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
    # Only pairs that touch are kept: an n x n matrix needs gigabytes on a long
    # trip, because every vertex of the line survives resampling.
    pairs = cKDTree(xy).query_pairs(tolerance_m, output_type="ndarray")
    if len(pairs) == 0:
        return False
    first, second = pairs[:, 0], pairs[:, 1]
    far = np.abs(along[first] - along[second]) >= max(min_gap_m, spacing * run)
    first, second = first[far], second[far]
    # Each (i, j) as one number, both ways round. Walking i up and j down, the
    # (i + k, j - k) of a spur differ from it by k * (n - 1).
    hit = np.concatenate([first * n + second, second * n + first])
    start = hit[(hit // n + run - 1 < n) & (hit % n >= run - 1)]
    for k in range(1, run):
        start = start[np.isin(start + k * (n - 1), hit, assume_unique=False)]
    return bool(len(start))


def _haversine_to(lat: float, lon: float, points: np.ndarray) -> np.ndarray:
    """``haversine_m`` from one point to an array of ``(lat, lon)`` rows."""
    phi1 = math.radians(lat)
    phi2 = np.radians(points[:, 0])
    half_lat = (phi2 - phi1) / 2.0
    half_lon = np.radians(points[:, 1] - lon) / 2.0
    a = np.sin(half_lat) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(half_lon) ** 2
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _without_position(maneuver: dict) -> dict:
    return {key: value for key, value in maneuver.items() if key not in ("lat", "lon")}


class RouteHints:
    """What the router knows about the route being scored, by position.

    The model-free scorer (``context_model``) uses the routed speed, junction
    density and manoeuvres where no crash model covers the route. They are
    summarised per map cell, the unit a stretch is scored in, so the score of a
    stretch reflects all of it. Scorers read the hints through
    ``current_route_hint`` so their signature stays ``(lat, lon)``.
    """

    #: How near a manoeuvre must be to count when the cell summary is missing.
    NEAR_M = 150.0

    def __init__(self, points: Sequence[Point], raw: dict, cell_for: CellFor | None = None):
        self.points = list(points)
        speeds = raw.get("speeds_kmh") or []
        self.speeds = speeds if len(speeds) == len(self.points) - 1 else None
        self.junctions = raw.get("junctions") or []
        self.maneuvers = [m for m in raw.get("maneuvers") or [] if "lat" in m and "lon" in m]
        self.cell_for = cell_for
        self.cells = self._per_cell() if cell_for is not None else {}
        # Arrays, because `_near` runs once per stretch: searching Python lists
        # there made scoring grow with the square of the trip's length.
        self._road = np.asarray(self.points[:-1], dtype=float).reshape(-1, 2)
        self._junctions = np.asarray(self.junctions, dtype=float).reshape(-1, 2)

    def _per_cell(self) -> dict:
        cells: dict = {}

        def entry(cell):
            return cells.setdefault(
                cell, {"length_m": 0.0, "speed_m": 0.0, "speed_len": 0.0, "junctions": 0, "maneuvers": []}
            )

        for index in range(len(self.points) - 1):
            (lat1, lon1), (lat2, lon2) = self.points[index], self.points[index + 1]
            length = haversine_m(lat1, lon1, lat2, lon2)
            if length <= 0:
                continue
            item = entry(self.cell_for((lat1 + lat2) / 2.0, (lon1 + lon2) / 2.0))
            item["length_m"] += length
            speed = self.speeds[index] if self.speeds else None
            if speed:
                item["speed_m"] += float(speed) * length
                item["speed_len"] += length
        for jlat, jlon in self.junctions:
            item = cells.get(self.cell_for(jlat, jlon))
            if item is not None:
                item["junctions"] += 1
        for maneuver in self.maneuvers:
            entry(self.cell_for(maneuver["lat"], maneuver["lon"]))["maneuvers"].append(
                _without_position(maneuver)
            )
        return cells

    def _near(self, lat: float, lon: float) -> dict:
        hint: dict = {}
        if self.speeds:
            scale = math.cos(math.radians(lat))
            squared = (self._road[:, 0] - lat) ** 2 + ((self._road[:, 1] - lon) * scale) ** 2
            speed = self.speeds[int(np.argmin(squared))]
            if speed:
                hint["speed_kmh"] = float(speed)
        if self.junctions:
            near = int(np.count_nonzero(_haversine_to(lat, lon, self._junctions) <= 250.0))
            hint["junctions_per_km"] = near / 0.5  # junctions on ~500 m of road around the point
        return hint

    def at(self, lat: float, lon: float) -> dict:
        hint = self._near(lat, lon)
        item = self.cells.get(self.cell_for(lat, lon)) if self.cells else None
        if item is not None:
            if item["speed_len"] > 0:
                hint["speed_kmh"] = round(item["speed_m"] / item["speed_len"], 1)
            if self.junctions and item["length_m"] >= 150.0:
                hint["junctions_per_km"] = item["junctions"] / (item["length_m"] / 1000.0)
            if item["maneuvers"]:
                hint["maneuvers"] = item["maneuvers"]
        elif self.maneuvers:
            near = [
                _without_position(m)
                for m in self.maneuvers
                if haversine_m(lat, lon, m["lat"], m["lon"]) <= self.NEAR_M
            ]
            if near:
                hint["maneuvers"] = near
        return hint


_ROUTE_HINTS: ContextVar["RouteHints | None"] = ContextVar("route_hints", default=None)


def current_route_hint(lat: float, lon: float) -> dict | None:
    """Router knowledge at a point of the route being scored, or None."""
    hints = _ROUTE_HINTS.get()
    return hints.at(lat, lon) if hints is not None else None


@contextmanager
def _route_hints(points: Sequence[Point], raw: dict, cell_for: CellFor | None = None):
    useful = raw.get("speeds_kmh") or raw.get("junctions") or raw.get("maneuvers")
    token = _ROUTE_HINTS.set(RouteHints(points, raw, cell_for) if useful else None)
    try:
        yield
    finally:
        _ROUTE_HINTS.reset(token)


def annotate_segments(exposure: dict, raw: dict | None) -> None:
    """Give every segment its place along the route and, when known, its street.

    A segment is the stretch of route inside one H3 cell. "Segment 7" means
    nothing to a driver; "Victoria Embankment, km 2.4-3.1" does. Street names
    come from the router's steps, matched by distance along the route.
    """
    segments = exposure.get("segments") or []
    steps = [step for step in ((raw or {}).get("steps") or []) if step.get("distance")]
    total_m = sum(segment["length_m"] for segment in segments)
    step_total = sum(step["distance"] for step in steps)
    ends: list[float] = []
    names: list[str] = []
    if steps and step_total > 0:
        scale = total_m / step_total
        reached = 0.0
        for step in steps:
            reached += step["distance"] * scale
            ends.append(reached)
            names.append(step.get("name") or "")
    position = 0.0
    for segment in segments:
        start = position
        position += segment["length_m"]
        segment["from_km"] = round(start / 1000.0, 2)
        segment["to_km"] = round(position / 1000.0, 2)
        if not ends:
            continue
        middle = (start + position) / 2.0
        index = next((i for i, end in enumerate(ends) if middle <= end), len(ends) - 1)
        street = None
        # Roundabouts and slip roads are often unnamed; borrow the nearest name.
        for offset in (0, -1, 1, -2, 2):
            candidate = index + offset
            if 0 <= candidate < len(names) and names[candidate]:
                street = names[candidate]
                break
        segment["street"] = street


def _score_raw_route(
    raw: dict,
    score_factory: ScoreFactory,
    cell_for: CellFor,
    model_version: str | None,
) -> tuple[dict | None, str | None]:
    """Score one provider route. Returns ``(route, None)`` or ``(None, warning)``."""
    geometry = (raw.get("geometry") or {}).get("coordinates") or []
    if len(geometry) < 2:
        return None, "A provider route had empty geometry and was dropped"
    points = [(float(lat), float(lon)) for lon, lat in geometry]
    midpoint = points[len(points) // 2]
    score_at, context = score_factory(midpoint[0], midpoint[1])
    try:
        with _route_hints(points, raw, cell_for):
            exposure = score_geometry(
                points,
                cell_for,
                score_at,
                duration_min=(raw.get("duration") or 0) / 60.0 or None,
                model_version=model_version,
                context=context,
            )
    except GeometryError as exc:
        return None, f"A provider route could not be scored: {exc}"
    annotate_segments(exposure, raw)
    exposure["provider_distance_km"] = round((raw.get("distance") or 0) / 1000.0, 2)
    return exposure, None


def _fetch_via_routes(
    via_provider: ViaProvider,
    origin: Point,
    destination: Point,
    vias: Sequence[Point],
    deadline_s: float,
    workers: int = VIA_WORKERS,
) -> list[tuple[Point, dict, str]]:
    """Ask for every via route at once. Slow or failing requests are dropped."""

    def call(via: Point):
        try:
            raws, source = via_provider(origin, via, destination)
        except Exception:  # noqa: BLE001 - an alternative is optional, never fatal
            return None
        return (raws[0], source) if raws else None

    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(vias))))
    futures = [pool.submit(call, via) for via in vias]
    done, _ = wait(futures, timeout=deadline_s)
    pool.shutdown(wait=False, cancel_futures=True)
    fetched: list[tuple[Point, dict, str]] = []
    for via, future in zip(vias, futures):
        if future not in done:
            continue
        result = future.result()
        if result is not None:
            fetched.append((via, result[0], result[1]))
    return fetched


def slim_long_trip(routes: list[dict]) -> list[dict]:
    """Keep a very long trip light enough for a browser to draw.

    Five 2,000 km routes came to 12.6 MB and about 15,000 stretches, which held
    the page for over a minute to offer roads that barely differ. Past
    ``LONG_TRIP_KM`` only the pick, the fastest and the safest stay, and calm
    stretches lose the notes nobody is shown. Numbers are untouched.
    """
    if not routes or max(route["distance_km"] for route in routes) <= LONG_TRIP_KM:
        return routes
    pick = routes[choose_recommended(routes)]
    kept = [route for route in routes if route is pick or route.get("is_fastest") or route.get("is_safest")]
    for route in kept:
        for segment in route.get("segments", []):
            if segment.get("risk_level") not in ("HIGH", "CRITICAL"):
                for field in SLIM_FIELDS:
                    segment.pop(field, None)
    for rank, route in enumerate(kept, start=1):
        route["rank"] = rank
    return kept


def choose_recommended(routes: Sequence[dict]) -> int:
    """The index of the route worth driving: the fastest, unless a slower one earns it.

    A slower route must cut risk by enough, buy enough risk per extra minute, and
    not be far longer. Of those that do, the lowest risk wins.
    """
    timed = [route for route in routes if route.get("duration_min")]
    if not timed:
        return 0
    # Equal times: the lower risk is the better fastest route.
    fastest = min(timed, key=lambda route: (route["duration_min"], route["expected_crashes_total"]))
    base = fastest["expected_crashes_total"]
    if base <= 0:
        return list(routes).index(fastest)

    def worth_it(route: dict) -> bool:
        cut = (base - route["expected_crashes_total"]) / base * 100.0
        extra = route["duration_min"] - fastest["duration_min"]
        return (
            cut >= RECOMMEND_MIN_CUT_PERCENT
            and cut >= extra * RECOMMEND_CUT_PER_EXTRA_MINUTE
            and extra <= fastest["duration_min"] * RECOMMEND_MAX_EXTRA_TIME
        )

    choices = [route for route in timed if route is not fastest and worth_it(route)]
    chosen = min(choices, key=lambda route: route["expected_crashes_total"]) if choices else fastest
    return list(routes).index(chosen)


def tag_routes(routes: list[dict]) -> None:
    """Name the safest, fastest and balanced routes, and sort by risk.

    Run again whenever durations change: traffic can make another route fastest.
    """
    if not routes:
        return
    max_exposure = max(route["expected_crashes_total"] for route in routes)
    max_duration = max(route["duration_min"] or 0 for route in routes) or 1.0
    for route in routes:
        exposure_part = (
            route["expected_crashes_total"] / max_exposure if max_exposure > 0 else 0.0
        )
        time_part = (route["duration_min"] or 0) / max_duration
        route["_composite"] = exposure_part * 0.6 + time_part * 0.4

    safest = min(routes, key=lambda route: route["expected_crashes_total"])
    fastest = min(routes, key=lambda route: route["duration_min"] or float("inf"))
    balanced = min(routes, key=lambda route: route["_composite"])
    for route in routes:
        route["is_safest"] = route is safest
        route["is_fastest"] = route is fastest
        route["is_balanced"] = route is balanced
        if route.get("synthetic"):
            route["route_type"] = "detour"
        elif route["is_safest"]:
            route["route_type"] = "safest"
        elif route["is_fastest"]:
            route["route_type"] = "fastest"
        elif route["is_balanced"]:
            route["route_type"] = "balanced"
        else:
            route["route_type"] = "alternative"
        route.pop("_composite", None)

    routes.sort(key=lambda route: (route["expected_crashes_total"], route["duration_min"] or 0))
    for index, route in enumerate(routes, start=1):
        route["rank"] = index


def plan_routes(
    provider: Provider,
    origin: Point,
    destination: Point,
    score_factory: ScoreFactory,
    cell_for: CellFor,
    *,
    model_version: str | None = None,
    max_detours: int = 3,
    via_provider: ViaProvider | None = None,
    target_routes: int = VIA_TARGET_ROUTES,
    max_via_candidates: int = VIA_MAX_CANDIDATES,
    via_deadline_s: float = VIA_DEADLINE_S,
) -> RoutePlan:
    """Fetch, score and rank routes. Raises an explicit error type on failure.

    With a ``via_provider`` the planner also asks for routes forced through
    waypoints beside the fastest route, so the comparison is not limited to the
    one or two routes a provider volunteers.
    """
    try:
        raw_routes, provider_source = provider(
            origin[0], origin[1], destination[0], destination[1]
        )
    except Exception as exc:  # noqa: BLE001 - provider failures must be explicit
        raise ProviderError(f"Routing provider failed: {type(exc).__name__}") from exc

    if not raw_routes:
        raise NoRoutesError("Routing provider returned no routes")

    scored: list[dict] = []
    warnings: list[str] = []
    for raw in raw_routes:
        geometry = (raw.get("geometry") or {}).get("coordinates") or []
        if len(geometry) < 2:
            warnings.append("A provider route had empty geometry and was dropped")
            continue
        points = [(float(lat), float(lon)) for lon, lat in geometry]
        midpoint = points[len(points) // 2]
        score_at, context = score_factory(midpoint[0], midpoint[1])
        try:
            with _route_hints(points, raw, cell_for):
                exposure = score_geometry(
                    points,
                    cell_for,
                    score_at,
                    duration_min=(raw.get("duration") or 0) / 60.0 or None,
                    model_version=model_version,
                    context=context,
                )
        except GeometryError as exc:
            warnings.append(f"A provider route could not be scored: {exc}")
            continue
        annotate_segments(exposure, raw)
        scored.append(
            {
                **exposure,
                "synthetic": False,
                "provider": provider_source,
                "provider_distance_km": round((raw.get("distance") or 0) / 1000.0, 2),
                **({"live_reroute": True} if raw.get("live_reroute") else {}),
            }
        )

    if not scored:
        raise GeometryError("No provider route had usable geometry")

    distinct: list[dict] = []
    for route in scored:
        if any(
            geometries_similar(route["geometry"], other["geometry"])
            for other in distinct
        ):
            warnings.append("A duplicate provider geometry was dropped")
            continue
        distinct.append(route)
    distinct_count = len(distinct)

    detour_synthesis = {
        "attempted": False,
        "reason": None,
        "candidate_count": 0,
        "scored_count": 0,
        "improved": False,
        "fastest_expected_crashes": None,
        "best_detour_expected_crashes": None,
    }

    via_alternatives: dict = {
        "enabled": via_provider is not None,
        "attempted": False,
        "reason": None,
        "candidate_count": 0,
        "returned": 0,
        "added": 0,
        "rejected": {"duplicate": 0, "too_long": 0, "backtrack": 0, "unscorable": 0},
    }
    if via_provider is not None and len(distinct) < target_routes:
        reference = min(
            distinct,
            key=lambda route: route["duration_min"]
            if route["duration_min"] is not None
            else float("inf"),
        )
        riskiest = reference.get("highest_risk_segment")
        anchor = (
            (riskiest["midpoint"][0], riskiest["midpoint"][1])
            if riskiest and riskiest.get("midpoint")
            else None
        )
        reference_mid = reference["geometry"][len(reference["geometry"]) // 2]
        reference_score, _context = score_factory(reference_mid[0], reference_mid[1])

        def over_modeled_land(point: Point) -> bool:
            try:
                return reference_score(point[0], point[1]).get("coverage") == "model"
            except Exception:  # noqa: BLE001 - vetting is best-effort
                return True

        vias = via_candidates(
            reference["geometry"],
            anchor,
            count=max_via_candidates,
            keep=over_modeled_land,
        )
        via_alternatives["attempted"] = True
        via_alternatives["candidate_count"] = len(vias)
        rejected = via_alternatives["rejected"]
        if not vias:
            via_alternatives["reason"] = "route_too_short"
        else:
            fetched = _fetch_via_routes(
                via_provider, origin, destination, vias, via_deadline_s
            )
            via_alternatives["returned"] = len(fetched)
            reference_km = reference["distance_km"]
            reference_min = reference["duration_min"]
            for via, raw, source in fetched:
                if len(distinct) >= target_routes:
                    break
                # Geometry checks first: they are cheap, and most candidates
                # fail one of them. Only a survivor pays for a scoring pass.
                coordinates = (raw.get("geometry") or {}).get("coordinates") or []
                if len(coordinates) < 2:
                    rejected["unscorable"] += 1
                    continue
                points = [(float(lat), float(lon)) for lon, lat in coordinates]
                length_km = polyline_length_m(points) / 1000.0
                minutes = (raw.get("duration") or 0) / 60.0 or None
                too_long = length_km > reference_km * VIA_MAX_DISTANCE_RATIO or (
                    reference_min
                    and minutes
                    and minutes > reference_min * VIA_MAX_DURATION_RATIO
                )
                if too_long:
                    rejected["too_long"] += 1
                    continue
                if has_backtrack(points):
                    rejected["backtrack"] += 1
                    continue
                if any(
                    geometries_similar(points, other["geometry"])
                    or route_overlap(points, other["geometry"]) >= VIA_MAX_OVERLAP
                    for other in distinct
                ):
                    rejected["duplicate"] += 1
                    continue
                candidate, _warning = _score_raw_route(
                    raw, score_factory, cell_for, model_version
                )
                if candidate is None:
                    rejected["unscorable"] += 1
                    continue
                candidate.update(
                    synthetic=False,
                    provider=f"{source}+via",
                    via=[round(via[0], 6), round(via[1], 6)],
                )
                distinct.append(candidate)
                via_alternatives["added"] += 1
            if via_alternatives["added"] == 0:
                via_alternatives["reason"] = (
                    "provider_returned_nothing" if not fetched else "no_distinct_alternative"
                )

    too_long_to_nudge = max(route["distance_km"] for route in distinct) > DETOUR_MAX_ROUTE_KM
    if len(distinct) < 2 and not too_long_to_nudge:
        fastest = min(
            distinct,
            key=lambda route: route["duration_min"]
            if route["duration_min"] is not None
            else float("inf"),
        )
        highest = fastest.get("highest_risk_segment")
        detour_synthesis["attempted"] = True
        detour_synthesis["reason"] = "fewer_than_two_distinct_routes"
        detour_synthesis["fastest_expected_crashes"] = fastest[
            "expected_crashes_total"
        ]
        if highest and highest.get("percentile") is not None:
            center = (highest["midpoint"][0], highest["midpoint"][1])
            detour_synthesis["highest_risk_cell"] = highest["cell"]
            detour_synthesis["highest_risk_percentile"] = highest["percentile"]
            candidates = synthesize_detours(
                fastest["geometry"], center, count=max_detours
            )
            detour_synthesis["candidate_count"] = len(candidates)
            scored_detours = []
            for candidate in candidates:
                midpoint = candidate[len(candidate) // 2]
                score_at, context = score_factory(midpoint[0], midpoint[1])
                duration_min = (
                    (fastest["duration_min"] or 0)
                    * polyline_length_m(candidate)
                    / max(1.0, polyline_length_m(fastest["geometry"]))
                )
                try:
                    exposure = score_geometry(
                        candidate,
                        cell_for,
                        score_at,
                        duration_min=duration_min or None,
                        model_version=model_version,
                        context=context,
                    )
                except GeometryError:
                    continue
                annotate_segments(exposure, None)
                if exposure["coverage"] == "rule_based_only":
                    continue
                if exposure["covered_length_m"] / max(
                    1.0, exposure["distance_km"] * 1000.0
                ) < MIN_DETOUR_COVERAGE:
                    continue
                if any(
                    geometries_similar(exposure["geometry"], other["geometry"])
                    for other in [fastest, *scored_detours]
                ):
                    continue
                scored_detours.append(
                    {
                        **exposure,
                        "synthetic": True,
                        "provider": "synthesized",
                        "detour_of": fastest.get("route_type", "fastest"),
                        "detour_center_cell": highest["cell"],
                    }
                )
            detour_synthesis["scored_count"] = len(scored_detours)
            if scored_detours:
                best = min(
                    scored_detours,
                    key=lambda route: route["expected_crashes_total"],
                )
                detour_synthesis["best_detour_expected_crashes"] = best[
                    "expected_crashes_total"
                ]
                detour_synthesis["improved"] = (
                    best["expected_crashes_total"]
                    < fastest["expected_crashes_total"] - 1e-9
                )
            distinct.extend(scored_detours)
        else:
            warnings.append(
                "No modeled segment was available to anchor a synthesized detour"
            )

    tag_routes(distinct)
    return RoutePlan(
        routes=distinct,
        provider_source=provider_source,
        total_provider_routes=len(raw_routes),
        distinct_provider_routes=distinct_count,
        detour_synthesis=detour_synthesis,
        warnings=warnings,
        via_alternatives=via_alternatives,
    )


def departure_slots(hour: int, day: int, window: int = DEPARTURE_WINDOW_HOURS) -> list[tuple[int, int, int]]:
    """``(offset, hour, day)`` around a departure, rolling over midnight (0 = Monday)."""
    slots = []
    for offset in range(-window, window + 1):
        total = hour + offset
        slots.append((offset, total % 24, (day + total // 24) % 7))
    return slots


def departure_centre(hour: int, day: int, anchor: tuple[int, int] | None) -> tuple[int, int]:
    """The hour the window sits around: the original plan, while still in reach.

    After taking a suggested time the window stays where it was. Re-centring it
    on the new time would let every suggestion push the trip a little later.
    """
    if anchor is not None and any(
        (slot_hour, slot_day) == (hour, day) for _, slot_hour, slot_day in departure_slots(*anchor)
    ):
        return anchor
    return hour, day


def _hour_label(hour: int) -> str:
    return f"{hour:02d}:00"


def _departure_text(
    verdict: str,
    chosen: dict,
    best: dict | None,
    percent: float,
    basis: str,
    centre_hour: int,
) -> str:
    if verdict == "move":
        gain = (
            f"lowers this route's estimated risk by {percent:.0f}%"
            if basis == "relative_risk_estimate"
            else f"means {percent:.0f}% fewer expected injury crashes on this route"
        )
        text = f"Leaving at {_hour_label(best['hour'])} instead of {_hour_label(chosen['hour'])} {gain}."
        beyond = best.get("adjusted_change_percent")
        if beyond is not None:
            text += f" That is {abs(beyond):.0f}% beyond the citywide change at that hour."
        return text
    if verdict == "already_best":
        return f"This is the best hour within three hours of your original {_hour_label(centre_hour)}."
    if verdict == "busy_hours_only":
        return (
            "Other hours look lower, but mostly because roads across the city are "
            "quieter then, so no change of time is advised."
        )
    if verdict == "only_small_hours":
        return (
            "Only the small hours look lower, and that mostly reflects empty roads, "
            "so no change of time is advised."
        )
    return "Timing makes little difference for this trip within three hours either way."


def plan_departures(
    segments: Sequence[dict],
    hour: int,
    day: int,
    rate_at: Callable[[float, float, int, int], dict],
    *,
    basis: str = "expected_injury_crashes",
    city_rate: Callable[[int, int], float] | None = None,
    anchor: tuple[int, int] | None = None,
    can_leave_earlier: bool = True,
) -> dict:
    """Re-score a route's stretches at each hour around the plan.

    No routing happens here: the stretches are the chosen route's. Adds up the
    same way ``score_geometry`` does, one score per cell, so the chosen hour
    reproduces the route card's number.

    ``city_rate`` is the city's mean rate at an hour. With no traffic counts,
    it stands in for how busy that hour is: a route that only falls as far as
    the whole city does has shown nothing about itself, so a suggestion must
    beat the city's change as well. This also discounts real per-car effects,
    so it errs towards saying nothing.

    A live trip cannot leave earlier than now, so ``can_leave_earlier`` false
    keeps the earlier hours on the chart but out of the advice.
    """
    centre = departure_centre(hour, day, anchor)
    slots = []
    for offset, slot_hour, slot_day in departure_slots(*centre):
        scores: dict[str, dict] = {}
        total = 0.0
        for segment in segments:
            cell = segment["cell"]
            score = scores.get(cell)
            if score is None:
                lat, lon = segment["midpoint"]
                score = rate_at(lat, lon, slot_hour, slot_day)
                scores[cell] = score
            rate = score.get("expected_rate")
            if rate is not None:
                total += float(rate) * (float(segment["length_m"]) / 1000.0) / _cell_width_km(score)
        slots.append(
            {
                "offset_hours": offset,
                "hour": slot_hour,
                "day_of_week": slot_day,
                "expected_crashes_total": round(total, 6),
                "is_chosen": (slot_hour, slot_day) == (hour, day),
                "is_recommended": False,
                "small_hours": slot_hour in SMALL_HOURS,
            }
        )

    chosen = next(slot for slot in slots if slot["is_chosen"])
    base = chosen["expected_crashes_total"]
    for slot in slots:
        slot["is_past"] = not can_leave_earlier and slot["offset_hours"] < chosen["offset_hours"]
    for slot in slots:
        slot["change_percent"] = (
            round((slot["expected_crashes_total"] - base) / base * 100.0, 1) if base > 0 else 0.0
        )
    if city_rate is not None:
        city_base = city_rate(hour, day)
        for slot in slots:
            city_ratio = city_rate(slot["hour"], slot["day_of_week"]) / city_base if city_base > 0 else 1.0
            slot["city_change_percent"] = round((city_ratio - 1.0) * 100.0, 1)
            slot["adjusted_change_percent"] = (
                round(((slot["expected_crashes_total"] / base) / city_ratio - 1.0) * 100.0, 1)
                if base > 0 and city_ratio > 0
                else 0.0
            )

    def beats_the_city(slot: dict) -> bool:
        adjusted = slot.get("adjusted_change_percent")
        return adjusted is None or adjusted <= -MEANINGFUL_REDUCTION * 100.0

    def meaningful(slot: dict) -> bool:
        return not slot["is_chosen"] and not slot["is_past"] and base > 0 and (
            slot["expected_crashes_total"] <= base * (1.0 - MEANINGFUL_REDUCTION)
        )

    candidates = [slot for slot in slots if meaningful(slot)]
    waking = [slot for slot in candidates if not slot["small_hours"]]
    allowed = [slot for slot in waking if beats_the_city(slot)]
    best = None
    if allowed:
        # Ties go to the smaller change of plan, then to leaving later.
        best = min(
            allowed,
            key=lambda slot: (
                slot["expected_crashes_total"],
                abs(slot["offset_hours"]),
                -slot["offset_hours"],
            ),
        )
        best["is_recommended"] = True
        verdict = "move"
    elif waking:
        verdict = "busy_hours_only"
    elif candidates:
        verdict = "only_small_hours"
    elif centre != (hour, day):
        verdict = "already_best"
    else:
        verdict = "little_difference"

    percent = (base - best["expected_crashes_total"]) / base * 100.0 if best else 0.0
    return {
        "available": True,
        "basis": basis,
        "verdict": verdict,
        "text": _departure_text(verdict, chosen, best, percent, basis, centre[0]),
        "recommended": best,
        "reduction_percent": round(percent, 1),
        "adjusted_reduction_percent": (
            abs(best["adjusted_change_percent"]) if best and "adjusted_change_percent" in best else None
        ),
        "threshold_percent": round(MEANINGFUL_REDUCTION * 100.0),
        "slots": slots,
    }
