"""Offline nightlife (bar) counts from a local CSV.

Reads ``bars_nyc.csv`` next to this module when present.  Expected columns are
``latitude``/``longitude`` (or ``lat``/``lon``); other common spellings are
accepted.  Nothing is downloaded.  If the file is missing or unusable the index
degrades gracefully: ``available`` is ``False`` and counts are 0.
"""

import os

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_KM = 6371.0088

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CSV = os.path.join(_MODULE_DIR, "bars_nyc.csv")

_LAT_CANDIDATES = ("latitude", "lat", "y")
_LON_CANDIDATES = ("longitude", "lon", "lng", "long", "x")


def _find_column(frame, candidates):
    """Return the first matching column name, case-insensitively."""
    lookup = {str(column).strip().lower(): column for column in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


class BarsIndex:
    """Spatial index of bar locations for radius counts.

    Parameters
    ----------
    csv_path:
        Optional path to the bars CSV.  Defaults to ``bars_nyc.csv`` next to
        this module.
    radius_km:
        Default search radius in kilometres.
    """

    def __init__(self, csv_path=None, radius_km=1.0):
        self.csv_path = str(csv_path) if csv_path is not None else _DEFAULT_CSV
        self.radius_km = float(radius_km)
        self.available = False
        self.lats = np.empty(0, dtype=float)
        self.lons = np.empty(0, dtype=float)
        self.tree = None

        try:
            self._load()
        except Exception as exc:
            print(
                f"[bars] WARNING: could not build BarsIndex from "
                f"{self.csv_path!r} ({type(exc).__name__}: {exc}). "
                "Bar counts will be reported as 0."
            )
            self.available = False
            self.tree = None
            self.lats = np.empty(0, dtype=float)
            self.lons = np.empty(0, dtype=float)

    def _load(self):
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"bars CSV not found: {self.csv_path}")

        frame = pd.read_csv(self.csv_path, low_memory=False)
        lat_column = _find_column(frame, _LAT_CANDIDATES)
        lon_column = _find_column(frame, _LON_CANDIDATES)
        if lat_column is None or lon_column is None:
            raise ValueError(
                "could not find latitude/longitude columns in "
                f"{list(frame.columns)}"
            )

        lats = pd.to_numeric(frame[lat_column], errors="coerce").to_numpy(dtype=float)
        lons = pd.to_numeric(frame[lon_column], errors="coerce").to_numpy(dtype=float)

        valid = np.isfinite(lats) & np.isfinite(lons) & (lats != 0) & (lons != 0)
        lats = lats[valid]
        lons = lons[valid]
        if lats.size == 0:
            raise ValueError("no usable bar rows after filtering")

        self.lats = lats
        self.lons = lons
        points = np.column_stack((np.radians(lats), np.radians(lons)))
        self.tree = BallTree(points, metric="haversine")
        self.available = True

    def _radius_radians(self, radius_km):
        radius = self.radius_km if radius_km is None else float(radius_km)
        return max(radius, 0.0) / EARTH_KM

    def counts(self, lats, lons, radius_km=None):
        """Number of bars within the radius of each (lat, lon) pair."""
        lats = np.atleast_1d(np.asarray(lats, dtype=float))
        lons = np.atleast_1d(np.asarray(lons, dtype=float))
        if lats.shape != lons.shape:
            raise ValueError("lats and lons must have the same shape")

        result = np.zeros(lats.size, dtype=int)
        if not self.available or lats.size == 0:
            return result

        finite = np.isfinite(lats) & np.isfinite(lons)
        if not np.any(finite):
            return result

        points = np.column_stack(
            (np.radians(lats[finite]), np.radians(lons[finite]))
        )
        neighbours = self.tree.query_radius(
            points, r=self._radius_radians(radius_km), return_distance=False
        )
        result[finite] = np.fromiter(
            (len(indices) for indices in neighbours),
            dtype=int,
            count=len(neighbours),
        )
        return result

    def count(self, lat, lon, radius_km=1.0):
        """Number of bars within ``radius_km`` of a single point."""
        return int(self.counts([lat], [lon], radius_km=radius_km)[0])
