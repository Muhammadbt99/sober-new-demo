"""Per-hex road and land-use features from an OpenStreetMap extract.

For every H3 resolution-8 cell (about 0.74 km2) inside a bounding box:

* road length in km by class (motorway, trunk, primary, secondary, tertiary,
  local streets, service roads) plus footways and cycleways;
* junctions: nodes where three or more street ends meet (service roads such as
  parking aisles and driveways are left out, so car parks do not look like a
  street grid);
* counts of traffic signals, pedestrian crossings, bus stops, roundabouts and
  stations, and of nightlife, food, shops, schools, fuel and parking places.

Tags whose coverage varies between countries (speed limits, lanes) are left
out on purpose: a model trained where they are mapped would read their absence
elsewhere as a property of the road.
"""
from __future__ import annotations

import math
import time
from array import array
from collections import defaultdict

import h3
import numpy as np
import osmium
import pandas as pd

RESOLUTION = 8
PIECE_M = 120.0  # long segments are split so each piece lands in its own cell

DRIVE_CLASS = {
    "motorway": "motorway",
    "motorway_link": "motorway",
    "trunk": "trunk",
    "trunk_link": "trunk",
    "primary": "primary",
    "primary_link": "primary",
    "secondary": "secondary",
    "secondary_link": "secondary",
    "tertiary": "tertiary",
    "tertiary_link": "tertiary",
    "unclassified": "local",
    "residential": "local",
    "living_street": "local",
    "road": "local",
    "service": "service",
}
WALK = {"footway", "pedestrian", "path", "steps"}
CYCLE = {"cycleway"}

ROAD_FEATURES = [
    "km_motorway",
    "km_trunk",
    "km_primary",
    "km_secondary",
    "km_tertiary",
    "km_local",
    "km_service",
    "km_walk",
    "km_cycle",
]
POINT_FEATURES = [
    "junctions",
    "signals",
    "crossings",
    "bus_stops",
    "roundabouts",
    "stations",
    "nightlife",
    "food",
    "shops",
    "schools",
    "fuel",
    "parking",
]
FEATURES = ROAD_FEATURES + POINT_FEATURES

NIGHTLIFE = {"bar", "pub", "nightclub", "biergarten"}
FOOD = {"restaurant", "fast_food", "cafe", "food_court"}
SCHOOLS = {"school", "college", "university", "kindergarten"}

EARTH_M = 6_371_008.8


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def _point_categories(tags) -> list[str]:
    out = []
    highway = tags.get("highway")
    if highway == "traffic_signals":
        out.append("signals")
    elif highway == "crossing":
        out.append("crossings")
    elif highway == "bus_stop":
        out.append("bus_stops")
    amenity = tags.get("amenity")
    if amenity in NIGHTLIFE:
        out.append("nightlife")
    elif amenity in FOOD:
        out.append("food")
    elif amenity in SCHOOLS:
        out.append("schools")
    elif amenity == "fuel":
        out.append("fuel")
    elif amenity == "parking":
        out.append("parking")
    if "shop" in tags:
        out.append("shops")
    if tags.get("railway") == "station" or tags.get("public_transport") == "station":
        out.append("stations")
    return out


