"""The response-gap map: where crash risk is high and a hospital is far.

Each scored place is sorted by two questions: is the crash risk here high at
this hour, and is a mapped hospital far away? The answer that matters to a
planner is the corner where both are true.

What this is not is as important as what it is. The distance is a straight line
from the middle of a map cell, not a drive, so it says nothing about ambulance
times. A mapped hospital may not have an emergency department: OpenStreetMap
says so for few of them. Both limits go to the user with the numbers.
"""
from __future__ import annotations

import os
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

HOSPITALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_model", "artifacts", "hospitals.csv")
#: Design choices, not response-time standards: both are shown to the user.
FAR_KM = 5.0
HIGH_PERCENTILE = 75.0
RANKED = 8
#: The map asks for the cells around what it is looking at.
MAP_RADIUS_KM = 15.0
EARTH_KM = 6371.0

NOTE = (
    "Distance is a straight line to the nearest hospital on OpenStreetMap, so it can "
    "cross water. It is not a drive or an ambulance time, and not every mapped hospital "
    "has an emergency department."
)


class Hospitals:
    """The mapped hospitals of the regions the map serves."""

    def __init__(self, path: str = HOSPITALS_FILE):
        self.table = pd.read_csv(path, keep_default_na=False) if os.path.exists(path) else None

    @property
    def available(self) -> bool:
        return self.table is not None and len(self.table) > 0

    def region(self, key: str) -> pd.DataFrame:
        if self.table is None:
            return pd.DataFrame()
        return self.table[self.table["region"] == key]


def nearest(lats: Sequence[float], lons: Sequence[float], hospitals: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Straight-line km to the nearest hospital for each point, and which one."""
    lat = np.radians(np.asarray(lats, dtype=float))[:, None]
    lon = np.radians(np.asarray(lons, dtype=float))[:, None]
    h_lat = np.radians(hospitals["lat"].to_numpy(dtype=float))[None, :]
    h_lon = np.radians(hospitals["lon"].to_numpy(dtype=float))[None, :]
    half = np.sin((h_lat - lat) / 2.0) ** 2 + np.cos(lat) * np.cos(h_lat) * np.sin((h_lon - lon) / 2.0) ** 2
    km = 2.0 * EARTH_KM * np.arcsin(np.sqrt(np.clip(half, 0.0, 1.0)))
    return km.min(axis=1), km.argmin(axis=1)


def quadrant(km: float, percentile: float) -> str:
    high, far = percentile >= HIGH_PERCENTILE, km >= FAR_KM
    if high:
        return "gap" if far else "covered"
    return "remote" if far else "near"


def summarise(cells: Iterable[dict], hospitals: pd.DataFrame, *, centre: Sequence[float]) -> dict:
    """Sort every scored place, and rank the gaps by risk."""
    cells = list(cells)
    if not cells or hospitals.empty:
        return {"counts": {}, "cells": [], "ranked": []}
    km, which = nearest([cell["lat"] for cell in cells], [cell["lon"] for cell in cells], hospitals)
    names = hospitals["name"].tolist()
    counts = {"gap": 0, "covered": 0, "remote": 0, "near": 0}
    rows = []
    for cell, distance, index in zip(cells, km, which):
        kind = quadrant(float(distance), float(cell["percentile"]))
        counts[kind] += 1
        rows.append(
            {
                "cell": cell["cell"],
                "lat": float(cell["lat"]),
                "lon": float(cell["lon"]),
                "name": cell.get("name"),
                "percentile": round(float(cell["percentile"]), 1),
                "km": round(float(distance), 1),
                "quadrant": kind,
                "hospital": names[int(index)] or "an unnamed hospital",
            }
        )
    # With no gaps the honest list is the high-risk places furthest from help: the
    # line stays where it is rather than moving until something falls outside it.
    gaps = [row for row in rows if row["quadrant"] == "gap"]
    if gaps:
        ranked = sorted(gaps, key=lambda row: (row["percentile"], row["km"]), reverse=True)[:RANKED]
    else:
        high = [row for row in rows if row["quadrant"] == "covered"]
        ranked = sorted(high, key=lambda row: (row["km"], row["percentile"]), reverse=True)[:RANKED]
    near, _ = nearest([row["lat"] for row in rows], [row["lon"] for row in rows], pd.DataFrame({"lat": [centre[0]], "lon": [centre[1]]}))
    shown = [
        {key: row[key] for key in ("cell", "km", "quadrant", "hospital")}
        for row, distance in zip(rows, near)
        if distance <= MAP_RADIUS_KM
    ]
    return {"counts": counts, "cells": shown, "ranked": ranked}


def here(lat: float, lon: float, hospitals: pd.DataFrame) -> dict | None:
    """The nearest hospital to one place."""
    if hospitals.empty:
        return None
    km, which = nearest([lat], [lon], hospitals)
    row = hospitals.iloc[int(which[0])]
    return {
        "km": round(float(km[0]), 1),
        "hospital": row["name"] or "an unnamed hospital",
        "emergency_tagged": row["emergency"] == "yes",
    }
