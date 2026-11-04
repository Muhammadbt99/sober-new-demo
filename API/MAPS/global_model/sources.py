"""Where each training city's crashes, population and map come from.

Every loader returns one row per injury crash with the same columns:

    lat, lon      WGS84
    year, month   local calendar (month may be missing for nothing)
    hour          0-23, local time
    dow           0 = Monday ... 6 = Sunday

Only crashes with at least one person hurt are kept, so the cities agree on
what is counted (STATS19, BAAC and the Unfallatlas record injury crashes only;
New York and Chicago also record damage-only crashes, which are dropped).
"""
from __future__ import annotations

import glob
import os
import zipfile
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
WORK = os.path.join(HERE, "work")
MAPS_DIR = os.path.dirname(HERE)

YEARS = (2021, 2022, 2023, 2024, 2025)
COLUMNS = ["lat", "lon", "year", "month", "hour", "dow"]


@dataclass(frozen=True)
class City:
    key: str
    name: str
    country: str  # Kontur / ISO code
    osm: str  # file in raw/osm
    bbox: tuple[float, float, float, float]  # south, west, north, east (generous)
    loader: str
    #: First day of the local weekend, 0 = Monday. Saturday everywhere here,
    #: including the UAE since 2022.
    weekend_start: int = 5
    notes: list[str] = field(default_factory=list)


CITIES: dict[str, City] = {
    "nyc": City("nyc", "New York", "US", "NewYork.osm.pbf", (40.47, -74.27, 40.93, -73.68), "load_nyc"),
    "chicago": City("chicago", "Chicago", "US", "Chicago.osm.pbf", (41.63, -87.95, 42.03, -87.51), "load_chicago"),
    "london": City("london", "London", "GB", "London.osm.pbf", (51.27, -0.53, 51.71, 0.35), "load_london"),
    "paris": City("paris", "Paris", "FR", "Paris.osm.pbf", (48.71, 2.12, 49.01, 2.62), "load_paris"),
    "berlin": City("berlin", "Berlin", "DE", "Berlin.osm.pbf", (52.33, 13.08, 52.68, 13.77), "load_berlin"),
}


def _finish(frame: pd.DataFrame, city: City) -> pd.DataFrame:
    frame = frame.dropna(subset=["lat", "lon", "hour", "dow"])
    south, west, north, east = city.bbox
    inside = frame["lat"].between(south, north) & frame["lon"].between(west, east)
    frame = frame.loc[inside & frame["year"].isin(YEARS)].copy()
    frame["hour"] = frame["hour"].astype(int)
    frame["dow"] = frame["dow"].astype(int)
    frame["year"] = frame["year"].astype(int)
    return frame[COLUMNS].reset_index(drop=True)


def _from_timestamps(lat, lon, stamps: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "lat": pd.to_numeric(lat, errors="coerce"),
            "lon": pd.to_numeric(lon, errors="coerce"),
            "year": stamps.dt.year,
            "month": stamps.dt.month,
            "hour": stamps.dt.hour,
            "dow": stamps.dt.dayofweek,
        }
    )


def load_nyc(city: City) -> pd.DataFrame:
    path = os.path.join(MAPS_DIR, "nyc_crashes_rich.csv.gz")
    usecols = [
        "crash_date",
        "crash_time",
        "latitude",
        "longitude",
        "number_of_persons_injured",
        "number_of_persons_killed",
    ]
    frame = pd.read_csv(path, usecols=usecols, low_memory=False)
    hurt = (
        pd.to_numeric(frame["number_of_persons_injured"], errors="coerce").fillna(0)
        + pd.to_numeric(frame["number_of_persons_killed"], errors="coerce").fillna(0)
    ) > 0
    frame = frame.loc[hurt]
    stamps = pd.to_datetime(
        frame["crash_date"].astype(str).str[:10] + " " + frame["crash_time"].astype(str),
        errors="coerce",
    )
    return _finish(_from_timestamps(frame["latitude"], frame["longitude"], stamps), city)


def load_chicago(city: City) -> pd.DataFrame:
    frame = pd.read_csv(os.path.join(RAW, "chicago", "crashes_injury_2021_2025.csv"))
    frame = frame.loc[pd.to_numeric(frame["injuries_total"], errors="coerce") > 0]
    stamps = pd.to_datetime(frame["crash_date"], errors="coerce")
    return _finish(_from_timestamps(frame["latitude"], frame["longitude"], stamps), city)


