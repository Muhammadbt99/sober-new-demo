"""Dataset construction for the crash-risk model.

Everything here is pure data preparation: no model, no Flask, no I/O beyond the
files passed in. The two rules that matter:

1. The target is injury-or-fatal crashes only.
2. The historical-rate feature for a row may only use years before that row's
   year. A leaky rate makes every metric meaningless, so the split is explicit.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import h3
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.abspath(os.path.join(HERE, ".."))

CRASHES = os.path.join(MODULE_DIR, "nyc_crashes_rich.csv.gz")
RESOLUTION = 8
BBOX = (40.4, 41.0, -74.5, -73.5)

BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNKNOWN"]
BOROUGH_CODE = {name: index for index, name in enumerate(BOROUGHS)}

FEATURES = [
    "hist_rate",
    "housing_density",
    "lat",
    "lon",
    "borough_code",
    "hour",
    "day_of_week",
    "month",
    "is_weekend",
    "is_holiday",
    "is_covid",
    "temperature",
    "rain",
    "wind_speed",
]


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def load_crashes(path: str = CRASHES) -> pd.DataFrame:
    """Load injury/fatal crashes inside the NYC bbox with a stable schema."""
    usecols = [
        "crash_date",
        "crash_time",
        "borough",
        "latitude",
        "longitude",
        "number_of_persons_injured",
        "number_of_persons_killed",
    ]
    raw = pd.read_csv(path, usecols=usecols, low_memory=False)

    lat = pd.to_numeric(raw["latitude"], errors="coerce")
    lon = pd.to_numeric(raw["longitude"], errors="coerce")
    inside = (
        lat.between(BBOX[0], BBOX[1])
        & lon.between(BBOX[2], BBOX[3])
        & lat.notna()
        & lon.notna()
    )
    injured = pd.to_numeric(raw["number_of_persons_injured"], errors="coerce").fillna(0)
    killed = pd.to_numeric(raw["number_of_persons_killed"], errors="coerce").fillna(0)

    frame = raw.loc[inside & ((injured > 0) | (killed > 0))].copy()
    frame["lat"] = lat.loc[frame.index].astype(float)
    frame["lon"] = lon.loc[frame.index].astype(float)
    frame["injured"] = injured.loc[frame.index].astype(int)
    frame["killed"] = killed.loc[frame.index].astype(int)

    date_part = pd.to_datetime(frame["crash_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    time_part = frame["crash_time"].astype(str).str.strip()
    stamp = pd.to_datetime(date_part + " " + time_part, errors="coerce")
    frame["timestamp"] = stamp.dt.floor("h")
    frame = frame.dropna(subset=["timestamp"])

    frame["cell"] = [
        h3.latlng_to_cell(a, b, RESOLUTION) for a, b in zip(frame["lat"], frame["lon"])
    ]
    frame["borough"] = frame["borough"].fillna("UNKNOWN").astype(str).str.upper()
    return frame[["timestamp", "cell", "lat", "lon", "borough", "injured", "killed"]]


def cell_borough_map(frame: pd.DataFrame) -> pd.Series:
    """Majority borough per hex from the labelled rows. Geography, not target."""
    labelled = frame[frame["borough"] != "UNKNOWN"]
    if labelled.empty:
        return pd.Series(dtype=object)
    return labelled.groupby("cell")["borough"].agg(lambda s: s.value_counts().idxmax())


def attach_borough(frame: pd.DataFrame, mapping: pd.Series) -> pd.DataFrame:
    frame = frame.copy()
    frame["borough"] = frame["cell"].map(mapping).fillna(frame["borough"])
    frame.loc[~frame["borough"].isin(BOROUGHS), "borough"] = "UNKNOWN"
    return frame


def hourly_counts(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per cell-hour that had at least one injury/fatal crash."""
    grouped = (
        frame.groupby(["cell", "timestamp"], as_index=False)
        .agg(count=("cell", "size"), lat=("lat", "mean"), lon=("lon", "mean"))
    )
    return grouped


