"""Global context estimate: a risk rating for places the NYC model has never seen.

The crash model is trained on New York City crash records and cannot speak for
London or Riyadh. Outside its area this module gives an *estimate* built from
inputs that exist everywhere, each one shown to the user as a reason:

  country baseline   road deaths per 100k people (World Bank / WHO, data/)
  road               OpenStreetMap road class, or the routed speed on a route
  junctions          junctions per km along a route (from the router's steps)
  manoeuvres         turns across oncoming traffic, U-turns, merges, roundabouts
  time               hour of day, with the local weekend's late nights
  weather            live conditions (rain, snow, fog, wind)
  nightlife          bars, pubs and clubs nearby (same 0-40 scale as NYC)

The factors are multiplied into a relative risk index and mapped onto the same
0-100 score and bands the NYC model uses. They are reasoned heuristics, not
learned weights, and the output says so: ``coverage`` is ``"context"`` and the
rate is a relative index, not a crash count.
"""
from __future__ import annotations

import json
import math
import os

from crash_model.model import action_for_band, nightlife_points, risk_band

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "data", "road_deaths_per_100k.json"), encoding="utf-8") as _handle:
    _DATA = json.load(_handle)
COUNTRIES: dict[str, dict] = _DATA["countries"]
MEDIAN_DEATHS = float(_DATA["median_deaths_per_100k"])
BASELINE_SOURCE = "World Bank / WHO road deaths per 100k"

VERSION = "context-2"
CELL_AREA_KM2 = 0.737  # mean area of an H3 resolution-8 cell
# Turns the relative index into a pseudo cell-hour rate, so a route outside NYC
# integrates exposure the same way as inside it. The total is relative.
REFERENCE_RATE = 0.004
# How quickly the 0-100 position moves away from 50 as the index moves away
# from 1.0 (typical conditions on a typical road in a median country). A
# logistic on log(index): 1.5x typical sits near 76, 2x near 88, half near 12.
INDEX_SPREAD = 0.35

# Countries whose weekend is Friday and Saturday.
FRI_SAT_WEEKEND = {"SA", "KW", "QA", "BH", "OM", "EG", "JO", "IQ", "YE", "DZ", "LY", "SD", "SY"}

ROAD_FACTORS: dict[str, tuple[float, str]] = {
    "motorway": (0.85, "Motorway"),
    "motorway_link": (1.05, "Motorway ramp"),
    "trunk": (1.15, "Trunk road"),
    "trunk_link": (1.1, "Trunk road ramp"),
    "primary": (1.3, "Primary road"),
    "primary_link": (1.15, "Primary road link"),
    "secondary": (1.25, "Secondary road"),
    "secondary_link": (1.1, "Secondary road link"),
    "tertiary": (1.1, "Tertiary road"),
    "tertiary_link": (1.05, "Tertiary road link"),
    "unclassified": (1.0, "Minor road"),
    "residential": (0.8, "Residential street"),
    "living_street": (0.55, "Living street"),
    "service": (0.6, "Service road"),
    "pedestrian": (0.5, "Pedestrian street"),
    "track": (0.8, "Track"),
}

# Crash risk per kilometre by hour: lowest in the working day, highest in the
# small hours (fatigue, impairment, speed), with commuting peaks.
HOUR_FACTORS = [
    1.45, 1.55, 1.6, 1.55, 1.35, 1.1, 1.05, 1.2, 1.25, 1.1, 1.0, 1.0,
    1.0, 1.0, 1.05, 1.15, 1.25, 1.3, 1.25, 1.15, 1.15, 1.2, 1.3, 1.4,
]

# Manoeuvres on a route stretch. Turns across oncoming traffic (left where
# traffic keeps right, right where it keeps left) are among the commonest
# junction crashes; merges add weaving; roundabouts are junctions too, but slow.
MANEUVER_FACTORS = {
    "uturn": 1.4,
    "across": 1.35,
    "merge": 1.15,
    "kerbside": 1.1,
    "roundabout": 1.05,
}
# Several manoeuvres in one stretch compound, up to this.
MANEUVER_CAP = 1.6
MANEUVER_LABELS = {
    "uturn": "U-turn across oncoming traffic",
    "merge": "Merging into faster traffic",
    "roundabout": "Roundabout",
}