def extract(path: str, bbox: tuple[float, float, float, float], *, log=print) -> pd.DataFrame:
    """Features for every cell with any mapped road or place inside ``bbox``.

    ``bbox`` is (south, west, north, east). Returns a frame indexed by H3 cell
    id with one column per name in FEATURES.
    """
    south, west, north, east = bbox

    def inside(lat: float, lon: float) -> bool:
        return south <= lat <= north and west <= lon <= east

    lengths: dict[tuple[str, str], float] = defaultdict(float)
    points: dict[tuple[str, str], int] = defaultdict(int)
    # Street ends per node, for junctions: interior nodes count twice.
    node_ids = array("q")
    node_lat = array("d")
    node_lon = array("d")

    started = time.time()
    seen_ways = 0
    processor = (
        osmium.FileProcessor(path)
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("highway", "amenity", "shop", "railway", "public_transport", "junction"))
    )
    for obj in processor:
        if obj.is_node():
            categories = _point_categories(obj.tags)
            if not categories:
                continue
            location = obj.location
            if not location.valid() or not inside(location.lat, location.lon):
                continue
            cell = h3.latlng_to_cell(location.lat, location.lon, RESOLUTION)
            for category in categories:
                points[(cell, category)] += 1
            continue

        if not obj.is_way():
            continue
        tags = obj.tags
        highway = tags.get("highway")
        nodes = obj.nodes
        if len(nodes) < 2:
            continue
        try:
            first, last = nodes[0].location, nodes[len(nodes) - 1].location
        except osmium.InvalidLocationError:
            continue
        if not (first.valid() and last.valid()):
            continue
        if not inside(first.lat, first.lon) and not inside(last.lat, last.lon):
            continue

        # Places drawn as areas (a restaurant's building) count once, at their centre.
        if highway is None:
            categories = _point_categories(tags)
            if categories:
                lats = [n.location.lat for n in nodes if n.location.valid()]
                lons = [n.location.lon for n in nodes if n.location.valid()]
                if lats:
                    cell = h3.latlng_to_cell(sum(lats) / len(lats), sum(lons) / len(lons), RESOLUTION)
                    for category in categories:
                        points[(cell, category)] += 1
            continue

        if highway in DRIVE_CLASS:
            feature = "km_" + DRIVE_CLASS[highway]
        elif highway in WALK:
            feature = "km_walk"
        elif highway in CYCLE:
            feature = "km_cycle"
        else:
            continue
        seen_ways += 1

        if tags.get("junction") in ("roundabout", "circular") and feature != "km_service":
            location = first
            points[(h3.latlng_to_cell(location.lat, location.lon, RESOLUTION), "roundabouts")] += 1

        street = highway in DRIVE_CLASS and highway != "service"
        previous = None
        count = len(nodes)
        for index, node in enumerate(nodes):
            location = node.location
            if not location.valid():
                previous = None
                continue
            lat, lon = location.lat, location.lon
            if street:
                ends = 1 if index in (0, count - 1) else 2
                for _ in range(ends):
                    node_ids.append(node.ref)
                    node_lat.append(lat)
                    node_lon.append(lon)
            if previous is not None:
                plat, plon = previous
                metres = _haversine_m(plat, plon, lat, lon)
                pieces = max(1, math.ceil(metres / PIECE_M))
                share = metres / pieces / 1000.0
                for k in range(pieces):
                    t = (k + 0.5) / pieces
                    mlat, mlon = plat + (lat - plat) * t, plon + (lon - plon) * t
                    if inside(mlat, mlon):
                        lengths[(h3.latlng_to_cell(mlat, mlon, RESOLUTION), feature)] += share
            previous = (lat, lon)

    log(f"    ways {seen_ways:,}, street ends {len(node_ids):,} ({time.time() - started:.0f}s)")

    # Junctions: three or more street ends at one node.
    if len(node_ids):
        ids = np.frombuffer(node_ids, dtype=np.int64)
        unique, first_index, ends = np.unique(ids, return_index=True, return_counts=True)
        junction_index = first_index[ends >= 3]
        lat_arr = np.frombuffer(node_lat, dtype=np.float64)[junction_index]
        lon_arr = np.frombuffer(node_lon, dtype=np.float64)[junction_index]
        for lat, lon in zip(lat_arr.tolist(), lon_arr.tolist()):
            if inside(lat, lon):
                points[(h3.latlng_to_cell(lat, lon, RESOLUTION), "junctions")] += 1

    rows: dict[str, dict[str, float]] = defaultdict(dict)
    for (cell, feature), value in lengths.items():
        rows[cell][feature] = value
    for (cell, feature), value in points.items():
        rows[cell][feature] = value
    frame = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=FEATURES).fillna(0.0)
    frame.index.name = "cell"
    log(f"    {len(frame):,} cells with features ({time.time() - started:.0f}s)")
    return frame
