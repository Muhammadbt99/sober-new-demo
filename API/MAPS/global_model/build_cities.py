"""Build one training table per city, and the feature table for the UAE.

For a training city:

1. Its injury crashes (sources.py) are put in H3 resolution-8 cells.
2. The study area is the cells its crash records cover: cells with at least
   three crashes in five years, plus cells mostly surrounded by them (parks,
   rail yards and quiet blocks inside the city, which really have few crashes).
   Stray points just outside the jurisdiction are left out, because a cell the
   police force does not cover would look safe for the wrong reason.
3. Every cell gets OpenStreetMap features (osm_features.py) and Kontur
   population, for itself and for its ring of six neighbours.
4. Crashes are counted per cell, day of the week (counted from the start of
   the local weekend) and hour.

Usage:
    python build_cities.py                 # every city, then the UAE
    python build_cities.py london uae      # some of them
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import h3  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from global_model.osm_features import FEATURES, RESOLUTION, extract  # noqa: E402
from global_model.sources import CITIES, RAW, WORK, YEARS, describe, load_crashes  # noqa: E402

CORE_MIN_CRASHES = 3
FILL_NEIGHBOURS = 4  # of 6
#: The UAE, with a margin; cells outside the country are dropped by population.
UAE_BBOX = (22.6, 51.4, 26.2, 56.45)
UAE_RINGS = 2  # roads this many cells beyond populated cells are still scored


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- population


def _kontur_path(country: str) -> str:
    target = os.path.join(WORK, "kontur", f"kontur_population_{country}.gpkg")
    if not os.path.exists(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        source = os.path.join(RAW, "kontur", f"kontur_population_{country}.gpkg.gz")
        log(f"  unpacking {os.path.basename(source)}")
        with gzip.open(source, "rb") as packed, open(target + ".part", "wb") as out:
            shutil.copyfileobj(packed, out, 1 << 20)
        os.replace(target + ".part", target)
    return target


def population(country: str, bbox: tuple[float, float, float, float]) -> dict[str, float]:
    """Kontur population per H3 resolution-8 cell inside ``bbox``.

    The files have no spatial index, so every row is read and kept by its
    cell centre (a few seconds for the whole United States).
    """
    south, west, north, east = bbox
    con = sqlite3.connect(_kontur_path(country))
    try:
        rows = con.execute("SELECT h3, population FROM population")
        out = {}
        for cell, people in rows:
            lat, lon = h3.cell_to_latlng(cell)
            if south <= lat <= north and west <= lon <= east:
                out[cell] = float(people or 0.0)
    finally:
        con.close()
    return out


# --------------------------------------------------------------------------- cells


def study_area(crash_cells: pd.Series) -> list[str]:
    counts = crash_cells.value_counts()
    area = set(counts.index[counts >= CORE_MIN_CRASHES])
    for _ in range(2):
        candidates = {n for cell in area for n in h3.grid_ring(cell, 1)} - area
        area |= {
            cell
            for cell in candidates
            if sum(n in area for n in h3.grid_ring(cell, 1)) >= FILL_NEIGHBOURS
        }
    return sorted(area)


def cell_table(cells: list[str], osm: pd.DataFrame, people: dict[str, float]) -> pd.DataFrame:
    """Features of each cell and of its six neighbours (summed)."""
    own = osm.reindex(cells).fillna(0.0)
    ring_rows = []
    for cell in cells:
        ring = h3.grid_ring(cell, 1)
        ring_rows.append(osm.reindex(ring).fillna(0.0).sum(axis=0).to_numpy())
    ring = pd.DataFrame(ring_rows, index=cells, columns=[f"ring_{f}" for f in FEATURES])
    table = pd.concat([own, ring], axis=1)
    table["population"] = [people.get(cell, 0.0) for cell in cells]
    table["ring_population"] = [
        sum(people.get(n, 0.0) for n in h3.grid_ring(cell, 1)) for cell in cells
    ]
    lat_lon = [h3.cell_to_latlng(cell) for cell in cells]
    table["lat"] = [p[0] for p in lat_lon]
    table["lon"] = [p[1] for p in lat_lon]
    table.index.name = "cell"
    return table


def build_city(key: str) -> dict:
    city = CITIES[key]
    started = time.time()
    log(f"[{key}] {city.name}")
    crashes = load_crashes(key)
    log(f"  {describe(crashes)}")
    crashes["cell"] = [
        h3.latlng_to_cell(lat, lon, RESOLUTION)
        for lat, lon in zip(crashes["lat"].to_numpy(), crashes["lon"].to_numpy())
    ]
    cells = study_area(crashes["cell"])
    index = {cell: i for i, cell in enumerate(cells)}
    kept = crashes["cell"].map(index)
    inside = kept.notna()
    log(f"  study area {len(cells):,} cells; {inside.mean():.1%} of crashes inside")

    # Counts per cell, year, day (from the start of the local weekend) and hour,
    # so a model can be tested on years after the ones it learned from.
    counts = np.zeros((len(cells), len(YEARS), 7, 24), dtype=np.int32)
    year_index = crashes.loc[inside, "year"].map({year: i for i, year in enumerate(YEARS)}).to_numpy()
    rel_dow = (crashes.loc[inside, "dow"].to_numpy() - city.weekend_start) % 7
    np.add.at(
        counts,
        (kept[inside].astype(int).to_numpy(), year_index, rel_dow, crashes.loc[inside, "hour"].to_numpy()),
        1,
    )

    lats = [h3.cell_to_latlng(c)[0] for c in cells]
    lons = [h3.cell_to_latlng(c)[1] for c in cells]
    margin = 0.02
    bbox = (min(lats) - margin, min(lons) - margin, max(lats) + margin, max(lons) + margin)
    osm = extract(os.path.join(RAW, "osm", city.osm), bbox, log=log)
    people = population(city.country, bbox)
    table = cell_table(cells, osm, people)

    years = list(YEARS)
    weeks = [(366 if y % 4 == 0 else 365) / 7.0 for y in years]
    meta = {
        "key": key,
        "name": city.name,
        "country": city.country,
        "weekend_start": city.weekend_start,
        "years": years,
        "weeks": weeks,
        "crashes_total": int(len(crashes)),
        "crashes_in_area": int(counts.sum()),
        "cells": len(cells),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    os.makedirs(WORK, exist_ok=True)
    table.to_csv(os.path.join(WORK, f"{key}_cells.csv.gz"))
    np.save(os.path.join(WORK, f"{key}_counts.npy"), counts)
    with open(os.path.join(WORK, f"{key}_meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
    log(f"  wrote {key}: {len(cells):,} cells, {int(counts.sum()):,} crashes ({time.time() - started:.0f}s)")
    return meta


def build_uae() -> dict:
    started = time.time()
    log("[uae] United Arab Emirates (features only)")
    people = population("AE", UAE_BBOX)
    populated = {cell for cell, value in people.items() if value > 0}
    osm = extract(os.path.join(RAW, "osm", "gcc-states.osm.pbf"), UAE_BBOX, log=log)
    near = set(populated)
    for cell in populated:
        near.update(h3.grid_disk(cell, UAE_RINGS))
    drivable = [c for c in FEATURES if c.startswith("km_") and c not in ("km_walk", "km_cycle")]
    has_road = set(osm.index[osm[drivable].sum(axis=1) > 0])
    cells = sorted(populated | (near & has_road))
    table = cell_table(cells, osm, people)
    table.to_csv(os.path.join(WORK, "uae_cells.csv.gz"))
    meta = {
        "key": "uae",
        "name": "United Arab Emirates",
        "country": "AE",
        "weekend_start": 5,
        "cells": len(cells),
        "populated_cells": len(populated),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(WORK, "uae_meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
    log(f"  wrote uae: {len(cells):,} cells ({len(populated):,} populated) ({time.time() - started:.0f}s)")
    return meta


if __name__ == "__main__":
    wanted = sys.argv[1:] or [*CITIES, "uae"]
    for name in wanted:
        if name == "uae":
            build_uae()
        else:
            build_city(name)