# Countries and territories where traffic keeps left. Decides which turn crosses
# oncoming traffic; the public routers report right-hand traffic everywhere.
LEFT_HAND_TRAFFIC = {
    "AG", "AI", "AU", "BB", "BD", "BM", "BN", "BS", "BT", "BW", "CK", "CY", "DM", "FJ",
    "FK", "GB", "GD", "GG", "GY", "HK", "ID", "IE", "IM", "IN", "JE", "JM", "JP", "KE",
    "KI", "KN", "KY", "LC", "LK", "LS", "MO", "MS", "MT", "MU", "MV", "MW", "MY", "MZ",
    "NA", "NP", "NR", "NU", "NZ", "PG", "PK", "SB", "SC", "SG", "SH", "SR", "SZ", "TC",
    "TH", "TL", "TO", "TT", "TV", "TZ", "UG", "VC", "VG", "VI", "WS", "ZA", "ZM", "ZW",
}


def driving_side(country: str | None) -> str:
    return "left" if (country or "").upper() in LEFT_HAND_TRAFFIC else "right"


def maneuver_effect(item: dict, side: str) -> tuple[str, float, str] | None:
    """``(kind, factor, label)`` for one manoeuvre where traffic keeps to ``side``."""
    kind = item.get("kind") or ""
    if kind == "turn":
        direction = item.get("turn")
        if direction not in ("left", "right"):
            return None
        if direction == ("right" if side == "left" else "left"):
            kind, label = "across", f"{direction.title()} turn across oncoming traffic"
        else:
            kind, label = "kerbside", f"{direction.title()} turn across the kerbside crossing"
    elif kind in MANEUVER_FACTORS:
        label = item.get("label") or MANEUVER_LABELS.get(kind, kind)
    else:
        return None
    street = item.get("street")
    return kind, MANEUVER_FACTORS[kind], f"{label}{' onto ' + street if street else ''}"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def country_baseline(country: str | None) -> tuple[float, dict | None]:
    """Multiplier from the country's road-death rate, damped by a square root.

    Deaths per 100k people mixes risk with how much people drive, so the raw
    ratio is too strong a lever; the square root keeps a 4x difference in rate
    to a 2x difference in the estimate.
    """
    info = COUNTRIES.get((country or "").upper())
    if not info:
        return 1.0, None
    return _clamp((info["deaths_per_100k"] / MEDIAN_DEATHS) ** 0.5, 0.45, 2.2), info


def is_party_night(day: int, hour: int, country: str | None) -> bool:
    """The two late nights before a weekend day, in the local weekend."""
    first, second = (3, 4) if (country or "").upper() in FRI_SAT_WEEKEND else (4, 5)
    if hour >= 20 and day in (first, second):
        return True
    return hour < 5 and day in ((first + 1) % 7, (second + 1) % 7)


def time_factor(hour: int, day: int, country: str | None) -> tuple[float, str]:
    factor = HOUR_FACTORS[hour % 24]
    label = f"{hour:02d}:00 {DAY_NAMES[day % 7]}"
    if is_party_night(day % 7, hour % 24, country):
        return factor * 1.15, f"{label}: a weekend night, the riskiest hours on the road"
    if factor >= 1.4:
        return factor, f"{label}: late night, when fatigue and impairment peak"
    if factor >= 1.2:
        return factor, f"{label}: a busy commuting or evening hour"
    return factor, f"{label}: a quieter daytime hour"


def weather_factor(weather: dict | None) -> tuple[float, str]:
    if not weather:
        return 1.0, "Live weather unavailable - assumed dry"
    factor = 1.0
    parts = []
    temperature = weather.get("temperature_c")
    precipitation = float(weather.get("precipitation_mm") or 0.0)
    if weather.get("snow") or (temperature is not None and temperature <= 0.5 and precipitation > 0):
        factor *= 1.7
        parts.append("snow or ice")
    elif weather.get("rain"):
        heavy = precipitation >= 4.0
        factor *= 1.45 if heavy else 1.3
        parts.append("heavy rain" if heavy else "rain")
    visibility = weather.get("visibility_km")
    if weather.get("fog") or (visibility is not None and visibility < 1.0):
        factor *= 1.35
        parts.append("poor visibility")
    if float(weather.get("wind_kph") or 0.0) >= 50:
        factor *= 1.1
        parts.append("strong wind")
    described = ", ".join(parts) if parts else "dry"
    where = f" ({weather['provider']})" if weather.get("provider") else ""
    temp = f", {temperature:.0f} °C" if temperature is not None else ""
    return factor, f"Live weather: {described}{temp}{where}"


