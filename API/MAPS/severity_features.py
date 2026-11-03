"""Feature engineering for the crash-severity model.

Why this module exists
----------------------
The original model was trained from ``crashes.csv``, which has six columns:
date, time, lat, lon, injured, killed. Severity is driven by *what was
involved* - a bicycle, a motorcycle, unsafe speed, alcohol - and none of that
was in the data, so the model could not beat the majority class no matter how
it was tuned.

``fetch_crashes_rich.py`` downloads the full NYC Open Data collision dataset
(h9gi-nx95), which adds vehicle types and contributing factors. This module
turns those raw strings into a stable, ordered feature frame shared by training
and serving, so the two can never drift apart.

Leakage rule
------------
Every injury and fatality count is an *outcome* and is used only to build the
label. No count is ever used as a feature. Vehicle types and contributing
factors are crash attributes recorded by the responding officer, not outcomes,
so they are legitimate features - but see the honesty note in ``train.py``:
they are recorded *after* the crash and may be shaped by its severity, which
makes the model retrospective, not predictive.

Ordering
--------
``SEVERITY_FEATURES`` is the single source of truth. The saved model was
trained on exactly this order, and serving must build the same columns.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Iterable, Mapping

import pandas as pd

from features import NYC_BBOX, time_features

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
BOROUGH_CODES = {name: index + 1 for index, name in enumerate(BOROUGHS)}  # 0 = unknown

VEHICLE_GROUPS = [
    "bicycle",
    "ebike",
    "scooter",
    "motorcycle",
    "truck",
    "bus",
    "van",
    "taxi",
    "suv",
    "sedan",
    "other",
]

FACTOR_GROUPS = [
    "unsafe_speed",
    "alcohol",
    "distraction",
    "failure_to_yield",
    "following_too_closely",
    "lane_usage",
    "traffic_control",
    "backing",
    "turning",
    "inexperience",
    "view_obstructed",
    "aggressive",
    "slippery",
    "asleep",
    "brakes",
    "pedestrian_error",
]

#: Columns that describe the moment and place only - known before a crash.
CONTEXT_FEATURES = [
    "hour",
    "is_weekend",
    "month",
    "is_night",
    "is_rush_hour",
    "lat",
    "lon",
    "borough_code",
]

#: Columns that describe the crash itself - only known after it happened.
CRASH_ATTRIBUTE_FEATURES = (
    ["n_vehicles", "n_factors"]
    + [f"is_{group}" for group in VEHICLE_GROUPS]
    + [f"factor_{group}" for group in FACTOR_GROUPS]
)

SEVERITY_FEATURES = CONTEXT_FEATURES + CRASH_ATTRIBUTE_FEATURES

CLASS_NAMES = ["property_damage_only", "injury_likely", "fatal_risk"]

_TRUCK_HINTS = (
    "truck",
    "dump",
    "flat bed",
    "garbage",
    "refuse",
    "tanker",
    "tractor",
    "cement",
    "crane",
    "tow",
    "chassis",
    "carry all",
    "pk",
)

_EMPTY_TOKENS = {"", "nan", "none", "null", "unspecified", "unknown", "not applicable"}


def _norm(value: object) -> str:
    """Lower-case, collapse whitespace, and treat NA/placeholder values as empty."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = re.sub(r"\s+", " ", str(value).strip().lower())
    return "" if text in _EMPTY_TOKENS else text