def load_london(city: City) -> pd.DataFrame:
    """STATS19: Metropolitan Police (1) and City of London (48) areas."""
    parts = []
    for path in sorted(glob.glob(os.path.join(RAW, "stats19", "collision-*.csv"))):
        frame = pd.read_csv(
            path,
            usecols=["date", "time", "police_force", "latitude", "longitude"],
            dtype={"police_force": str},
            low_memory=False,
        )
        frame = frame.loc[frame["police_force"].str.strip().isin(["1", "48"])]
        stamps = pd.to_datetime(
            frame["date"] + " " + frame["time"].fillna(""), format="%d/%m/%Y %H:%M", errors="coerce"
        )
        parts.append(_from_timestamps(frame["latitude"], frame["longitude"], stamps))
    return _finish(pd.concat(parts, ignore_index=True), city)


def load_paris(city: City) -> pd.DataFrame:
    """BAAC: Paris and the three inner departments (92, 93, 94)."""
    parts = []
    for path in sorted(glob.glob(os.path.join(RAW, "baac", "caract-*.csv"))):
        frame = pd.read_csv(path, sep=";", dtype=str, encoding="latin-1")
        frame.columns = [column.strip().strip('"').lower() for column in frame.columns]
        frame = frame.loc[frame["dep"].str.strip().isin(["75", "92", "93", "94"])]
        hrmn = frame["hrmn"].str.strip()
        # Older files write 1730 instead of 17:30.
        hrmn = hrmn.where(hrmn.str.contains(":"), hrmn.str.zfill(4).str[:2] + ":" + hrmn.str.zfill(4).str[2:])
        stamps = pd.to_datetime(
            frame["an"].str.strip()
            + "-"
            + frame["mois"].str.strip().str.zfill(2)
            + "-"
            + frame["jour"].str.strip().str.zfill(2)
            + " "
            + hrmn,
            format="%Y-%m-%d %H:%M",
            errors="coerce",
        )
        lat = frame["lat"].str.replace(",", ".", regex=False)
        lon = frame["long"].str.replace(",", ".", regex=False)
        parts.append(_from_timestamps(lat, lon, stamps))
    return _finish(pd.concat(parts, ignore_index=True), city)


def load_berlin(city: City) -> pd.DataFrame:
    """Unfallatlas: state 11 (Berlin). No exact date, only month, hour and weekday."""
    parts = []
    for path in sorted(glob.glob(os.path.join(RAW, "unfallatlas", "Unfallorte*_CSV.zip"))):
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith((".csv", ".txt")))
            with archive.open(name) as handle:
                frame = pd.read_csv(handle, sep=";", dtype=str, encoding="utf-8-sig")
        frame = frame.loc[frame["ULAND"].str.strip() == "11"]
        # UWOCHENTAG counts from Sunday = 1.
        weekday = pd.to_numeric(frame["UWOCHENTAG"], errors="coerce")
        parts.append(
            pd.DataFrame(
                {
                    "lat": pd.to_numeric(frame["YGCSWGS84"].str.replace(",", ".", regex=False), errors="coerce"),
                    "lon": pd.to_numeric(frame["XGCSWGS84"].str.replace(",", ".", regex=False), errors="coerce"),
                    "year": pd.to_numeric(frame["UJAHR"], errors="coerce"),
                    "month": pd.to_numeric(frame["UMONAT"], errors="coerce"),
                    "hour": pd.to_numeric(frame["USTUNDE"], errors="coerce"),
                    "dow": (weekday + 5) % 7,
                }
            )
        )
    return _finish(pd.concat(parts, ignore_index=True), city)


def load_crashes(key: str) -> pd.DataFrame:
    city = CITIES[key]
    frame = globals()[city.loader](city)
    if len(frame) == 0:
        raise RuntimeError(f"{key}: no crashes loaded")
    return frame


def describe(frame: pd.DataFrame) -> str:
    years = frame["year"].value_counts().sort_index().to_dict()
    hours = np.bincount(frame["hour"], minlength=24)
    return f"{len(frame):,} injury crashes; by year {years}; peak hour {int(hours.argmax())}"


if __name__ == "__main__":
    import sys

    for key in sys.argv[1:] or list(CITIES):
        crashes = load_crashes(key)
        print(f"{key:8s} {describe(crashes)}")
