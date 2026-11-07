"""Serving surface for the crash-risk model.

One question, one answer: given a point and a local hour, what is the expected
number of injury-or-fatal crashes in that H3 res 8 cell, and how does it rank
against the reference distribution? Everything else in the API (risk, heatmap,
trend, compare, routes) must go through this module so two endpoints can never
disagree about the same cell.
"""
from __future__ import annotations

import json
import math
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date as date_type
from functools import lru_cache

import h3
import joblib
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = HERE

MODEL_FILE = "crash_risk_model.joblib"
METADATA_FILE = "crash_risk_metadata.json"
CELLS_FILE = "cell_rates.csv"
REFERENCE_FILE = "percentile_reference.npy"

BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNKNOWN"]
BOROUGH_CODE = {name: index for index, name in enumerate(BOROUGHS)}

# The model's points are the only learned component. 60 is deliberately below
# the 75-point CRITICAL threshold: geography and history alone cannot make an
# area critical without the separate nightlife component.
MODEL_MAX_POINTS = 60
NIGHTLIFE_MAX_POINTS = 40

# Nightlife bands, calibrated from the offline venue index (venues within 1 km).
BARS_MEDIUM = 13
BARS_HIGH = 50


@dataclass(frozen=True)
class ModelInfo:
    version: str
    status: str
    prior_rate: float
    cell_count: int
    gate: dict


def points_from_percentile(percentile: float) -> float:
    """Map a 0-100 percentile to the PRD's 0-60 model tiers.

    The tiers are deliberate: a score jump is explainable ("this hex crossed
    P90"), whereas a continuous ramp makes two cells one percentile apart look
    meaningfully different. Model points alone cap at 60, so CRITICAL still
    requires a nightlife contribution.
    """
    p = min(100.0, max(0.0, float(percentile)))
    if p < 50:
        return 0.0
    if p < 75:
        return 15.0
    if p < 90:
        return 30.0
    if p < 99:
        return 50.0
    return 60.0


def nightlife_points(bars: float | int | None) -> int:
    count = float(bars or 0)
    if count <= 0:
        return 0
    if count <= 5:
        return 8
    if count <= BARS_MEDIUM:
        return 16
    if count <= BARS_HIGH:
        return 28
    return NIGHTLIFE_MAX_POINTS


def risk_band(total: float) -> str:
    if total >= 75:
        return "CRITICAL"
    if total >= 50:
        return "HIGH"
    if total >= 25:
        return "MEDIUM"
    return "LOW"


def action_for_band(level: str) -> str:
    if level == "CRITICAL":
        return "Avoid driving if possible. Consider alternative transportation."
    if level == "HIGH":
        return "Drive with extreme caution. Avoid high-risk areas."
    if level == "MEDIUM":
        return "Drive with caution and stay alert."
    return "Normal driving conditions. Drive safely."


def _borough_label(raw: str | None) -> str | None:
    """"STATEN ISLAND" -> "Staten Island"; the UNKNOWN placeholder -> None."""
    text = (raw or "").strip()
    if not text or text.upper() in ("UNKNOWN", "NAN", "NONE"):
        return None
    return text.title()


@lru_cache(maxsize=16)
def _holidays(year: int) -> frozenset:
    calendar = USFederalHolidayCalendar()
    start = pd.Timestamp(year=year, month=1, day=1)
    end = pd.Timestamp(year=year, month=12, day=31)
    return frozenset(calendar.holidays(start=start, end=end))


#: Predictions kept in memory. One entry per (cell, hour, day, month, holiday,
#: era, weather); the whole surface at one hour is ~1,100 cells.
PREDICTION_CACHE_SIZE = 50_000


def _weather_key(weather: tuple | None) -> tuple | None:
    """Hashable weather for a cache key. None and NaN both mean "missing",
    exactly as ``_feature_row`` treats them, and NaN never equals itself."""
    if weather is None:
        return (None, None, None)
    return tuple(
        None
        if value is None or (isinstance(value, float) and math.isnan(value))
        else float(value)
        for value in weather
    )


