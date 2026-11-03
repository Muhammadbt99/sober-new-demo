"""Offline, weighted crash-density lookup for New York City.

The live app used to call the Overpass API for an "accident density" feature.
This module answers the same question from the bundled ``crashes.csv`` with a
BallTree, so no network access is required at serving time.

Severity weights (per crash inside the radius):
    fatal (>=1 killed)      -> 3.0
    injury (>=1 injured)    -> 2.0
    property damage only    -> 1.0

Only numpy, pandas and scikit-learn are used.
"""

import os

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_KM = 6371.0088

# The crash dataset (crashes.csv) only covers New York City.
NYC_BBOX = (40.4, 41.0, -74.5, -73.5)  # lat_min, lat_max, lon_min, lon_max

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CSV = os.path.join(_MODULE_DIR, "crashes.csv")


def _as_radians(lats, lons):
    """Return an (n, 2) array of (lat, lon) in radians for BallTree."""
    return np.column_stack(
        (np.radians(np.asarray(lats, dtype=float)),
         np.radians(np.asarray(lons, dtype=float)))
    )


def _segment_sums(neighbour_indices, weights):
    """Sum ``weights`` per query using one prefix-sum over flattened neighbours.

    ``neighbour_indices`` is the object array returned by
    ``BallTree.query_radius`` (one index array per query).  The concatenated
    neighbour weights get a single ``cumsum``; each query's total is then the
    difference between two positions in that prefix sum.  This avoids a Python
    loop over individual neighbours (and over individual rows).
    """
    n_queries = len(neighbour_indices)
    lengths = np.fromiter(
        (len(idx) for idx in neighbour_indices), dtype=np.int64, count=n_queries
    )
    totals = np.zeros(n_queries, dtype=float)
    total_neighbours = int(lengths.sum())
    if total_neighbours == 0:
        return totals

    flat_indices = np.concatenate(neighbour_indices).astype(np.intp, copy=False)
    prefix = np.cumsum(weights[flat_indices])

    ends = np.cumsum(lengths) - 1
    starts = ends - lengths + 1

    non_empty = lengths > 0
    totals[non_empty] = prefix[ends[non_empty]]
    before_start = starts[non_empty] > 0
    # Subtract everything that came before this query's slice.
    totals[non_empty] -= np.where(
        before_start, prefix[np.maximum(starts[non_empty] - 1, 0)], 0.0
    )
    return totals


