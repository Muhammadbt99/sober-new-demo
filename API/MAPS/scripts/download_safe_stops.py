"""Build safe_stops_nyc.csv from a local OpenStreetMap extract.

Safe stops are police stations, hospitals, clinics, parking and rest areas.
They power /nearest_safe without depending on a live Overpass mirror.

Usage:
    python scripts/download_safe_stops.py --pbf /path/to/NewYork.osm.pbf

Output columns: latitude, longitude, name, type, osm_id.
Data © OpenStreetMap contributors, ODbL 1.0.
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

AMENITIES = ("police", "hospital", "clinic", "parking")
HIGHWAY_TYPES = ("rest_area",)
OUTPUT = os.path.join(MODULE_DIR, "safe_stops_nyc.csv")
METADATA = os.path.join(MODULE_DIR, "safe_stops_metadata.json")
SOURCE_URL = "https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf"


def in_bbox(lat, lon):
    lat_min, lat_max, lon_min, lon_max = NYC_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _stop_type(tags):
    amenity = tags.get("amenity")
    if amenity in AMENITIES:
        return amenity
    highway = tags.get("highway")
    if highway in HIGHWAY_TYPES:
        return highway
    return None


def _way_centroid(way):
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


def write_rows(rows, output, pbf_path=None):
    """Write the safe-stop rows.  Returns False (and writes nothing) if empty.

    An empty result usually means a failed or truncated scan, so the existing
    CSV is left untouched instead of being replaced by an empty file.
    """
    if not rows:
        print(f"WARNING: no safe stops found - {output} left untouched")
        return False

    rows.sort(key=lambda row: (row["type"], row["latitude"], row["longitude"]))
    with open(output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["latitude", "longitude", "name", "type", "osm_id"],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} safe stops -> {output}")

    by_type = {}
    for row in rows:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
    metadata = {
        "source": "OpenStreetMap (BBBike New York extract)",
        "source_url": SOURCE_URL,
        "source_file": os.path.basename(pbf_path) if pbf_path else "unknown",
        "license": "ODbL 1.0",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "attribution": "© OpenStreetMap contributors",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bbox": list(NYC_BBOX),
        "count": len(rows),
        "by_type": by_type,
    }
    metadata_path = os.path.join(os.path.dirname(output),
                                 os.path.basename(METADATA))
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)
    print(f"Wrote metadata -> {metadata_path}")
    return True


def from_pbf(pbf_path, output):
    import osmium

    rows = []

    class Handler(osmium.SimpleHandler):
        def _append(self, tags, osm_id, lat, lon):
            stop_type = _stop_type(tags)
            if stop_type is None or not in_bbox(lat, lon):
                return
            rows.append({
                "latitude": round(lat, 7),
                "longitude": round(lon, 7),
                "name": tags.get("name", ""),
                "type": stop_type,
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
            centroid = _way_centroid(way)
            if centroid is None:
                return
            self._append(way.tags, way.id, centroid[0], centroid[1])

    print(f"Scanning {pbf_path} (nodes + ways, locations enabled) ...")
    Handler().apply_file(pbf_path, locations=True)
    write_rows(rows, output, pbf_path=pbf_path)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", required=True, help="Path to a local .osm.pbf extract")
    parser.add_argument("--output", default=OUTPUT)
    args = parser.parse_args()

    rows = from_pbf(args.pbf, args.output)
    if not rows:
        raise SystemExit("No safe stops found - check the extract coverage and bbox")
    print("Sample:", rows[:3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