@lru_cache(maxsize=4096)
def vehicle_groups(raw: object) -> frozenset[str]:
    """Map a raw ``vehicle_type_code`` value to normalised groups."""
    text = _norm(raw)
    if not text:
        return frozenset()

    groups: set[str] = set()

    if "e-bike" in text or "ebike" in text or "e bike" in text:
        groups.update({"bicycle", "ebike"})
    elif "bike" in text or "bicycle" in text:
        groups.add("bicycle")

    if "scooter" in text or "moped" in text or "motor scooter" in text:
        groups.add("scooter")

    if "motorcycle" in text:
        groups.add("motorcycle")

    if "bus" in text:
        groups.add("bus")

    if "taxi" in text:
        groups.add("taxi")

    if "van" in text:
        groups.add("van")

    if "sport utility" in text or "suv" in text or "station wagon" in text:
        groups.add("suv")

    if "sedan" in text or "convertible" in text or "coupe" in text:
        groups.add("sedan")

    if any(hint in text for hint in _TRUCK_HINTS):
        groups.add("truck")

    if not groups:
        groups.add("other")

    return frozenset(groups)


@lru_cache(maxsize=4096)
def factor_groups(raw: object) -> frozenset[str]:
    """Map a raw ``contributing_factor_vehicle_N`` value to normalised groups."""
    text = _norm(raw)
    if not text:
        return frozenset()

    groups: set[str] = set()

    if "speed" in text:
        groups.add("unsafe_speed")
    if "alcohol" in text or "drinking" in text:
        groups.add("alcohol")
    if "inattention" in text or "distraction" in text:
        groups.add("distraction")
    if "yield" in text:
        groups.add("failure_to_yield")
    if "too closely" in text:
        groups.add("following_too_closely")
    if "lane" in text:
        groups.add("lane_usage")
    if "traffic control" in text:
        groups.add("traffic_control")
    if "backing" in text:
        groups.add("backing")
    if "turning" in text:
        groups.add("turning")
    if "inexperience" in text:
        groups.add("inexperience")
    if "view obstructed" in text:
        groups.add("view_obstructed")
    if "aggressive" in text:
        groups.add("aggressive")
    if "slippery" in text:
        groups.add("slippery")
    if "asleep" in text or "fatigued" in text:
        groups.add("asleep")
    if "brake" in text:
        groups.add("brakes")
    if "pedestrian" in text or "bicyclist" in text:
        groups.add("pedestrian_error")

    return frozenset(groups)


def _parse_hour(series: pd.Series) -> pd.Series:
    """Parse the dataset's ``crash_time`` (``"6:30"``, ``"18:05"``) to an hour."""
    parsed = pd.to_datetime(series.astype(str).str.strip(), format="%H:%M", errors="coerce")
    hours = parsed.dt.hour.astype("float")
    # A handful of rows use a malformed time; fall back to splitting on ":".
    missing = hours.isna()
    if missing.any():
        fallback = (
            series[missing]
            .astype(str)
            .str.extract(r"^\s*(\d{1,2})", expand=False)
            .astype("float")
        )
        hours.loc[missing] = fallback.where((fallback >= 0) & (fallback <= 23))
    return hours