def road_factor(
    road: dict | None,
    speed_kmh: float | None = None,
    junctions_per_km: float | None = None,
    maneuvers: list[dict] | None = None,
    country: str | None = None,
) -> tuple[float, list[str]]:
    factor = 1.0
    reasons: list[str] = []
    if road and road.get("class") in ROAD_FACTORS:
        weight, label = ROAD_FACTORS[road["class"]]
        factor *= weight
        reasons.append(f"{label}{' - ' + road['name'] if road.get('name') else ''}")
    if speed_kmh is not None and speed_kmh > 0:
        if speed_kmh < 25:
            weight, label = 0.9, "slow traffic"
        elif speed_kmh < 45:
            weight, label = 1.0, "urban speeds"
        elif speed_kmh < 65:
            weight, label = 1.15, "fast urban roads"
        elif speed_kmh < 85:
            weight, label = 1.3, "high-speed roads"
        else:
            weight, label = 1.2, "highway speeds"
        factor *= weight
        reasons.append(f"Routed speed about {speed_kmh:.0f} km/h ({label})")
    if junctions_per_km:
        factor *= 1.0 + 0.03 * min(float(junctions_per_km), 12.0)
        reasons.append(f"{junctions_per_km:.0f} junctions per km")
    weight, labels = 1.0, []
    side = driving_side(country)
    for item in maneuvers or []:
        effect = maneuver_effect(item, side)
        if effect is None:
            continue
        weight *= effect[1]
        labels.append(effect[2])
    if weight > 1.0:
        factor *= min(weight, MANEUVER_CAP)
        more = len(labels) - 2
        reasons.append("; ".join(labels[:2]) + (f" (+{more} more)" if more > 0 else ""))
    return factor, reasons


def estimate(
    lat: float,
    lon: float,
    hour: int,
    day: int,
    *,
    cell: str | None,
    weather: dict | None,
    bars: float | int | None,
    road: dict | None = None,
    country: str | None = None,
    speed_kmh: float | None = None,
    junctions_per_km: float | None = None,
    maneuvers: list[dict] | None = None,
) -> dict:
    """A risk score in the same shape as ``RiskModel.score``, marked as an estimate."""
    country = (country or (road or {}).get("country") or "").upper() or None
    base, info = country_baseline(country)
    time_weight, time_reason = time_factor(hour, day, country)
    weather_weight, weather_reason = weather_factor(weather)
    road_weight, road_reasons = road_factor(road, speed_kmh, junctions_per_km, maneuvers, country)

    index = base * time_weight * weather_weight * road_weight
    share = 1.0 / (1.0 + math.exp(-math.log(index) / INDEX_SPREAD))
    context_points = round(60.0 * share, 1)
    night = nightlife_points(bars)
    total = min(100.0, context_points + night)
    level = risk_band(total)
    rate = REFERENCE_RATE * index

    reasons = []
    if info:
        reasons.append(
            f"{info['name']}: {info['deaths_per_100k']:g} road deaths per 100k people "
            f"({info['year']}), {'above' if base > 1 else 'below'} the world median of {MEDIAN_DEATHS:g}"
        )
    else:
        reasons.append("Country baseline unavailable - world median used")
    reasons.extend(road_reasons)
    reasons.append(time_reason)
    reasons.append(weather_reason)
    if night:
        reasons.append(f"Nightlife nearby ({int(bars or 0)} bars, pubs or clubs) adds {night} points")

    return {
        "cell": cell,
        "coverage": "context",
        "coverage_reason": "outside_model_area_context_estimate",
        "model_available": True,
        "model_version": VERSION,
        "expected_rate": rate,
        "expected_rate_per_km2_hour": rate / CELL_AREA_KM2,
        # A position on the estimate's own 0-100 scale, used for colouring. It
        # is not a percentile of observed crashes.
        "percentile": round(100.0 * share, 1),
        "model_points": context_points,
        "nightlife_points": night,
        "risk_score": round(total, 1),
        "risk_level": level,
        "action": action_for_band(level),
        "reasons": reasons,
        "cell_area_km2": CELL_AREA_KM2,
        "context": {
            "country": country,
            "country_name": info["name"] if info else None,
            "deaths_per_100k": info["deaths_per_100k"] if info else None,
            "baseline_factor": round(base, 3),
            "time_factor": round(time_weight, 3),
            "weather_factor": round(weather_weight, 3),
            "road_factor": round(road_weight, 3),
            "road_class": (road or {}).get("class"),
            "road_name": (road or {}).get("name"),
            "maneuvers": [
                effect[0]
                for effect in (maneuver_effect(item, driving_side(country)) for item in maneuvers or [])
                if effect
            ],
            "index": round(index, 3),
            "basis": "estimate from road, time, weather, nightlife and country data",
        },
    }
