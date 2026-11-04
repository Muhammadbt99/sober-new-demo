"""Serving the global crash model for regions without a crash model of their own.

A region (the UAE first) is a table of H3 resolution-8 cells with the same
OpenStreetMap and population features the model was trained on. For a cell
and an hour of the week the model gives a *relative* injury-crash rate (1.0 =
the average cell-hour of a training city). The score uses its percentile among
every cell and hour of the week in the region, exactly as the New York model
uses its percentile among New York's, so a score of 60/60 means "among the
riskiest 1% of places and hours in the UAE", never a crash count.
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

import h3
import joblib
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS = os.path.join(HERE, "artifacts")
MODEL_FILE = "global_crash_model.joblib"
METADATA_FILE = "global_crash_metadata.json"
RESOLUTION = 8
CELL_SPACING_KM = 0.80
REFERENCE_QUANTILES = 10_001
#: From this percentile a place is CRITICAL on crash risk alone (UAE only, a
#: decision of 2026-11-04: see ``score_total``).
CRASH_ONLY_CRITICAL_PERCENTILE = 99.0
CRITICAL_SCORE = 75.0

#: The percentile reference is the region's street-grid hexes, the counterpart
#: of New York's (all urban). Against every UAE hex, most of them desert, every
#: city street landed in the top 2% at every hour.
REFERENCE_MIN_JUNCTIONS = 5
#: Slot predictions kept per region (a whole week is 168).
SLOT_CACHE = 168


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    country: str
    weekend_start: int  # 0 = Monday
    cells_file: str
    reference_file: str


REGIONS = {
    "uae": Region(
        key="uae",
        name="United Arab Emirates",
        country="AE",
        weekend_start=5,
        cells_file="uae_cells.csv.gz",
        reference_file="uae_reference.npy",
    ),
}


def rel_dow(day_of_week: int, weekend_start: int) -> int:
    """Day counted from the first day of the local weekend (0) onwards."""
    return (int(day_of_week) - weekend_start) % 7


def design_rows(static: np.ndarray, hour: int, rel_day: int) -> np.ndarray:
    """Rows for every cell at one hour of the week, in the training layout."""
    count = len(static)
    time = np.column_stack(
        [
            np.full(count, hour, dtype=np.float32),
            np.full(count, rel_day, dtype=np.float32),
            np.full(count, 1.0 if rel_day < 2 else 0.0, dtype=np.float32),
        ]
    )
    return np.column_stack([static, time])


def score_total(percentile: float, model_points: float, nightlife: float) -> float:
    """The 0-100 score, with the top of the crash scale able to reach CRITICAL.

    Elsewhere CRITICAL needs nightlife on top of the model's 60. Mapped nightlife
    in the UAE is too sparse for that to mean anything (Deira's busiest hours sat
    at 58 in the 99th percentile), so here the crash record can earn it alone.
    """
    total = min(100.0, model_points + nightlife)
    # Rounded as it is shown: a place displayed at 99.0 must behave like 99.0.
    return max(total, CRITICAL_SCORE) if round(percentile, 1) >= CRASH_ONLY_CRITICAL_PERCENTILE else total


class RegionModel:
    """One region's cells, cached predictions and percentile reference."""

    def __init__(self, region: Region, table: pd.DataFrame, model, static_features: list[str]):
        self.region = region
        self.table = table
        self.model = model
        self.static_features = static_features
        self.static = table[static_features].to_numpy(dtype=np.float32)
        self.index = {cell: i for i, cell in enumerate(table.index)}
        self.lat = table["lat"].to_numpy(dtype=np.float64)
        self.lon = table["lon"].to_numpy(dtype=np.float64)
        #: Mapped nightlife venues in each cell and its six neighbours (~1 km).
        self.bars = (table["nightlife"] + table["ring_nightlife"]).to_numpy(dtype=np.int64)
        self.reference: np.ndarray | None = None
        self._slots: "OrderedDict[tuple[int, int], np.ndarray]" = OrderedDict()
        self._lock = threading.Lock()
        self._week_means: dict[int, float] = {}
        # For reasons: where a cell sits among the region's cells, per feature.
        self._sorted = {
            name: np.sort(table[name].to_numpy(dtype=np.float64))
            for name in ("junctions", "signals", "population", "ring_population", "nightlife", "ring_nightlife")
            if name in table
        }

    # -- predictions --------------------------------------------------------

    def slot(self, hour: int, rel_day: int) -> np.ndarray:
        key = (int(hour), int(rel_day))
        with self._lock:
            cached = self._slots.get(key)
            if cached is not None:
                self._slots.move_to_end(key)
                return cached
        rates = np.clip(self.model.predict(design_rows(self.static, hour, rel_day)), 1e-9, None).astype(np.float32)
        with self._lock:
            self._slots[key] = rates
            if len(self._slots) > SLOT_CACHE:
                self._slots.popitem(last=False)
        return rates

    def weeks(self, rows: list[int]) -> np.ndarray:
        """Each cell's rate at every hour of the week (cells x 7 x 24)."""
        static = np.repeat(self.static[rows], 168, axis=0)
        rel_day = np.tile(np.repeat(np.arange(7), 24), len(rows)).astype(np.float32)
        hour = np.tile(np.arange(24), 7 * len(rows)).astype(np.float32)
        design = np.column_stack([static, hour, rel_day, (rel_day < 2).astype(np.float32)])
        return np.clip(self.model.predict(design), 1e-9, None).reshape(len(rows), 7, 24)

    def week(self, row: int) -> np.ndarray:
        """One cell's rate at every hour of the week (7 x 24)."""
        return self.weeks([row])[0]

    def prepare_week_means(self, rows: list[int]) -> None:
        """Predict many cells' weeks in one batch.

        One model call costs ~70 ms however few rows it has, so a route of 700
        cells took 50 s one cell at a time and takes well under a second here.
        """
        missing = sorted({row for row in rows if row not in self._week_means})
        if missing:
            means = self.weeks(missing).mean(axis=(1, 2))
            self._week_means.update(zip(missing, map(float, means)))

    def week_mean(self, row: int) -> float:
        self.prepare_week_means([row])
        return self._week_means[row]

    def reference_rows(self) -> np.ndarray:
        """The cells the percentile is taken among: the published ones, else street grids."""
        if "reference" in self.table:
            return self.table["reference"].to_numpy() > 0
        return self.table["junctions"].to_numpy() >= REFERENCE_MIN_JUNCTIONS

    def build_reference(self) -> np.ndarray:
        """Quantiles of the rate over every reference cell and hour of the week."""
        rows = self.reference_rows()
        rates = np.concatenate(
            [self.slot(hour, day)[rows] for day in range(7) for hour in range(24)]
        ).astype(np.float64)
        return np.quantile(rates, np.linspace(0.0, 1.0, REFERENCE_QUANTILES)).astype(np.float64)

    def percentile(self, rate: float | np.ndarray) -> float | np.ndarray:
        reference = self.reference
        if reference is None:
            raise RuntimeError("region reference not loaded")
        position = np.searchsorted(reference, rate, side="right")
        return np.clip(100.0 * position / len(reference), 0.0, 100.0)

    def rank_among_cells(self, name: str, value: float) -> float | None:
        values = self._sorted.get(name)
        if values is None or len(values) == 0:
            return None
        return float(100.0 * np.searchsorted(values, value, side="right") / len(values))

    # -- cells ----------------------------------------------------------------

    def row_for(self, lat: float, lon: float) -> int | None:
        """The region cell of a point; a point just off the table borrows a neighbour."""
        cell = h3.latlng_to_cell(lat, lon, RESOLUTION)
        row = self.index.get(cell)
        if row is not None:
            return row
        best, best_people = None, -1.0
        for neighbour in h3.grid_ring(cell, 1):
            candidate = self.index.get(neighbour)
            if candidate is None:
                continue
            people = float(self.table.iloc[candidate]["population"])
            if people > best_people:
                best, best_people = candidate, people
        return best

    def rows_near(self, lat: float, lon: float, radius_km: float) -> list[int]:
        centre = h3.latlng_to_cell(lat, lon, RESOLUTION)
        # Neighbouring resolution-8 centres are about 0.80 km apart.
        rings = max(1, int(round(radius_km / CELL_SPACING_KM)))
        return sorted(
            row for row in (self.index.get(cell) for cell in h3.grid_disk(centre, rings)) if row is not None
        )