def _flag(series: pd.Series, groups_getter, group: str) -> pd.Series:
    return series.map(lambda value: group in groups_getter(value)).astype("int8")


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Build ``SEVERITY_FEATURES`` for a raw crash dataframe.

    Vectorised over the whole frame; used by training.
    """
    out = pd.DataFrame(index=df.index)

    hours = _parse_hour(df["crash_time"]).fillna(12).astype(int)
    dates = pd.to_datetime(df["crash_date"], errors="coerce")

    out["hour"] = hours.astype("int8")
    out["is_weekend"] = (dates.dt.dayofweek >= 5).astype("int8")
    out["month"] = dates.dt.month.fillna(1).astype("int8")

    engineered = hours.map(time_features)
    out["is_night"] = engineered.map(lambda triple: triple[1]).astype("int8")
    out["is_rush_hour"] = engineered.map(lambda triple: triple[2]).astype("int8")

    out["lat"] = pd.to_numeric(df["latitude"], errors="coerce")
    out["lon"] = pd.to_numeric(df["longitude"], errors="coerce")

    borough = df["borough"].astype(str).str.strip().str.upper()
    out["borough_code"] = borough.map(BOROUGH_CODES).fillna(0).astype("int8")

    vehicle_columns = [c for c in ("vehicle_type_code1", "vehicle_type_code2") if c in df]
    factor_columns = [c for c in df.columns if c.startswith("contributing_factor_vehicle_")]

    if vehicle_columns:
        vehicle_values = [df[c].fillna("") for c in vehicle_columns]
        present = sum((values.map(_norm) != "").astype("int8") for values in vehicle_values)
        out["n_vehicles"] = present.astype("int8")
        for group in VEHICLE_GROUPS:
            flags = None
            for values in vehicle_values:
                current = _flag(values, vehicle_groups, group)
                flags = current if flags is None else (flags | current)
            out[f"is_{group}"] = flags.astype("int8")
        out["is_other"] = out["is_other"].astype("int8")
    else:
        out["n_vehicles"] = 0
        for group in VEHICLE_GROUPS:
            out[f"is_{group}"] = 0

    if factor_columns:
        factor_values = [df[c].fillna("") for c in factor_columns]
        out["n_factors"] = sum(
            (values.map(_norm) != "").astype("int8") for values in factor_values
        ).astype("int8")
        for group in FACTOR_GROUPS:
            flags = None
            for values in factor_values:
                current = _flag(values, factor_groups, group)
                flags = current if flags is None else (flags | current)
            out[f"factor_{group}"] = flags.astype("int8")
    else:
        out["n_factors"] = 0
        for group in FACTOR_GROUPS:
            out[f"factor_{group}"] = 0

    return out[SEVERITY_FEATURES]


def build_severity_labels(df: pd.DataFrame) -> pd.Series:
    """0 = property damage only, 1 = injury, 2 = fatality.

    A crash with any fatality is ``fatal_risk`` even when people were also
    injured. Injury/fatality counts are the label source only - never features.
    """
    injured = pd.to_numeric(df.get("number_of_persons_injured"), errors="coerce").fillna(0)
    killed = pd.to_numeric(df.get("number_of_persons_killed"), errors="coerce").fillna(0)

    labels = pd.Series(0, index=df.index, dtype="int8")
    labels[injured > 0] = 1
    labels[killed > 0] = 2
    return labels


def build_row_from_parts(
    *,
    hour: int,
    month: int,
    is_weekend: int,
    lat: float,
    lon: float,
    borough: str | None,
    vehicles: Iterable[object],
    factors: Iterable[object],
) -> dict[str, float]:
    """Build one feature row for serving, from a described crash scenario.

    ``vehicles`` and ``factors`` are raw strings (``"Bike"``, ``"Unsafe Speed"``)
    or empty values. Unknown values map to the ``other`` / no-flag columns
    rather than raising, because the caller is a public API.
    """
    _, is_night, is_rush_hour = time_features(hour)

    vehicle_list = [v for v in vehicles if _norm(v)]
    factor_list = [f for f in factors if _norm(f)]

    row: dict[str, float] = {
        "hour": float(hour),
        "is_weekend": float(is_weekend),
        "month": float(month),
        "is_night": float(is_night),
        "is_rush_hour": float(is_rush_hour),
        "lat": float(lat),
        "lon": float(lon),
        "borough_code": float(BOROUGH_CODES.get((borough or "").strip().upper(), 0)),
        "n_vehicles": float(len(vehicle_list)),
        "n_factors": float(len(factor_list)),
    }

    for group in VEHICLE_GROUPS:
        row[f"is_{group}"] = float(
            any(group in vehicle_groups(value) for value in vehicle_list)
        )
    for group in FACTOR_GROUPS:
        row[f"factor_{group}"] = float(
            any(group in factor_groups(value) for value in factor_list)
        )

    return row


def in_nyc_bbox(lat: pd.Series, lon: pd.Series) -> pd.Series:
    lat_min, lat_max, lon_min, lon_max = NYC_BBOX
    return lat.between(lat_min, lat_max) & lon.between(lon_min, lon_max)
