"""'Get home safe': a plan for a trip after dark, not just a route.

Nothing is estimated here. Every step restates something already scored: the
route's stretches, the departure planner's verdict, the map cells near the
start, and the safe-stop index. The module only decides what to say and in what
order, so a number on screen can always be traced to the model behind it.

The driver's own state is deliberately absent. Check-in answers never leave the
browser, so the app reorders these steps itself when someone says they should
not drive.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from route_engine import haversine_m

#: 20:00 to 05:59, the danger model's evening and small-hours bands.
NIGHT_HOURS = frozenset(range(20, 24)) | frozenset(range(0, 6))
#: A stretch counts as high for a kind of crash at twice a typical hour. A design choice.
ELEVATED_RATIO = 2.0
#: How far someone should be asked to walk at night, and how much lower a place
#: must rank to be worth it. Design choices, both shown to the user.
PICKUP_MAX_WALK_M = 1000.0
PICKUP_MIN_DROP = 15.0
#: Moves shorter than this are dropped from the plan unless they are risky.
MIN_LEG_KM = 0.3

COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def is_night(hour: int) -> bool:
    return hour in NIGHT_HOURS


def direction(start: Sequence[float], end: Sequence[float]) -> str:
    """The compass word for walking from one point to another."""
    lat1, lat2 = math.radians(start[0]), math.radians(end[0])
    d_lon = math.radians(end[1] - start[1])
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return COMPASS[round(bearing / 45.0) % 8]


def _ratio(segment: dict, group: str) -> float | None:
    for item in (segment.get("danger_types") or {}).get("groups", []):
        if item.get("id") == group and item.get("status") == "ok":
            return item.get("rate_ratio")
    return None


def danger_on_route(segments: Iterable[dict], group: str = "impaired") -> dict | None:
    """Where a kind of crash runs high on a route, or None without crash-type data."""
    segments = list(segments)
    if not any(segment.get("danger_types") for segment in segments):
        return None
    metres, worst = 0.0, 0.0
    streets: dict[str, float] = {}
    for segment in segments:
        ratio = _ratio(segment, group)
        if ratio is None or ratio < ELEVATED_RATIO:
            continue
        metres += float(segment.get("length_m") or 0.0)
        worst = max(worst, ratio)
        street = segment.get("street")
        if street:
            streets[street] = max(streets.get(street, 0.0), ratio)
    return {
        "km": round(metres / 1000.0, 1),
        "worst": round(worst, 1),
        "streets": sorted(streets, key=streets.get, reverse=True),
    }


def streets_avoided(fastest: dict | None, chosen: dict | None, limit: int = 2) -> list[str]:
    """The fastest route's worst streets that the chosen route stays off."""
    if not fastest or not chosen:
        return []
    used = {segment.get("street") for segment in chosen.get("segments", [])}
    worst = danger_on_route(fastest.get("segments", [])) or {"streets": []}
    return [street for street in worst["streets"] if street not in used][:limit]


def choose_pickup(
    origin: Sequence[float],
    here_percentile: float | None,
    cells: Iterable[dict],
    *,
    max_walk_m: float = PICKUP_MAX_WALK_M,
    min_drop: float = PICKUP_MIN_DROP,
) -> dict | None:
    """The nearest place within a walk that ranks clearly lower than the start."""
    if here_percentile is None:
        return None
    best = None
    for cell in cells:
        percentile = cell.get("percentile")
        if percentile is None or here_percentile - float(percentile) < min_drop:
            continue
        walk = haversine_m(origin[0], origin[1], float(cell["lat"]), float(cell["lon"]))
        if walk <= max_walk_m and (best is None or walk < best["walk_m"]):
            best = {
                "walk_m": round(walk),
                "direction": direction(origin, (float(cell["lat"]), float(cell["lon"]))),
                "lat": float(cell["lat"]),
                "lon": float(cell["lon"]),
                "percentile": round(float(percentile), 1),
                "from_percentile": round(float(here_percentile), 1),
            }
    return best


def rest_stop(stops: Iterable[dict]) -> dict | None:
    """Somewhere to stop: a rest area first, then the nearest car park."""
    rank = lambda stop: 0 if "rest" in str(stop.get("type")) else 1 if stop.get("type") == "parking" else 2
    usable = [stop for stop in stops if rank(stop) < 2]
    return min(usable, key=lambda stop: (rank(stop), stop.get("distance_m", 0)), default=None)


def ordinal(percentile: float) -> str:
    """ "81st", "92nd", "99th". Capped at 99: nothing ranks above every place."""
    whole = min(99, max(1, round(percentile)))
    if 10 <= whole % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(whole % 10, "th")
    return f"{whole}{suffix}"


def _minutes(value: float | None) -> str:
    whole = round(value or 0)
    return f"{whole} min" if whole < 60 else f"{whole // 60} h {whole % 60} min"