def sample_zero_cell_hours(
    cells: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    positives: set[tuple[str, pd.Timestamp]],
    per_hour: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Uniform sample of cell-hours with no injury/fatal crash.

    Each sampled row represents ``len(cells) / per_hour`` real cell-hours; the
    trainer uses that as its weight. Sampling keeps the frame small without
    changing the Poisson target.
    """
    if per_hour >= len(cells):
        raise ValueError("per_hour must be smaller than the cell count")
    rows: list[tuple[str, pd.Timestamp]] = []
    for hour in pd.date_range(start, end, freq="h"):
        picks = rng.choice(cells, size=per_hour, replace=False)
        for cell in picks:
            key = (cell, hour)
            if key not in positives:
                rows.append(key)
    frame = pd.DataFrame(rows, columns=["cell", "timestamp"])
    frame["count"] = 0
    return frame


def time_features(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    stamp = frame["timestamp"]
    frame["hour"] = stamp.dt.hour.astype("int8")
    frame["day_of_week"] = stamp.dt.dayofweek.astype("int8")
    frame["month"] = stamp.dt.month.astype("int8")
    frame["is_weekend"] = (frame["day_of_week"] >= 5).astype("int8")
    holidays = USFederalHolidayCalendar().holidays(start=stamp.min(), end=stamp.max())
    frame["is_holiday"] = stamp.dt.normalize().isin(holidays).astype("int8")
    frame["is_covid"] = (
        (stamp >= pd.Timestamp("2020-03-01")) & (stamp < pd.Timestamp("2022-01-01"))
    ).astype("int8")
    return frame


def join_weather(frame: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    frame = frame.merge(
        weather.rename(columns={"time": "timestamp"}),
        on="timestamp",
        how="left",
        validate="many_to_one",
    )
    return frame


def attach_hist_rate(
    frame: pd.DataFrame,
    yearly: pd.DataFrame,
    prior: float,
    prior_hours: float,
) -> pd.DataFrame:
    """Add the leakage-safe cell rate for each row's own year.

    ``yearly`` holds per-cell per-year counts and exposure. A row in year Y only
    sees years strictly before Y, smoothed toward ``prior`` with ``prior_hours``
    of pseudo-exposure. Rows in the first year therefore get the prior.
    """
    frame = frame.copy()
    frame["year"] = frame["timestamp"].dt.year.astype("int16")
    frame["hist_rate"] = prior

    rate_by_year: dict[tuple[str, int], float] = {}
    for (cell, year), group in yearly.groupby(["cell", "year"]):
        rate_by_year[(cell, year)] = float(group["count"].sum()) / max(
            float(group["exposure"].sum()), 1.0
        )

    years = sorted(yearly["year"].unique())
    for year in sorted(frame["year"].unique()):
        prior_years = [y for y in years if y < year]
        if not prior_years:
            continue
        subset = yearly[yearly["year"].isin(prior_years)]
        totals = (
            subset.groupby("cell")[["count", "exposure"]].sum().reindex(
                frame.loc[frame["year"] == year, "cell"].unique()
            )
        )
        rates = (totals["count"].fillna(0.0) + prior * prior_hours) / (
            totals["exposure"].fillna(0.0) + prior_hours
        )
        mask = frame["year"] == year
        frame.loc[mask, "hist_rate"] = frame.loc[mask, "cell"].map(rates).fillna(prior)
    return frame


def yearly_cell_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-cell per-year crash count and exposure hours for the training window."""
    start = frame["timestamp"].min().floor("D")
    end = frame["timestamp"].max().ceil("D")
    hours_by_year = {}
    for year in range(start.year, end.year + 1):
        lo = max(start, pd.Timestamp(year=year, month=1, day=1))
        hi = min(end, pd.Timestamp(year=year, month=12, day=31, hour=23))
        hours_by_year[year] = int((hi - lo).total_seconds() // 3600) + 1

    counts = (
        frame.assign(year=frame["timestamp"].dt.year)
        .groupby(["cell", "year"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )
    cells = sorted(frame["cell"].unique())
    grid = pd.MultiIndex.from_product([cells, sorted(hours_by_year)]).to_frame(
        index=False
    )
    grid.columns = ["cell", "year"]
    grid["exposure"] = grid["year"].map(hours_by_year).astype(float)
    merged = grid.merge(counts, on=["cell", "year"], how="left")
    merged["count"] = merged["count"].fillna(0).astype(float)
    return merged


def build_events(
    crashes: pd.DataFrame,
    weather: pd.DataFrame,
    per_hour: int = 30,
    seed: int = 20261029,
    yearly_crashes: pd.DataFrame | None = None,
    cells: list[str] | None = None,
    borough_map: pd.Series | None = None,
) -> pd.DataFrame:
    """Build the training frame: positives plus weighted zero cell-hours.

    ``yearly_crashes`` lets a spatial holdout compute the historical rate from
    the training boroughs only, while still encoding rows from the held-out
    borough. ``cells`` is the zero-sampling universe.
    """
    if borough_map is None:
        borough_map = cell_borough_map(crashes)
    crashes = attach_borough(crashes, borough_map)
    yearly_crashes = crashes if yearly_crashes is None else yearly_crashes

    positives = hourly_counts(crashes)
    positives = time_features(positives)
    positive_keys = {(cell, stamp) for cell, stamp in zip(positives["cell"], positives["timestamp"])}

    cells = sorted(cells if cells is not None else crashes["cell"].unique())
    zeros = sample_zero_cell_hours(
        cells,
        crashes["timestamp"].min(),
        crashes["timestamp"].max(),
        positive_keys,
        per_hour=per_hour,
        rng=np.random.default_rng(seed),
    )
    zeros["lat"] = np.nan
    zeros["lon"] = np.nan

    # A sampled zero stands for len(cells) / per_hour real cell-hours.
    zeros["weight"] = len(cells) / float(per_hour)
    positives["weight"] = 1.0

    frame = pd.concat([positives, zeros], ignore_index=True)
    frame = time_features(frame)
    frame["borough"] = frame["cell"].map(borough_map).fillna("UNKNOWN")
    frame["borough_code"] = frame["borough"].map(BOROUGH_CODE).astype("int8")

    cell_centres = pd.DataFrame(
        {
            "cell": cells,
            "lat": [h3.cell_to_latlng(c)[0] for c in cells],
            "lon": [h3.cell_to_latlng(c)[1] for c in cells],
        }
    )
    frame = frame.drop(columns=["lat", "lon"]).merge(cell_centres, on="cell", how="left")

    # External density proxy, safe for a held-out borough because it contains no
    # crash outcomes. Missing cells pass NaN to the model.
    from population import ensure_density

    density = ensure_density(cells)
    frame = frame.merge(
        density[["cell", "housing_density"]], on="cell", how="left"
    )

    yearly = yearly_cell_totals(yearly_crashes)
    total_hours = float(yearly["exposure"].sum())
    prior = float(yearly["count"].sum()) / total_hours
    prior_hours = 24.0 * 30.0
    frame = attach_hist_rate(frame, yearly, prior, prior_hours)

    frame = join_weather(frame, weather)
    return frame


def temporal_split(
    frame: pd.DataFrame, train_end: str, validation_end: str
) -> Split:
    train = frame[frame["timestamp"] < pd.Timestamp(train_end)]
    validation = frame[
        (frame["timestamp"] >= pd.Timestamp(train_end))
        & (frame["timestamp"] < pd.Timestamp(validation_end))
    ]
    test = frame[frame["timestamp"] >= pd.Timestamp(validation_end)]
    return Split(train=train, validation=validation, test=test)


def feature_matrix(frame: pd.DataFrame, features: list[str] = FEATURES) -> pd.DataFrame:
    return frame.loc[:, features].astype(float)
