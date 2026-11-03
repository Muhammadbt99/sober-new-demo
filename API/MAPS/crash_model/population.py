"""Static housing density per H3 cell.

Crash history cannot tell a held-out borough that it is dense. This module
attaches an external, non-crash covariate: 2020 census housing units in the 2020
NTA that contains each hex centroid. It is a density proxy, not a crash outcome,
so it transfers to a borough the model has never seen.

Run once to build ``cell_density.csv``; training and serving read the cache.
"""
from __future__ import annotations

import json
import os

import h3
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape
from shapely.ops import transform
from shapely.prepared import prep

HERE = os.path.dirname(os.path.abspath(__file__))
GEOJSON_CACHE = os.path.join(HERE, "nyc_nta_2020.geojson")
HOUSING_CACHE = os.path.join(HERE, "nyc_nta_housing_2020.csv")
OUTPUT = os.path.join(HERE, "cell_density.csv")

NTA_URL = "https://data.cityofnewyork.us/resource/9nt8-h7nd.geojson"
HOUSING_URL = "https://data.cityofnewyork.us/resource/kyz5-72x5.json?%24limit=5000"

# Equirectangular metres per degree at NYC's latitude.
M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON = 111320.0 * np.cos(np.deg2rad(40.7))


def _project(x, y, z=None):
    return (x * M_PER_DEG_LON, y * M_PER_DEG_LAT)


def ensure_sources() -> tuple[dict, pd.DataFrame]:
    if os.path.exists(GEOJSON_CACHE):
        with open(GEOJSON_CACHE, encoding="utf-8") as handle:
            geo = json.load(handle)
    else:
        response = requests.get(NTA_URL, timeout=120)
        response.raise_for_status()
        geo = response.json()
        with open(GEOJSON_CACHE, "w", encoding="utf-8") as handle:
            json.dump(geo, handle)

    if os.path.exists(HOUSING_CACHE):
        housing = pd.read_csv(HOUSING_CACHE)
    else:
        response = requests.get(HOUSING_URL, timeout=180)
        response.raise_for_status()
        raw = pd.DataFrame(response.json())
        housing = raw[["nta2010", "ntaname2010", "cenunits20"]].rename(
            columns={
                "nta2010": "nta_code",
                "ntaname2010": "nta_name",
                "cenunits20": "housing_units_2020",
            }
        )
        housing["housing_units_2020"] = pd.to_numeric(
            housing["housing_units_2020"], errors="coerce"
        )
        housing.to_csv(HOUSING_CACHE, index=False)
    return geo, housing


def build(cells: list[str]) -> pd.DataFrame:
    geo, housing = ensure_sources()
    units_by_code = housing.set_index("nta_code")["housing_units_2020"].to_dict()

    areas = []
    for feature in geo["features"]:
        props = feature["properties"]
        code = props["nta2020"]
        geom = shape(feature["geometry"])
        area_km2 = transform(_project, geom).area / 1_000_000.0
        areas.append(
            {
                "nta_code": code,
                "nta_name": props.get("ntaname"),
                "borough": props.get("boroname"),
                "geometry": geom,
                "prepared": prep(geom),
                "area_km2": area_km2,
                "housing_units_2020": float(units_by_code.get(code, np.nan)),
            }
        )

    rows = []
    for cell in cells:
        lat, lon = h3.cell_to_latlng(cell)
        point = Point(lon, lat)
        match = None
        for area in areas:
            if area["prepared"].contains(point):
                match = area
                break
        method = "contains"
        if match is None:
            # Coastal cells can fall just outside a boundary. Nearest centroid is
            # an explicit, labelled fallback rather than a silent zero.
            best = None
            for area in areas:
                centroid = area["geometry"].centroid
                distance = (centroid.x - lon) ** 2 + (centroid.y - lat) ** 2
                if best is None or distance < best[0]:
                    best = (distance, area)
            match = best[1]
            method = "nearest"
        rows.append(
            {
                "cell": cell,
                "lat": lat,
                "lon": lon,
                "nta_code": match["nta_code"],
                "nta_name": match["nta_name"],
                "borough": match["borough"],
                "housing_units_2020": match["housing_units_2020"],
                "area_km2": match["area_km2"],
                "housing_density": match["housing_units_2020"] / match["area_km2"],
                "match_method": method,
            }
        )
    return pd.DataFrame(rows)


def ensure_density(cells: list[str]) -> pd.DataFrame:
    if os.path.exists(OUTPUT):
        frame = pd.read_csv(OUTPUT)
        if set(cells).issubset(set(frame["cell"])):
            return frame
    frame = build(cells)
    frame.to_csv(OUTPUT, index=False)
    return frame


if __name__ == "__main__":
    import dataset

    crashes = dataset.load_crashes()
    cells = sorted(crashes["cell"].unique())
    table = ensure_density(cells)
    print(f"cells: {len(table):,}")
    print(table.groupby("borough")["housing_density"].agg(["mean", "median"]).to_string())
    print(table["match_method"].value_counts().to_string())
    print(f"wrote {OUTPUT}")