def route_facts(route: dict) -> dict:
    """What a road is like tonight, in the terms the plan compares it on."""
    critical, high = 0.0, 0.0
    for segment in route.get("segments", []):
        if segment.get("risk_level") == "CRITICAL":
            critical += float(segment.get("length_m") or 0.0)
        elif segment.get("risk_level") == "HIGH":
            high += float(segment.get("length_m") or 0.0)
    danger = danger_on_route(route.get("segments", []))
    return {
        "minutes": float(route.get("duration_min") or 0.0),
        "km": float(route.get("distance_km") or 0.0),
        "risk": float(route.get("expected_crashes_total") or 0.0),
        "critical_km": round(critical / 1000.0, 1),
        "high_km": round(high / 1000.0, 1),
        "impaired_km": danger["km"] if danger else None,
    }


def why_best(best: dict, fastest: dict) -> str:
    if best is fastest:
        return (
            "It is both the fastest and the lowest-risk road."
            if best.get("is_safest")
            else "It is the fastest, and the safer roads do not save enough risk for the extra time."
        )
    base = float(fastest.get("expected_crashes_total") or 0.0)
    cut = (base - float(best.get("expected_crashes_total") or 0.0)) / base * 100.0 if base > 0 else 0.0
    extra = float(best.get("duration_min") or 0.0) - float(fastest.get("duration_min") or 0.0)
    return f"{cut:.0f}% less risk than the fastest road, for {extra:.0f} more minutes."


def why_not(route: dict, best: dict) -> str:
    """One line on how a road compares with the pick."""
    mine, theirs = route_facts(route), route_facts(best)
    minutes = mine["minutes"] - theirs["minutes"]
    risk = (mine["risk"] - theirs["risk"]) / theirs["risk"] * 100.0 if theirs["risk"] > 0 else 0.0
    time_part = (
        "about the same time"
        if abs(minutes) < 1
        else f"{abs(minutes):.0f} min {'slower' if minutes > 0 else 'faster'}"
    )
    risk_part = (
        "about the same risk"
        if abs(risk) < 1
        else f"{abs(risk):.0f}% {'more' if risk > 0 else 'less'} risk"
    )
    joiner = " and " if (minutes > 0) == (risk > 0) or abs(minutes) < 1 or abs(risk) < 1 else ", but "
    text = f"{time_part.capitalize()}{joiner}{risk_part}"
    extra_critical = mine["critical_km"] - theirs["critical_km"]
    if extra_critical >= 0.5:
        text += f", with {extra_critical:.1f} km more critical road"
    return f"{text}."


def route_name(route: dict) -> str:
    """The name a road goes by on its route card."""
    if route.get("is_safest"):
        return "Safest"
    if route.get("is_fastest"):
        return "Fastest"
    if route.get("is_balanced"):
        return "Balanced"
    return f"Option {route.get('rank', 0)}"


def scan_options(routes: Sequence[dict], best_index: int) -> list[dict]:
    """Every road for the trip, the pick first, each with its case."""
    best = routes[best_index]
    options = []
    for index, route in enumerate(routes):
        facts = route_facts(route)
        options.append(
            {
                "route_index": index,
                "label": route_name(route),
                "is_best": index == best_index,
                "minutes": round(facts["minutes"]),
                "km": round(facts["km"], 1),
                "risk": round(facts["risk"], 3),
                "critical_km": facts["critical_km"],
                "high_km": facts["high_km"],
                "why": "The pick." if index == best_index else why_not(route, best),
            }
        )
    return sorted(options, key=lambda item: (not item["is_best"], item["minutes"]))


def _worst_between(segments: Sequence[dict], start_m: float, end_m: float, scale: float) -> tuple[str | None, float]:
    """The worst level on part of a route, and how far of it is high or critical."""
    at = 0.0
    worst = None
    risky = 0.0
    for segment in segments:
        length = float(segment.get("length_m") or 0.0) * scale
        overlap = min(end_m, at + length) - max(start_m, at)
        at += length
        if overlap <= 0:
            continue
        level = segment.get("risk_level")
        if level in ("HIGH", "CRITICAL"):
            risky += overlap
            if worst != "CRITICAL":
                worst = level
    return worst, risky


def _keep_legs(legs: list[dict], limit: int) -> list[dict]:
    """Short hops are dropped first; a risky leg always stays."""
    useful = [leg for leg in legs if leg["km"] >= MIN_LEG_KM or (leg["level"] and leg["km"] >= 0.1)]
    if len(useful) <= limit:
        return useful
    ranked = sorted(useful, key=lambda leg: (leg["level"] == "CRITICAL", leg["level"] == "HIGH", leg["km"]), reverse=True)
    keep = {id(leg) for leg in ranked[:limit]}
    return [leg for leg in useful if id(leg) in keep]