class GlobalModel:
    """Loads the approved global model and every region that has a table."""

    def __init__(self, directory: str = ARTIFACTS):
        self.directory = directory
        self.available = False
        self.error: str | None = None
        self.metadata: dict = {}
        self.model = None
        self.regions: dict[str, RegionModel] = {}
        self._load()

    def _path(self, name: str) -> str:
        return os.path.join(self.directory, name)

    def _load(self) -> None:
        try:
            with open(self._path(METADATA_FILE), encoding="utf-8") as handle:
                self.metadata = json.load(handle)
            if self.metadata.get("status") != "approved":
                raise RuntimeError(f"global model status is {self.metadata.get('status')!r}")
            self.model = joblib.load(self._path(MODEL_FILE))
            static = list(self.metadata["static_features"])
            for key, region in REGIONS.items():
                path = self._path(region.cells_file)
                if not os.path.exists(path):
                    continue
                table = pd.read_csv(path, index_col="cell")
                region_model = RegionModel(region, table, self.model, static)
                reference = self._path(region.reference_file)
                if os.path.exists(reference):
                    region_model.reference = np.load(reference)
                else:
                    region_model.reference = region_model.build_reference()
                self.regions[key] = region_model
            if not self.regions:
                raise RuntimeError("no region tables")
            self.available = True
        except FileNotFoundError as exc:
            self.error = f"not built: {exc.filename and os.path.basename(exc.filename)}"
        except Exception as exc:  # noqa: BLE001 - a missing model is a degraded start, not a crash
            self.error = f"{type(exc).__name__}: {exc}"
            self.available = False

    def warm_up(self, hours: list[tuple[int, int]] | None = None) -> None:
        """Predict a few whole-region slots now, so the first map or score is instant.

        ``hours`` are (hour, day of week) pairs; by default the demo's Saturday
        23:00 and the server's current hour.
        """
        if not self.available:
            return
        from datetime import datetime

        now = datetime.now()
        for hour, day in hours or [(23, 5), (now.hour, now.weekday())]:
            for region in self.regions.values():
                region.slot(hour, rel_dow(day, region.region.weekend_start))

    @property
    def version(self) -> str | None:
        return self.metadata.get("version")

    def prepare_points(self, points: list[tuple[float, float]]) -> None:
        """Batch the per-cell work for points about to be scored one at a time."""
        if not self.available:
            return
        rows: dict[str, list[int]] = {}
        for lat, lon in points:
            found = self.region_at(lat, lon)
            if found is not None:
                rows.setdefault(found[0].region.key, []).append(found[1])
        for key, region_rows in rows.items():
            self.regions[key].prepare_week_means(region_rows)

    def region_at(self, lat: float, lon: float) -> tuple[RegionModel, int] | None:
        if not self.available:
            return None
        for region in self.regions.values():
            row = region.row_for(lat, lon)
            if row is not None:
                return region, row
        return None

    def score(self, lat: float, lon: float, hour: int, day_of_week: int, *, road: dict | None = None) -> dict | None:
        """A risk answer in the shape of ``RiskModel.score``, or None outside every region.

        Nightlife comes from the same OpenStreetMap extract as the features
        (venues in the cell and its six neighbours, about 1 km), so a region's
        score never waits on, or changes with, a live venue lookup.
        """
        found = self.region_at(lat, lon)
        if found is None:
            return None
        region, row = found
        return self._answer(region, row, int(hour), int(day_of_week), road=road)

    def _answer(self, region: RegionModel, row: int, hour: int, day_of_week: int, *, road: dict | None = None) -> dict:
        from context_model import CELL_AREA_KM2, REFERENCE_RATE
        from crash_model.model import action_for_band, nightlife_points, points_from_percentile, risk_band

        rel_day = rel_dow(day_of_week, region.region.weekend_start)
        rate = float(region.slot(hour, rel_day)[row])
        percentile = float(region.percentile(rate))
        cells = region.table
        record = cells.iloc[row]
        bars = int(region.bars[row])
        model_points = round(points_from_percentile(percentile), 1)
        night = nightlife_points(bars)
        total = score_total(percentile, model_points, night)
        level = risk_band(total)
        week_mean = region.week_mean(row)
        time_ratio = rate / week_mean if week_mean > 0 else 1.0
        expected = REFERENCE_RATE * rate
        return {
            "cell": cells.index[row],
            "coverage": "context",
            "coverage_reason": "global_model",
            "model_available": True,
            "model_version": self.version,
            "expected_rate": expected,
            "expected_rate_per_km2_hour": expected / CELL_AREA_KM2,
            "percentile": round(percentile, 1),
            "model_points": model_points,
            "nightlife_points": night,
            "risk_score": round(total, 1),
            "risk_level": level,
            "action": action_for_band(level),
            "reasons": (
                [f"In the top {100 - CRASH_ONLY_CRITICAL_PERCENTILE:.0f}% of crash risk here: "
                 f"at least {CRITICAL_SCORE:.0f} on crash risk alone"]
                if round(percentile, 1) >= CRASH_ONLY_CRITICAL_PERCENTILE
                else []
            ) + self._reasons(region, record, hour, day_of_week, time_ratio, bars, night),
            "cell_area_km2": CELL_AREA_KM2,
            "bars": bars,
            "context": {
                "method": "global_model",
                "region": region.region.key,
                "region_name": region.region.name,
                "country": region.region.country,
                "country_name": region.region.name,
                "road_class": (road or {}).get("class"),
                "road_name": (road or {}).get("name"),
                "relative_rate": round(rate, 4),
                "time_ratio": round(time_ratio, 2),
                "index": round(rate, 4),
                "maneuvers": [],
                "trained_on": self.training_summary(),
                "basis": (
                    "global crash model trained on five cities and Dubai; "
                    "ranked against every hour of the hexes Dubai Police record"
                ),
            },
        }

    def _reasons(self, region: RegionModel, record, hour: int, day: int, time_ratio: float, bars: int, night: int) -> list[str]:
        """Only what the served model reads (the hour, junctions, roads, people), plus
        the nightlife points, which are counted separately."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        reasons = []
        if time_ratio >= 1.15 or time_ratio <= 0.85:
            reasons.append(
                f"{hour:02d}:00 {days[day]}: {time_ratio:.1f}x this place's average hour of the week"
            )
        else:
            reasons.append(f"{hour:02d}:00 {days[day]}: about this place's average hour of the week")
        junctions = int(record.get("junctions", 0))
        rank = region.rank_among_cells("junctions", junctions)
        if junctions and rank is not None:
            reasons.append(
                f"{junctions} street junctions in this hex, more than {min(99, int(rank))}% of UAE hexes"
            )
        major = float(
            record.get("km_motorway", 0) + record.get("km_trunk", 0) + record.get("km_primary", 0)
        )
        if major >= 0.3:
            reasons.append(f"{major:.1f} km of motorway, trunk or primary road in this hex")
        people = float(record.get("population", 0) + record.get("ring_population", 0))
        if people >= 100:
            reasons.append(f"About {int(round(people, -2)):,} people live in and around this hex")
        if night:
            reasons.append(f"{bars} bars, pubs or clubs mapped within about 1 km add {night} points")
        return reasons

    def surface(self, lat: float, lon: float, radius_km: float, hour: int, day_of_week: int, *, limit: int = 2500) -> dict | None:
        """Every region cell within ``radius_km`` of a point, scored for one hour."""
        found = self.region_at(lat, lon)
        if found is None:
            return None
        from crash_model.model import nightlife_points, points_from_percentile, risk_band

        region, _ = found
        rows = region.rows_near(lat, lon, radius_km)[:limit]
        rel_day = rel_dow(day_of_week, region.region.weekend_start)
        rates = region.slot(hour, rel_day)
        out = []
        table = region.table
        percentiles = region.percentile(rates[rows]) if rows else []
        for row, percentile in zip(rows, percentiles):
            cell = table.index[row]
            bars = int(region.bars[row])
            model_points = round(points_from_percentile(float(percentile)), 1)
            night = nightlife_points(bars)
            total = score_total(float(percentile), model_points, night)
            out.append(
                {
                    "cell": cell,
                    "lat": round(float(region.lat[row]), 6),
                    "lon": round(float(region.lon[row]), 6),
                    "name": None,
                    "borough": None,
                    "expected_rate": round(float(rates[row]), 6),
                    "percentile": round(float(percentile), 1),
                    "model_points": model_points,
                    "nightlife_points": night,
                    "bars": bars,
                    "risk_score": round(total, 1),
                    "risk_level": risk_band(total),
                    "coverage": "context",
                }
            )
        return {"region": region.region.key, "region_name": region.region.name, "cells": out}

    def training_summary(self) -> str:
        training = self.metadata.get("training") or {}
        names = [item["name"] for item in training.values()]
        crashes = sum(int(item["crashes"]) for item in training.values())
        if not names:
            return "other cities' crash records"
        listed = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
        return f"{crashes:,} recorded crashes in {listed}"

    def health(self) -> dict:
        gate = (self.metadata.get("gate") or {}).get("checks") or {}
        return {
            "available": self.available,
            "error": self.error,
            "version": self.version,
            "gate_passed": bool(gate.get("passed")),
            "regions": {
                key: {
                    "name": region.region.name,
                    "cells": int(len(region.table)),
                    # The hexes a score's percentile is taken among (Dubai's recorded area).
                    "reference_cells": int(region.reference_rows().sum()),
                }
                for key, region in self.regions.items()
            },
        }
