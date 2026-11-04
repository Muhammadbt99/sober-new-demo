"""Danger types: *what kind* of injury crash is over-represented here, now.

The crash model says how many injury crashes to expect in a cell at an hour.
This module says what kind they tend to be: impaired, drowsy, distracted or
speeding drivers, or drivers failing to yield.

Target, not feature
-------------------
Contributing factors are recorded by police *after* a crash, so the PRD forbids
them as model inputs, and the crash model never sees them. Here they are the
*thing being estimated*: the share of injury crashes in an area and time band
whose recorded factors include a group. Every input is known before a trip:
the place (its neighbourhood or borough) and the hour of the week. Nothing
about a crash that has not happened is used.

    expected rate of a type = crash-model rate(cell, hour) x share(area, band)

Sparsity
--------
Impaired and drowsy crashes are rare (about 1% and 0.25% of injury crashes),
far too rare for 1,120 cells x 168 hours. Shares are pooled over six bands of
the week and over areas, and each small sample is shrunk toward the next level
up (neighbourhood -> borough -> citywide for the band) with empirical-Bayes
pseudo-counts tuned on a validation year. Which level a group is modelled at is
chosen on validation data, and the choice is scored once on a held-out year
(``train_danger_types.py``). A group that fails its gate ships switched off.

Recorded factors
----------------
About half of all factor entries are "Unspecified", so these are shares of
crashes with the factor *recorded*. They rank places and hours; they are not
the true prevalence of drunk or tired driving.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACT_FILE = "danger_types.json"

#: Danger groups and the NYC contributing-factor values that belong to each.
#: Matching is case-insensitive on the trimmed value.
GROUPS: dict[str, dict] = {
    "impaired": {
        "label": "Impaired driving",
        "short": "Impaired",
        "describe": "alcohol or drugs",
        "factors": ["Alcohol Involvement", "Drugs (illegal)"],
    },
    "drowsy": {
        "label": "Drowsy driving",
        "short": "Drowsy",
        "describe": "a driver asleep or fatigued",
        "factors": ["Fell Asleep", "Fatigued/Drowsy"],
    },
    "distracted": {
        "label": "Distracted driving",
        "short": "Distracted",
        "describe": "inattention, a phone or another distraction",
        "factors": [
            "Driver Inattention/Distraction",
            "Cell Phone (hand-Held)",
            "Cell Phone (hand-held)",
            "Cell Phone (hands-free)",
            "Texting",
            "Using On Board Navigation Device",
            "Other Electronic Device",
            "Passenger Distraction",
            "Outside Car Distraction",
            "Eating or Drinking",
            "Listening/Using Headphones",
        ],
    },
    "speeding": {
        "label": "Speeding",
        "short": "Speeding",
        "describe": "unsafe speed",
        "factors": ["Unsafe Speed"],
    },
    "failure_to_yield": {
        "label": "Failure to yield",
        "short": "Not yielding",
        "describe": "a driver failing to yield right of way",
        "factors": ["Failure to Yield Right-of-Way"],
    },
}
GROUP_IDS = list(GROUPS)
FACTOR_TO_GROUP = {
    factor.strip().lower(): group for group, spec in GROUPS.items() for factor in spec["factors"]
}

#: Six bands of the week (day 0 = Monday). Late nights belong to the night
#: they started on: Saturday 02:00 is Friday's weekend night.
BANDS: dict[str, str] = {
    "weekday_morning": "Weekday morning (Mon-Fri 06:00-11:59)",
    "weekday_afternoon": "Weekday afternoon (Mon-Fri 12:00-17:59)",
    "weekday_evening": "Weekday evening (Mon-Fri 18:00-19:59, Sun-Thu 20:00-23:59)",
    "weekday_late": "Weekday small hours (Mon-Fri 00:00-05:59)",
    "weekend_day": "Weekend day (Sat-Sun 06:00-19:59)",
    "weekend_night": "Weekend night (Fri-Sat 20:00-23:59, Sat-Sun 00:00-05:59)",
}
BAND_IDS = list(BANDS)

#: Below this many expected recorded crashes of a type in the data behind an
#: estimate, no multiplier is shown ("not enough data").
MIN_EXPECTED_EVENTS = 5.0
#: A level counts as the evidence when its own data carries at least this
#: weight against the level above it.
EVIDENCE_WEIGHT = 0.5
#: Central interval reported around each share.
INTERVAL = 0.8


def band_for(hour: int, day: int) -> str:
    """The band of the week for a local hour (0-23) and day (0 = Monday)."""
    hour, day = int(hour) % 24, int(day) % 7
    weekend_day = day >= 5
    if hour < 6:
        # Saturday and Sunday small hours are the tail of Friday and Saturday night.
        return "weekend_night" if weekend_day else "weekday_late"
    if hour < 12:
        return "weekend_day" if weekend_day else "weekday_morning"
    if hour < 18:
        return "weekend_day" if weekend_day else "weekday_afternoon"
    if hour < 20:
        return "weekend_day" if weekend_day else "weekday_evening"
    return "weekend_night" if day in (4, 5) else "weekday_evening"


def groups_in(values) -> set[str]:
    """The danger groups named by a crash's contributing-factor values."""
    found = set()
    for value in values:
        if isinstance(value, str):
            group = FACTOR_TO_GROUP.get(value.strip().lower())
            if group:
                found.add(group)
    return found