def build_legs(route: dict, instructions: Sequence[dict], length_m: float | None, *, limit: int = 8) -> list[dict]:
    """The drive as moves: what to do, for how long, and what that part is like."""
    segments = route.get("segments", [])
    total = sum(float(segment.get("length_m") or 0.0) for segment in segments)
    if total <= 0:
        return []
    if instructions:
        scale = total / float(length_m) if length_m else 1.0
        marks = [
            (float(step["offset_m"]) * scale, str(step.get("message") or "Continue"))
            for step in instructions
            if "ARRIVE" not in str(step.get("maneuver"))
        ]
    else:
        # No directions: fall back to the streets the route runs along.
        marks = []
        at = 0.0
        for segment in segments:
            street = segment.get("street")
            if street and (not marks or marks[-1][2] != street):
                marks.append((at, f"Continue on {street}", street))
            at += float(segment.get("length_m") or 0.0)
        marks = [(offset, text) for offset, text, _ in marks]
        scale = 1.0
    legs = []
    for index, (offset, message) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else total
        level, risky = _worst_between(segments, offset, end, 1.0)
        legs.append(
            {
                "message": message,
                "km": round((end - offset) / 1000.0, 1),
                "level": level,
                "risky_km": round(risky / 1000.0, 1),
            }
        )
    return _keep_legs(legs, limit)


def _leg_step(leg: dict) -> dict:
    """One move. The risk is a fact here; what to do about it is said once, above."""
    detail = f"{leg['km']:.1f} km"
    if leg["level"]:
        word = "critical" if leg["level"] == "CRITICAL" else "high risk"
        whole = leg["risky_km"] >= leg["km"] - 0.05
        detail += f" · {word} all the way" if whole else f" · {word} for {leg['risky_km']:.1f} km"
    return {"kind": "drive", "level": leg["level"], "title": leg["message"], "detail": detail}


def drive_advice(legs: Sequence[dict]) -> dict | None:
    """What the risky parts add up to, and what to do about them, in one line."""
    critical = sum(leg["risky_km"] for leg in legs if leg["level"] == "CRITICAL")
    high = sum(leg["risky_km"] for leg in legs if leg["level"] == "HIGH")
    if critical <= 0 and high <= 0:
        return None
    if critical > 0:
        title = f"Take extra care on {critical:.1f} km of critical road"
        detail = "Slow down and leave more room there. Those stretches are marked in the moves below."
    else:
        title = f"Stay alert on {high:.1f} km of high-risk road"
        detail = "Those stretches are marked in the moves below."
    return {"kind": "care", "title": title, "detail": detail}


def build_steps(
    *,
    here: dict,
    fastest_danger: dict | None,
    scanned: int,
    best_label: str,
    best: dict,
    why: str,
    timing: dict | None,
    avoided: list[str],
    legs: list[dict],
    destination_note: str,
    pickup: dict | None,
    stop: dict | None,
) -> list[dict]:
    """The whole plan in order, as ``{kind, title, detail}`` lines."""
    steps = [
        {
            "kind": "now",
            "title": f"{str(here.get('risk_level', 'UNKNOWN')).title()} risk where you are",
            "detail": (
                f"Your start ranks at the {ordinal(here['percentile'])} percentile at this hour."
                if here.get("percentile") is not None
                else "There is no crash model for your start."
            ),
        },
    ]
    if fastest_danger is None:
        steps.append(
            {
                "kind": "danger",
                "title": "Impaired-driver data is New York only",
                "detail": "Crash-type records do not cover this trip, so no figure is given.",
            }
        )
    elif fastest_danger["km"] > 0:
        steps.append(
            {
                "kind": "danger",
                "title": f"Impaired-driver crashes run up to {fastest_danger['worst']:.1f}× a typical hour",
                "detail": f"That is on {fastest_danger['km']:.1f} km of the fastest road, at this hour.",
            }
        )
    else:
        steps.append(
            {
                "kind": "danger",
                "title": "No stretch runs high for impaired drivers",
                "detail": "Nothing on the fastest road reaches twice a typical hour.",
            }
        )
    noun = "road" if scanned == 1 else "roads"
    stays_off = f" It stays off {' and '.join(avoided)}." if avoided else ""
    steps.append(
        {
            "kind": "best",
            "title": f"Best of {scanned} {noun}: the {best_label.lower()} one, {_minutes(best.get('duration_min'))}",
            "detail": f"{why}{stays_off}",
        }
    )
    if timing and timing.get("available"):
        steps.append({"kind": "when", "title": "When to leave", "detail": timing["text"]})
    care = drive_advice(legs)
    if care:
        steps.append(care)
    steps.extend(_leg_step(leg) for leg in legs)
    if stop:
        steps.append(
            {
                "kind": "stop",
                "title": "If you need a break",
                "detail": f"{stop.get('name', 'A stop')}, {float(stop.get('distance_km', 0)):.1f} km from the middle of the trip.",
            }
        )
    steps.append({"kind": "arrive", "title": "Arrive", "detail": destination_note})
    if pickup:
        steps.append(
            {
                "kind": "pickup",
                "title": f"Being picked up? Walk about {pickup['walk_m']} m {pickup['direction']}",
                "detail": (
                    f"That area ranks at the {ordinal(pickup['percentile'])} percentile, against the "
                    f"{ordinal(pickup['from_percentile'])} here. Distance is in a straight line."
                ),
            }
        )
    return steps
