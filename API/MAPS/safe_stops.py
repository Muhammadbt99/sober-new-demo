"""Offline safe-stop lookup (police, hospitals, clinics, parking, rest areas).

Reads ``safe_stops_nyc.csv`` next to this module when present.  Expected
columns are ``latitude``/``longitude`` (or common aliases), ``type`` and
optionally ``name``.  If the file is missing or unusable the index degrades
gracefully (``available`` is ``False``) and callers can fall back to Overpass.
"""

import os

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_KM = 6371.0088

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CSV = os.path.join(_MODULE_DIR, "safe_stops_nyc.csv")

_LAT_CANDIDATES = ("latitude", "lat", "y")
_LON_CANDIDATES = ("longitude", "lon", "lng", "long", "x")

SAFETY_PRIORITY = {
    "police": 1,
    "hospital": 2,
    "clinic": 3,
    "rest_area": 4,
    "parking": 5,
}


def _find_column(frame, candidates):
    lookup = {str(column).strip().lower(): column for column in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


class SafeStopsIndex:
    """Spatial index of safe-stop POIs for nearest-neighbour lookups."""

    def __init__(self, csv_path=None):
        self.csv_path = str(csv_path) if csv_path is not None else _DEFAULT_CSV
        self.available = False
        self.lats = np.empty(0, dtype=float)
        self.lons = np.empty(0, dtype=float)
        self.names = []
        self.types = []
        self.tree = None

        try:
            self._load()
        except Exception as exc:
            print(
                f"[safe_stops] WARNING: could not build SafeStopsIndex from "
                f"{self.csv_path!r} ({type(exc).__name__}: {exc}). "
                "Nearest-safe lookups will use Overpass."
            )
            self.available = False
            self.tree = None

    def _load(self):
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"safe stops CSV not found: {self.csv_path}")

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
        if not np.any(valid):
            raise ValueError("no usable safe-stop rows after filtering")

        frame = frame.loc[valid]
        self.lats = lats[valid]
        self.lons = lons[valid]
        if "type" in frame.columns:
            self.types = frame["type"].fillna("unknown").astype(str).tolist()
        else:
            self.types = ["unknown"] * len(frame)
        if "name" in frame.columns:
            self.names = frame["name"].fillna("").astype(str).tolist()
        else:
            self.names = [""] * len(frame)

        points = np.column_stack((np.radians(self.lats), np.radians(self.lons)))
        self.tree = BallTree(points, metric="haversine")
        self.available = True

    def nearest(self, lat, lon, radius_km=3.0, limit=10):
        """Return safe stops within ``radius_km``, prioritised by type/distance.

        ``limit=None`` returns every stop in range; the caller decides how
        many of them to expose.
        """
        if not self.available:
            return []
        try:
            lat = float(lat)
            lon = float(lon)
            limit = None if limit is None else max(int(limit), 0)
        except (TypeError, ValueError):
            return []
        if not (np.isfinite(lat) and np.isfinite(lon)) or limit == 0:
            return []

        radius = max(float(radius_km), 0.0) / EARTH_KM
        indices, distances = self.tree.query_radius(
            [[np.radians(lat), np.radians(lon)]],
            r=radius,
            return_distance=True,
        )
        if len(indices) == 0:
            return []

        results = []
        for idx, distance_rad in zip(indices[0], distances[0]):
            idx = int(idx)
            stop_type = self.types[idx] if idx < len(self.types) else "unknown"
            name = self.names[idx] if idx < len(self.names) else ""
            if not name:
                name = stop_type.replace("_", " ").title()
            distance_m = float(distance_rad) * EARTH_KM * 1000.0
            results.append({
                "name": name,
                "type": stop_type,
                "lat": float(self.lats[idx]),
                "lon": float(self.lons[idx]),
                "distance_m": round(distance_m),
                "distance_km": round(distance_m / 1000.0, 2),
                "safety_priority": SAFETY_PRIORITY.get(stop_type, 6),
            })

        results.sort(key=lambda item: (item["safety_priority"], item["distance_m"]))
        return results if limit is None else results[:limit]