def shrink(k: float, n: float, prior: float, strength: float) -> float:
    """Empirical-Bayes share: ``k`` of ``n`` pulled toward ``prior`` by ``strength``."""
    if strength == math.inf:
        return prior
    return (k + strength * prior) / (n + strength) if n + strength > 0 else prior


def _beta_interval(alpha: float, beta: float, level: float = INTERVAL) -> tuple[float, float]:
    """Central interval of a Beta(alpha, beta) distribution."""
    from scipy.stats import beta as beta_dist  # local: only needed at answer time

    tail = (1.0 - level) / 2.0
    alpha, beta = max(alpha, 1e-6), max(beta, 1e-6)
    return float(beta_dist.ppf(tail, alpha, beta)), float(beta_dist.ppf(1 - tail, alpha, beta))


@dataclass(frozen=True)
class Area:
    """Where a cell sits: its 2020 neighbourhood (NTA) and borough."""

    nta: str | None
    nta_name: str | None
    borough: str | None


class DangerTypes:
    """Answers the danger-type question for a modelled cell and hour.

    Loaded from the artifact ``train_danger_types.py`` writes. When the artifact
    is missing every answer says so; nothing is invented.
    """

    def __init__(self, directory: str = HERE):
        self.directory = directory
        self.available = False
        self.error: str | None = None
        self.meta: dict = {}
        self._load()

    def _load(self) -> None:
        path = os.path.join(self.directory, ARTIFACT_FILE)
        try:
            with open(path, encoding="utf-8") as handle:
                self.meta = json.load(handle)
            self.version = str(self.meta["version"])
            self.groups = self.meta["groups"]
            self.city = self.meta["counts"]["city"]
            self.borough = self.meta["counts"]["borough"]
            self.nta = self.meta["counts"]["nta"]
            self.available = True
        except Exception as exc:  # noqa: BLE001 - a missing artifact is a stated state
            self.error = f"{type(exc).__name__}: {exc}"
            self.available = False

    # -- counts ---------------------------------------------------------------

    @staticmethod
    def _counts(table: dict, key: str | None, band: str, group: str) -> tuple[float, float]:
        entry = (table.get(key) or {}).get(band) if key else None
        if not entry:
            return 0.0, 0.0
        return float(entry["n"]), float(entry["k"].get(group, 0))

    def _city_band(self, band: str, group: str) -> tuple[float, float]:
        entry = self.city["bands"][band]
        return float(entry["n"]), float(entry["k"][group])

    def city_share(self, group: str) -> float:
        total = self.city["all"]
        return float(total["k"][group]) / float(total["n"])

    # -- answers --------------------------------------------------------------

    def group_estimate(
        self, group: str, area: Area, band: str, *, with_interval: bool = True
    ) -> dict:
        """Share of injury crashes of ``group`` here in ``band``, with its evidence.

        ``with_interval=False`` skips the Beta interval, for whole-city layers.
        """
        spec = self.groups[group]
        level = spec["level"]  # "time", "borough" or "neighbourhood"
        m_borough = float(spec["m_borough"]) if spec.get("m_borough") is not None else math.inf
        m_nta = float(spec["m_nta"]) if spec.get("m_nta") is not None else math.inf

        n_city, k_city = self._city_band(band, group)
        city_band = (k_city + 0.5) / (n_city + 1.0)

        n_b, k_b = self._counts(self.borough, area.borough, band, group)
        n_n, k_n = self._counts(self.nta, area.nta, band, group)

        if level == "time" or area.borough is None:
            share, alpha, beta = city_band, k_city + 0.5, n_city - k_city + 0.5
            evidence, evidence_n = "citywide", n_city
        else:
            borough_share = shrink(k_b, n_b, city_band, m_borough)
            share, evidence, evidence_n = borough_share, "borough", n_b
            alpha = k_b + m_borough * city_band
            beta = n_b - k_b + m_borough * (1 - city_band)
            if level == "neighbourhood" and area.nta is not None:
                share = shrink(k_n, n_n, borough_share, m_nta)
                alpha = k_n + m_nta * borough_share
                beta = n_n - k_n + m_nta * (1 - borough_share)
                if n_n / (n_n + m_nta) >= EVIDENCE_WEIGHT:
                    evidence, evidence_n = "neighbourhood", n_n
            if evidence == "borough" and n_b / (n_b + m_borough) < EVIDENCE_WEIGHT:
                evidence, evidence_n = "citywide", n_city

        low, high = _beta_interval(alpha, beta) if with_interval else (None, None)
        expected_events = evidence_n * city_band
        return {
            "share": share,
            "share_low": low,
            "share_high": high,
            "city_band_share": city_band,
            "evidence": evidence,
            "evidence_crashes": int(evidence_n),
            "too_thin": expected_events < MIN_EXPECTED_EVENTS,
        }

    def layer_value(self, group: str, area: Area, hour: int, day: int, expected_rate: float) -> dict:
        """One cell's value on a whole-city map layer for ``group``.

        ``rate_ratio`` is the expected rate of this type here and now against a
        typical NYC cell-hour; None when the group is off or the data is thin.
        """
        spec = self.groups.get(group) or {}
        if spec.get("status") != "validated":
            return {"rate_ratio": None, "status": "not_validated"}
        estimate = self.group_estimate(group, area, band_for(hour, day), with_interval=False)
        if estimate["too_thin"]:
            return {"rate_ratio": None, "status": "too_thin"}
        return {
            "rate_ratio": round(expected_rate * estimate["share"] / float(spec["city_type_rate"]), 2),
            "share_ratio": round(estimate["share"] / self.city_share(group), 2),
            "band_share_ratio": round(estimate["share"] / estimate["city_band_share"], 2),
            "status": "ok",
        }

    def profile(
        self,
        area: Area,
        hour: int,
        day: int,
        expected_rate: float | None,
        *,
        compact: bool = False,
    ) -> dict | None:
        """The danger profile of a cell-hour, or None without the artifact.

        ``expected_rate`` is the crash model's injury crashes per cell-hour; it
        turns each share into an expected rate of that type, compared with a
        typical New York cell-hour. ``compact`` drops the explanatory fields
        (used per route segment).
        """
        if not self.available:
            return None
        band = band_for(hour, day)
        groups = []
        for group in GROUP_IDS:
            spec = self.groups[group]
            label = GROUPS[group]
            if spec.get("status") != "validated":
                groups.append({"id": group, "label": label["label"], "status": "not_validated"})
                continue
            estimate = self.group_estimate(group, area, band)
            city = self.city_share(group)
            entry = {"id": group, "label": label["label"], "short": label["short"]}
            if estimate["too_thin"]:
                entry["status"] = "too_thin"
            else:
                entry["status"] = "ok"
                entry["share_ratio"] = round(estimate["share"] / city, 2)
                # Against the city at the same time of the week: isolates the
                # place, since the band already moves every share together.
                entry["band_share_ratio"] = round(
                    estimate["share"] / estimate["city_band_share"], 2
                )
                if expected_rate is not None and spec.get("city_type_rate"):
                    entry["rate_ratio"] = round(
                        expected_rate * estimate["share"] / float(spec["city_type_rate"]), 2
                    )
            if not compact:
                entry.update(
                    {
                        "share": round(estimate["share"], 5),
                        "share_low": round(estimate["share_low"], 5),
                        "share_high": round(estimate["share_high"], 5),
                        "city_share": round(city, 5),
                        "city_band_share": round(estimate["city_band_share"], 5),
                        "evidence": estimate["evidence"],
                        "evidence_crashes": estimate["evidence_crashes"],
                        "reason": self._reason(group, entry["status"], estimate, area, band, city),
                    }
                )
            groups.append(entry)
        if compact:
            return {"band": band, "groups": groups}
        return {
            "version": self.version,
            "band": {"id": band, "label": BANDS[band]},
            "area": {
                "neighbourhood": area.nta_name,
                "borough": area.borough.title() if area.borough else None,
            },
            "basis": "recorded_contributing_factors",
            "note": (
                "Shares of injury crashes whose police-recorded factors include each "
                "group; about half of all factor entries are 'Unspecified', so these "
                "rank places and hours rather than measure true prevalence. They "
                "explain the score and never change it."
            ),
            "groups": groups,
        }

    @staticmethod
    def _reason(
        group: str, status: str, estimate: dict, area: Area, band: str, city: float
    ) -> str:
        describe = GROUPS[group]["describe"]
        when = BANDS[band].split(" (")[0].lower()
        if status == "too_thin":
            return (
                f"Not enough local crash records to say how often {describe} is involved "
                f"here on a {when}; citywide on a {when} it is "
                f"{100 * estimate['city_band_share']:.1f}% of injury crashes."
            )
        where = {
            "neighbourhood": area.nta_name or "this neighbourhood",
            "borough": area.borough.title() if area.borough else "this borough",
            "citywide": "New York",
        }[estimate["evidence"]]
        return (
            f"In {where} on a {when}, {100 * estimate['share']:.1f}% of injury crashes "
            f"had {describe} recorded (city overall {100 * city:.1f}%)."
        )
