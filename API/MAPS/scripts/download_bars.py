"""Build bars_nyc.csv: nightlife venues (bar / pub / nightclub) in the NYC box.

Two acquisition paths:

1. From a local OpenStreetMap extract (recommended, reproducible):
       python scripts/download_bars.py --pbf /path/to/NewYork.osm.pbf

   Any BBBike / Geofabrik .osm.pbf extract covering New York works. BBBike's
   NewYork extract is ~153 MB and is what produced the shipped CSV.

2. From the Overpass API, when a reachable mirror exists:
       python scripts/download_bars.py --overpass

Output columns: latitude, longitude, name, amenity, osm_id.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, MODULE_DIR)

from features import NYC_BBOX  # noqa: E402

AMENITIES = ("bar", "pub", "nightclub")
OUTPUT = os.path.join(MODULE_DIR, "bars_nyc.csv")
METADATA = os.path.join(MODULE_DIR, "bars_metadata.json")
SOURCE_URL = "https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def in_bbox(lat, lon):
    lat_min, lat_max, lon_min, lon_max = NYC_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def write_rows(rows, output, pbf_path=None):
    """Write the venue rows.  Returns False (and writes nothing) if empty.

    An empty result usually means a failed or truncated fetch, so the
    existing CSV is left untouched instead of being replaced by an empty file.
    """
    if not rows:
        print(f"WARNING: no venues found - {output} left untouched")
        return False

    rows.sort(key=lambda r: (r["latitude"], r["longitude"]))
    with open(output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["latitude", "longitude", "name", "amenity", "osm_id"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} venues -> {output}")

    by_type = {}
    for row in rows:
        by_type[row["amenity"]] = by_type.get(row["amenity"], 0) + 1
    metadata = {
        "source": "OpenStreetMap (BBBike NewYork extract)" if pbf_path else "OpenStreetMap (Overpass API)",
        "source_url": SOURCE_URL if pbf_path else "https://overpass-api.de/",
        "source_file": os.path.basename(pbf_path) if pbf_path else "Overpass API",
        "license": "ODbL 1.0",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "attribution": "© OpenStreetMap contributors",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bbox": list(NYC_BBOX),
        "count": len(rows),
        "by_type": by_type,
    }
    metadata_path = os.path.join(os.path.dirname(output), "bars_metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)
    print(f"Wrote metadata -> {metadata_path}")
    return True


def _way_centroid(way):
    """Average the locations of a way's nodes (None if no located nodes)."""
    lats = []
    lons = []
    for node_ref in way.nodes:
        try:
            location = node_ref.location
        except Exception:
            continue
        if not location.valid():
            continue
        lats.append(location.lat)
        lons.append(location.lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def from_pbf(pbf_path, output):
    import osmium

    rows = []

    class Handler(osmium.SimpleHandler):
        def _append(self, tags, osm_id, lat, lon):
            amenity = tags.get("amenity")
            if amenity not in AMENITIES:
                return
            if not in_bbox(lat, lon):
                return
            rows.append({
                "latitude": round(lat, 7),
                "longitude": round(lon, 7),
                "name": tags.get("name", ""),
                "amenity": amenity,
                "osm_id": osm_id,
            })

        def node(self, node):
            try:
                location = node.location
            except Exception:
                return
            if not location.valid():
                return
            self._append(node.tags, node.id, location.lat, location.lon)

        def way(self, way):
            # Venues mapped as building polygons / area features.
            centroid = _way_centroid(way)
            if centroid is None:
                return
            self._append(way.tags, way.id, centroid[0], centroid[1])

    print(f"Scanning {pbf_path} (nodes + ways, locations enabled) ...")
    Handler().apply_file(pbf_path, locations=True)
    write_rows(rows, output, pbf_path=pbf_path)
    return rows


def from_overpass(output, timeout=300):
    import requests

    lat_min, lat_max, lon_min, lon_max = NYC_BBOX
    amenity_filter = "^(" + "|".join(AMENITIES) + ")$"
    # Nodes *and* ways: the PBF path collects both, so the Overpass path has
    # to as well or the two produce different datasets (and therefore
    # different BARS_MEDIUM / BARS_HIGH calibration).
    # `out center` gives ways a centre point; nodes carry lat/lon directly.
    query = (
        '[out:json][timeout:240];'
        f'(node["amenity"~"{amenity_filter}"]'
        f'({lat_min},{lon_min},{lat_max},{lon_max});'
        f'way["amenity"~"{amenity_filter}"]'
        f'({lat_min},{lon_min},{lat_max},{lon_max});'
        ');'
        'out center;'
    )

    data = None
    for url in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                url, data={"data": query}, timeout=timeout,
                headers={"User-Agent": "SOBER-Project/1.0"},
            )
            print(f"{url} -> HTTP {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                break
        except Exception as exc:
            print(f"{url} failed: {exc}")

    if not data:
        raise SystemExit("Overpass unavailable; pass --pbf with a local extract instead")

    rows = []
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        amenity = tags.get("amenity")
        if amenity not in AMENITIES:
            continue
        lat, lon = element.get("lat"), element.get("lon")
        if lat is None or lon is None:
            # Ways carry their centroid under "center" (because of `out center`).
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        if not in_bbox(lat, lon):
            continue
        rows.append({
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "name": tags.get("name", ""),
            "amenity": amenity,
            "osm_id": element.get("id"),
        })

    write_rows(rows, output)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pbf", help="Path to a local .osm.pbf extract")
    source.add_argument("--overpass", action="store_true", help="Download from Overpass")
    parser.add_argument("--output", default=OUTPUT)
    args = parser.parse_args()

    if args.pbf:
        rows = from_pbf(args.pbf, args.output)
    else:
        rows = from_overpass(args.output)

    if rows:
        by_type = {}
        for row in rows:
            by_type[row["amenity"]] = by_type.get(row["amenity"], 0) + 1
        print("By type:", by_type)
        print("Sample:", rows[:3])
    else:
        print("WARNING: no venues found - check the extract coverage and bbox")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