class CrashIndex:
    """Spatial index of historical crashes with severity weights.

    Parameters
    ----------
    csv_path:
        Path to the crash CSV.  Defaults to ``crashes.csv`` located next to
        this module (not the current working directory).
    radius_km:
        Default search radius in kilometres.

    If loading fails for any reason the index degrades gracefully:
    ``available`` is ``False`` and every density query returns zero.
    """

    def __init__(self, csv_path=None, radius_km=1.0):
        self.csv_path = str(csv_path) if csv_path is not None else _DEFAULT_CSV
        self.radius_km = float(radius_km)
        self.available = False

        self.lats = np.empty(0, dtype=float)
        self.lons = np.empty(0, dtype=float)
        self.weights = np.empty(0, dtype=float)
        self.tree = None
        self._self_weight = {}

        try:
            self._load()
        except Exception as exc:  # pragma: no cover - defensive fallback
            print(
                f"[geo] WARNING: could not build CrashIndex from "
                f"{self.csv_path!r} ({type(exc).__name__}: {exc}). "
                "Crash density will be reported as 0."
            )
            self.available = False
            self.tree = None
            self.lats = np.empty(0, dtype=float)
            self.lons = np.empty(0, dtype=float)
            self.weights = np.empty(0, dtype=float)
            self._self_weight = {}

    # ------------------------------------------------------------------ load
    def _load(self):
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"crash CSV not found: {self.csv_path}")

        columns = [
            "latitude",
            "longitude",
            "number_of_persons_injured",
            "number_of_persons_killed",
        ]
        frame = pd.read_csv(self.csv_path, usecols=columns, low_memory=False)
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        frame = frame.dropna(subset=["latitude", "longitude"])
        frame = frame[
            (frame["latitude"] != 0)
            & (frame["longitude"] != 0)
        ]

        lat_min, lat_max, lon_min, lon_max = NYC_BBOX
        frame = frame[
            (frame["latitude"] >= lat_min)
            & (frame["latitude"] <= lat_max)
            & (frame["longitude"] >= lon_min)
            & (frame["longitude"] <= lon_max)
        ]

        if frame.empty:
            raise ValueError("no usable crash rows after filtering")

        injured = frame["number_of_persons_injured"].fillna(0).to_numpy(dtype=float)
        killed = frame["number_of_persons_killed"].fillna(0).to_numpy(dtype=float)

        # 3 = fatal, 2 = injury, 1 = property damage only.  Missing counts
        # are treated as 0, so they fall through to the property-damage weight.
        weights = np.ones(len(frame), dtype=float)
        weights[injured > 0] = 2.0
        weights[killed > 0] = 3.0

        self.lats = frame["latitude"].to_numpy(dtype=float)
        self.lons = frame["longitude"].to_numpy(dtype=float)
        self.weights = weights

        # Exact-coordinate lookup for the "exclude self" variant.  Keyed on
        # rounded coordinates; one crash weight per coordinate.
        self._self_weight = {}
        lat_keys = np.round(self.lats, 6)
        lon_keys = np.round(self.lons, 6)
        for key, weight in zip(zip(lat_keys.tolist(), lon_keys.tolist()),
                               weights.tolist()):
            if key not in self._self_weight:
                self._self_weight[key] = weight

        self.tree = BallTree(_as_radians(self.lats, self.lons),
                             metric="haversine")
        self.available = True

    # --------------------------------------------------------------- queries
    def _radius_radians(self, radius_km):
        radius = self.radius_km if radius_km is None else float(radius_km)
        return max(radius, 0.0) / EARTH_KM

    def _query_zero(self, n):
        return np.zeros(n, dtype=float)

    def densities(self, lats, lons, radius_km=None):
        """Weighted crash density for each (lat, lon) pair.

        Returns a float array; non-finite inputs get 0.0.
        """
        lats = np.atleast_1d(np.asarray(lats, dtype=float))
        lons = np.atleast_1d(np.asarray(lons, dtype=float))
        if lats.shape != lons.shape:
            raise ValueError("lats and lons must have the same shape")

        result = self._query_zero(lats.size)
        if not self.available or lats.size == 0:
            return result

        finite = np.isfinite(lats) & np.isfinite(lons)
        if not np.any(finite):
            return result

        query_points = _as_radians(lats[finite], lons[finite])
        neighbours = self.tree.query_radius(
            query_points, r=self._radius_radians(radius_km), return_distance=False
        )
        result[finite] = _segment_sums(neighbours, self.weights)
        return result

    def density(self, lat, lon, radius_km=None):
        """Weighted crash density at a single point (float)."""
        values = self.densities([lat], [lon], radius_km=radius_km)
        return float(values[0])

    def densities_excluding_self(self, lats, lons, radius_km=None):
        """Like :meth:`densities`, but does not count a crash at the same spot.

        When an input coordinate exactly matches a crash coordinate (rounded
        to 6 decimals), one matching crash weight is removed.  This prevents a
        training row from counting its own crash as density (label leakage).
        """
        lats = np.atleast_1d(np.asarray(lats, dtype=float))
        lons = np.atleast_1d(np.asarray(lons, dtype=float))
        if lats.shape != lons.shape:
            raise ValueError("lats and lons must have the same shape")

        result = self.densities(lats, lons, radius_km=radius_km)
        if not self.available or not self._self_weight:
            return result

        finite = np.isfinite(lats) & np.isfinite(lons)
        if not np.any(finite):
            return result

        lat_keys = np.round(lats[finite], 6).tolist()
        lon_keys = np.round(lons[finite], 6).tolist()
        own = np.fromiter(
            (self._self_weight.get((la, lo), 0.0)
             for la, lo in zip(lat_keys, lon_keys)),
            dtype=float,
            count=len(lat_keys),
        )
        result[finite] = np.maximum(result[finite] - own, 0.0)
        return result