class RiskModel:
    def __init__(self, directory: str = DEFAULT_DIR):
        self.directory = directory
        self.available = False
        self.error = None
        self.model = None
        self.metadata: dict = {}
        self.info: ModelInfo | None = None
        self.cells: pd.DataFrame | None = None
        self.reference: np.ndarray | None = None
        # A route comparison scores the same cells on several routes, and the
        # heatmap and trend endpoints rescore cells as the map moves. The
        # prediction depends only on the key, so a cached value is identical.
        self._predictions: "OrderedDict[tuple, float]" = OrderedDict()
        self._predictions_lock = threading.Lock()
        self._load()

    def _predict(self, key: tuple, row: dict) -> float:
        with self._predictions_lock:
            cached = self._predictions.get(key)
            if cached is not None:
                self._predictions.move_to_end(key)
                return cached
        features = self.metadata.get("features") or list(row)
        x = pd.DataFrame([{name: row[name] for name in features}])
        expected = float(np.clip(self.model.predict(x)[0], 1e-12, None))
        with self._predictions_lock:
            self._predictions[key] = expected
            if len(self._predictions) > PREDICTION_CACHE_SIZE:
                self._predictions.popitem(last=False)
        return expected

    def warm_up(self) -> None:
        """Make one prediction so first-use costs are paid now, not by a user.

        scikit-learn counts physical CPU cores on its first prediction; on
        Windows that shells out to PowerShell and takes over a second.
        """
        if self.available:
            self.score(40.758, -73.9855, 12, 2)

    def _path(self, name: str) -> str:
        return os.path.join(self.directory, name)

    def _load(self) -> None:
        try:
            with open(self._path(METADATA_FILE), encoding="utf-8") as handle:
                self.metadata = json.load(handle)
            if self.metadata.get("status") != "approved":
                raise RuntimeError(
                    f"model status is {self.metadata.get('status')!r}, not 'approved'"
                )
            gate = self.metadata.get("gate") or {}
            if not gate.get("passed"):
                raise RuntimeError("model gate has not passed")

            cells = pd.read_csv(self._path(CELLS_FILE))
            required = {"cell", "lat", "lon", "borough", "rate", "housing_density"}
            missing = required - set(cells.columns)
            if missing:
                raise RuntimeError(f"cell table missing columns: {sorted(missing)}")
            cells = cells.copy()
            cells["area_km2"] = [
                float(h3.cell_area(cell, unit="km^2")) for cell in cells["cell"]
            ]
            cells["borough_code"] = (
                cells["borough"].map(BOROUGH_CODE).fillna(BOROUGH_CODE["UNKNOWN"]).astype(int)
            )
            self.cells = cells.set_index("cell", drop=False)

            self.reference = np.load(self._path(REFERENCE_FILE))
            if self.reference.size == 0:
                raise RuntimeError("percentile reference is empty")
            self.reference = np.sort(self.reference.astype(float))

            self.model = joblib.load(self._path(MODEL_FILE))
            self.info = ModelInfo(
                version=str(self.metadata.get("version")),
                status=str(self.metadata.get("status")),
                prior_rate=float(self.metadata.get("prior_rate", 0.0)),
                cell_count=int(self.metadata.get("cell_count", len(cells))),
                gate=gate,
            )
            self.available = True
        except Exception as exc:  # noqa: BLE001 - degraded start is explicit
            self.error = f"{type(exc).__name__}: {exc}"
            self.available = False
            self.model = None
            self.cells = None
            self.reference = None
            self.info = None

    def cell_for(self, lat: float, lon: float) -> str:
        return h3.latlng_to_cell(float(lat), float(lon), 8)

    def percentile(self, rate: float) -> float:
        if self.reference is None or self.reference.size == 0:
            return 0.0
        index = int(np.searchsorted(self.reference, float(rate), side="right"))
        return min(100.0, 100.0 * index / self.reference.size)

    def _feature_row(
        self,
        cell_row: pd.Series,
        hour: int,
        day_of_week: int,
        month: int | None,
        is_holiday: int,
        is_covid: int,
        weather: tuple[float, float, float] | None,
    ) -> dict:
        temperature, rain, wind = (np.nan, np.nan, np.nan)
        if weather is not None:
            temperature, rain, wind = weather
        hour = int(hour) % 24
        day_of_week = int(day_of_week) % 7
        return {
            "hist_rate": float(cell_row["rate"]),
            "housing_density": float(cell_row["housing_density"])
            if pd.notna(cell_row["housing_density"])
            else np.nan,
            "lat": float(cell_row["lat"]),
            "lon": float(cell_row["lon"]),
            "borough_code": int(cell_row["borough_code"]),
            "hour": hour,
            "day_of_week": day_of_week,
            "month": int(month if month is not None else 10),
            "is_weekend": int(day_of_week >= 5),
            "is_holiday": int(is_holiday),
            "is_covid": int(is_covid),
            "temperature": float(temperature) if temperature is not None else np.nan,
            "rain": float(rain) if rain is not None else np.nan,
            "wind_speed": float(wind) if wind is not None else np.nan,
        }

    def score(
        self,
        lat: float,
        lon: float,
        hour: int,
        day_of_week: int,
        *,
        month: int | None = None,
        on_date: date_type | None = None,
        weather: tuple[float, float, float] | None = None,
        bars: float | int | None = None,
    ) -> dict:
        """Score one point. Never raises; an unavailable model is a stated state."""
        hour = int(hour) % 24
        day_of_week = int(day_of_week) % 7
        cell = self.cell_for(lat, lon)
        night = nightlife_points(bars)

        if not self.available:
            total = night
            return {
                "cell": cell,
                "coverage": "rule_based_only",
                "coverage_reason": "model_unavailable",
                "model_available": False,
                "model_version": None,
                "expected_rate": None,
                "expected_rate_per_km2_hour": None,
                "percentile": None,
                "model_points": 0,
                "nightlife_points": night,
                "risk_score": total,
                "risk_level": risk_band(total),
                "action": action_for_band(risk_band(total)),
                "reasons": [
                    "Crash model unavailable - no learned crash points for this location",
                ],
                "cell_area_km2": None,
            }

        cell_row = self.cells.loc[cell] if cell in self.cells.index else None
        if cell_row is None:
            total = night
            return {
                "cell": cell,
                "coverage": "rule_based_only",
                "coverage_reason": "cell_outside_training_coverage",
                "model_available": True,
                "model_version": self.info.version if self.info else None,
                "expected_rate": None,
                "expected_rate_per_km2_hour": None,
                "percentile": None,
                "model_points": 0,
                "nightlife_points": night,
                "risk_score": total,
                "risk_level": risk_band(total),
                "action": action_for_band(risk_band(total)),
                "reasons": [
                    "No training coverage for this hex - no learned crash points",
                ],
                "cell_area_km2": None,
            }

        score_date = on_date or date_type.today()
        month_value = month if month is not None else score_date.month
        is_holiday = 1 if pd.Timestamp(score_date).normalize() in _holidays(score_date.year) else 0
        is_covid = 1 if score_date < date_type(2022, 1, 1) else 0
        row = self._feature_row(
            cell_row, hour, day_of_week, month_value, is_holiday, is_covid, weather
        )
        expected = self._predict(
            (cell, hour, day_of_week, month_value, is_holiday, is_covid, _weather_key(weather)),
            row,
        )
        percentile = self.percentile(expected)
        model_points = round(points_from_percentile(percentile), 1)
        total = min(100.0, model_points + night)
        level = risk_band(total)
        rate_per_km2 = expected / float(cell_row["area_km2"])

        reasons = [
            f"{expected:.5f} expected injury/fatal crashes per cell-hour "
            f"({percentile:.0f}th percentile)",
            f"{rate_per_km2:.4f} expected injury/fatal crashes per km2 per hour",
        ]
        if model_points >= 45:
            reasons.append("Top-decile learned crash risk for this hex and hour")
        elif model_points >= 30:
            reasons.append("Above-average learned crash risk for this hex and hour")
        elif model_points >= 15:
            reasons.append("Moderate learned crash risk for this hex and hour")
        else:
            reasons.append("Below-median learned crash risk for this hex and hour")
        if night:
            reasons.append(f"Nightlife density contributes {night} of 40 points")
        if cell_row.get("borough") == "UNKNOWN":
            reasons.append("Borough label unavailable for this hex")

        return {
            "cell": cell,
            "coverage": "model",
            "coverage_reason": None,
            "model_available": True,
            "model_version": self.info.version if self.info else None,
            "expected_rate": expected,
            "expected_rate_per_km2_hour": rate_per_km2,
            "percentile": percentile,
            "model_points": model_points,
            "nightlife_points": night,
            "risk_score": round(total, 1),
            "risk_level": level,
            "action": action_for_band(level),
            "reasons": reasons,
            "cell_area_km2": float(cell_row["area_km2"]),
        }

    def score_cells(
        self,
        hour: int,
        day_of_week: int,
        *,
        month: int | None = None,
        on_date: date_type | None = None,
        weather: tuple[float, float, float] | None = None,
        bars: "np.ndarray | list[float] | None" = None,
    ) -> list[dict]:
        """Score every modelled cell at once: the whole NYC surface for one hour.

        One batched prediction instead of 1,120 single ones. Each cell gets the
        numbers ``score`` gives for a point at its centre with the same bar
        count, so the map and the panel cannot disagree. ``bars`` lines up with
        ``self.cells`` (nightlife venues within 1 km of each centre).
        """
        if not self.available or self.cells is None or self.model is None:
            return []
        hour = int(hour) % 24
        day_of_week = int(day_of_week) % 7
        score_date = on_date or date_type.today()
        month_value = month if month is not None else score_date.month
        is_holiday = 1 if pd.Timestamp(score_date).normalize() in _holidays(score_date.year) else 0
        is_covid = 1 if score_date < date_type(2022, 1, 1) else 0

        temperature, rain, wind = (np.nan, np.nan, np.nan)
        if weather is not None:
            temperature, rain, wind = (np.nan if value is None else float(value) for value in weather)

        cells = self.cells
        count = len(cells)
        frame = pd.DataFrame(
            {
                "hist_rate": cells["rate"].astype(float).to_numpy(),
                "housing_density": pd.to_numeric(cells["housing_density"], errors="coerce")
                .astype(float)
                .to_numpy(),
                "lat": cells["lat"].astype(float).to_numpy(),
                "lon": cells["lon"].astype(float).to_numpy(),
                "borough_code": cells["borough_code"].astype(int).to_numpy(),
                "hour": np.full(count, hour),
                "day_of_week": np.full(count, day_of_week),
                "month": np.full(count, int(month_value)),
                "is_weekend": np.full(count, int(day_of_week >= 5)),
                "is_holiday": np.full(count, int(is_holiday)),
                "is_covid": np.full(count, int(is_covid)),
                "temperature": np.full(count, temperature, dtype=float),
                "rain": np.full(count, rain, dtype=float),
                "wind_speed": np.full(count, wind, dtype=float),
            }
        )
        features = self.metadata.get("features") or list(frame.columns)
        expected = np.clip(self.model.predict(frame[features]), 1e-12, None).astype(float)

        # Seed the single-point cache so a click on any cell is instant and
        # provably the same number the surface showed.
        weather_key = _weather_key(weather)
        with self._predictions_lock:
            for cell, value in zip(cells.index, expected):
                key = (cell, hour, day_of_week, month_value, is_holiday, is_covid, weather_key)
                self._predictions[key] = float(value)
                self._predictions.move_to_end(key)
            while len(self._predictions) > PREDICTION_CACHE_SIZE:
                self._predictions.popitem(last=False)

        reference = self.reference
        index = np.searchsorted(reference, expected, side="right")
        percentile = np.minimum(100.0, 100.0 * index / reference.size)
        model_points = np.select(
            [percentile < 50, percentile < 75, percentile < 90, percentile < 99],
            [0.0, 15.0, 30.0, 50.0],
            60.0,
        )
        bar_counts = np.zeros(count) if bars is None else np.asarray(bars, dtype=float)
        night = np.asarray([nightlife_points(value) for value in bar_counts], dtype=float)
        totals = np.minimum(100.0, model_points + night)
        areas = cells["area_km2"].astype(float).to_numpy()
        lats = cells["lat"].astype(float).to_numpy()
        lons = cells["lon"].astype(float).to_numpy()
        names = cells["nta_name"].tolist() if "nta_name" in cells.columns else [None] * count
        boroughs = cells["borough"].astype(str).tolist()

        surface = []
        for i, cell in enumerate(cells.index):
            total = float(totals[i])
            level = risk_band(total)
            name = names[i]
            surface.append(
                {
                    "cell": cell,
                    "lat": round(float(lats[i]), 6),
                    "lon": round(float(lons[i]), 6),
                    "name": None if name is None or pd.isna(name) else str(name),
                    "borough": _borough_label(boroughs[i]),
                    "expected_rate": float(expected[i]),
                    "expected_rate_per_km2_hour": float(expected[i] / areas[i]),
                    "percentile": float(percentile[i]),
                    "model_points": round(float(model_points[i]), 1),
                    "nightlife_points": int(night[i]),
                    "bars": int(bar_counts[i]),
                    "risk_score": round(total, 1),
                    "risk_level": level,
                    "coverage": "model",
                }
            )
        return surface

    def cell_info(self, cell: str | None) -> dict | None:
        """Neighbourhood (2020 NTA) and borough of a modelled cell, or None."""
        if not cell or self.cells is None or cell not in self.cells.index:
            return None
        row = self.cells.loc[cell]
        name = row.get("nta_name") if "nta_name" in self.cells.columns else None
        return {
            "name": None if name is None or pd.isna(name) else str(name),
            "borough": _borough_label(str(row.get("borough"))),
        }

    def nearest_cell_info(self, lat: float, lon: float, max_km: float = 1.2) -> dict | None:
        """The closest modelled cell's neighbourhood, if one is within ``max_km``.

        Names a river, park or pier next to the city ("Near Hudson Yards") rather
        than leaving it as bare coordinates.
        """
        if self.cells is None or len(self.cells) == 0:
            return None
        lats = self.cells["lat"].to_numpy(dtype=float)
        lons = self.cells["lon"].to_numpy(dtype=float)
        scale = math.cos(math.radians(float(lat)))
        dist_km = 111.32 * np.hypot(lats - float(lat), (lons - float(lon)) * scale)
        nearest = int(np.argmin(dist_km))
        if dist_km[nearest] > max_km:
            return None
        info = self.cell_info(self.cells.index[nearest])
        if info is None or not info.get("name"):
            return None
        return {**info, "distance_km": round(float(dist_km[nearest]), 2)}

    def health(self) -> dict:
        return {
            "available": self.available,
            "error": self.error,
            "version": self.info.version if self.info else None,
            "status": self.info.status if self.info else None,
            "cells": self.info.cell_count if self.info else 0,
            "gate_passed": bool(self.info.gate.get("passed")) if self.info else False,
        }

    def model_card(self) -> dict:
        """The validation numbers the Method screen shows, read from the artifact.

        Nothing here is typed into the UI, so a retrain cannot leave the page
        quoting the previous model.
        """
        meta = self.metadata
        fair_all = (meta.get("baseline") or {}).get("fair") or {}

        def split(name: str) -> dict:
            model = (meta.get("model_metrics") or {}).get(name) or {}
            baseline = (meta.get("baseline") or {}).get(name) or {}
            # The fair comparison (baseline tuned on validation, both sides refit on
            # train+validation, weighted deciles) replaces the older numbers when present.
            fair = fair_all.get(name) or {}
            if fair:
                baseline_deviance = fair.get("poisson_deviance")
                baseline_lift = fair.get("top_decile_lift")
                top_lift = fair.get("model_top_decile_lift")
            else:
                baseline_deviance = baseline.get("poisson_deviance")
                baseline_lift = baseline.get("top_decile_lift")
                top_lift = model.get("top_decile_lift")
            # Deciles and their monotonic flag must come from the same table as the lift.
            rows = fair.get("model_calibration") if fair else None
            if rows is None:
                rows = model.get("calibration") or []
                monotonic = model.get("monotonic_deciles")
            else:
                observed = [row.get("observed_rate") or 0.0 for row in rows]
                monotonic = all(
                    observed[i] <= observed[i + 1] * 1.05 for i in range(len(observed) - 1)
                )
            return {
                "poisson_deviance": model.get("poisson_deviance"),
                "baseline_deviance": baseline_deviance,
                "top_decile_lift": top_lift,
                "baseline_top_decile_lift": baseline_lift,
                "monotonic_deciles": monotonic,
                "deciles": [
                    {
                        "decile": row.get("decile"),
                        "predicted_rate": row.get("predicted_rate"),
                        "observed_rate": row.get("observed_rate"),
                        "lift": row.get("lift"),
                    }
                    for row in rows
                ],
            }

        window = meta.get("training_window") or {}
        return {
            "version": meta.get("version"),
            "created_at": meta.get("created_at"),
            "target": meta.get("target"),
            "gate": meta.get("gate"),
            "training_window": {
                "start": window.get("start"),
                "end": window.get("end"),
                "crashes": window.get("crashes"),
            },
            "holdout_borough": (meta.get("split") or {}).get("holdout_borough"),
            "temporal_test": split("temporal_test"),
            "holdout": split("holdout"),
            "cells": meta.get("cell_count"),
            "prior_rate": meta.get("prior_rate"),
        }


risk_model = RiskModel()
