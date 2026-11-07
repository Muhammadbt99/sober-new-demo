"""Hospitals for the response-gap map, from the local OpenStreetMap extracts.

    python -m global_model.build_hospitals

OpenStreetMap is the one source that covers both New York and the UAE, but its
health entries need cleaning before anyone is told how far away help is:

* places tagged ``emergency=no`` are dropped; most hospitals carry no such tag
  either way, so the map says "mapped hospital", never "emergency department";
* names that are clearly not general hospitals (dental, dermatology, eye and
  fertility clinics) are dropped;
* a "medical center" is a major hospital in New York and a small clinic in the
  UAE, so the name alone decides nothing: it is dropped only with no emergency
  tag and no "hospital" in its name. An explicit ``emergency=yes`` always stays;
* the same hospital mapped twice, as a point and an outline say, is kept once;
* entries emptied by their mappers ("تمت ازالته" means "removed") are dropped.

The UAE rows get a stricter rule on top of that (``uae_hospital``), because the
loose one let in Qatar and Oman, unnamed entries, police stations, car parks and
dentists. In order: a blank name is dropped; a point outside the UAE is dropped
(``providers.country_offline``); junk names are dropped; the hand-checked major
hospitals in ``UAE_MAJOR`` are kept, whatever their tags; anything else is kept
only if its name says "hospital" or "مستشفى". An ``emergency=yes`` tag does not
rescue a UAE name, since several such entries are small clinics. The NYC rows
are unchanged by this rule.

The list is written to ``artifacts/hospitals.csv`` so the app does not need the
raw extracts. It says where OpenStreetMap has a hospital, not which are open.
"""
from __future__ import annotations

import math
import os
import re

import osmium
import pandas as pd

from providers import country_offline

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw", "osm")
OUTPUT = os.path.join(HERE, "artifacts", "hospitals.csv")

REGIONS = {
    "nyc": {"file": "NewYork.osm.pbf", "box": (40.45, -74.30, 40.95, -73.65)},
    "uae": {"file": "gcc-states.osm.pbf", "box": (22.4, 51.5, 26.5, 56.5)},
}
NOT_A_HOSPITAL = re.compile(
    r"dental|dentist|derma|skin|beauty|cosmetic|aesthetic|fertility|\bivf\b|\beye\b|optic|physio|"
    r"pharmacy|veterinary|\bvet\b|\blab\b|laboratory|nursing home|hospice|rehab|psychiatr|"
    r"dialysis|diagnostic|ayurved|speech|day surgery|diabetes|تمت ازالته",
    re.IGNORECASE,
)
#: Clinics in most places, but the name of major hospitals in others.
MAYBE_A_CLINIC = re.compile(
    r"medical cent|health cent|healthcare cent|health care cent|primary health|polyclinic|poly clinic|orthop|"
    r"(?<!cleveland )\bclinics?\b",
    re.IGNORECASE,
)
HOSPITAL_NAME = re.compile(r"hospital|mediclinic", re.IGNORECASE)
#: Real hospitals OpenStreetMap has under another tag. Checked by hand.
MAPPED_WITHOUT_THE_TAG = {"Latifa Hospital"}
SAME_PLACE_M = 200.0

#: UAE names that are never junk: their mappers tag some of them as clinics, and
#: some have no "hospital" in the name. Matched case-insensitively.
UAE_MAJOR = {
    "burjeel medical city",
    "sheikh shakhbout medical city",
    "sheikh khalifa medical city",
    "sheikh tahnoon bin mohammed medical city",
    "cleveland clinic abu dhabi",
    "danat al emarat",
    "mediclinic parkview",
    "mustashfa sagar",
    "doctor sulaiman al habib",
    "thumbay medi city",
}
UAE_JUNK = re.compile(r"family care cent|outpatient|hr building|buraimi|كرونا|^مستشفى$", re.IGNORECASE)
UAE_HOSPITAL_WORD = re.compile(r"hospital|مستشفى", re.IGNORECASE)


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    x = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2.0))
    y = math.radians(b[0] - a[0])
    return 6_371_000.0 * math.hypot(x, y)


def keep(name: str, emergency: str | None) -> bool:
    """Whether an OpenStreetMap entry counts as a hospital someone could go to."""
    if emergency == "no" or NOT_A_HOSPITAL.search(name or ""):
        return False
    if emergency == "yes" or "hospital" in (name or "").lower():
        return True
    return not MAYBE_A_CLINIC.search(name or "")


def merge_duplicates(rows: list[dict]) -> list[dict]:
    """One row per place: emergency care first, then the most hospital-like name."""
    # A name that says "hospital" beats a stray label on the same spot: Fakeeh
    # University Hospital was otherwise kept as "HLG Umar".
    ordered = sorted(
        rows,
        key=lambda row: (row["emergency"] != "yes", "hospital" not in row["name"].lower(), not row["name"]),
    )
    kept: list[dict] = []
    for row in ordered:
        if not any(_metres((row["lat"], row["lon"]), (other["lat"], other["lon"])) <= SAME_PLACE_M for other in kept):
            kept.append(row)
    return kept


def uae_hospital(name: str, lat: float, lon: float) -> bool:
    """The strict UAE rule, applied to rows the extraction has already found."""
    name = (name or "").strip()
    if not name:
        return False
    if country_offline(lat, lon) != "AE":
        return False
    if UAE_JUNK.search(name):
        return False
    if name.lower() in UAE_MAJOR:
        return True
    return UAE_HOSPITAL_WORD.search(name) is not None


def extract(path: str, box: tuple[float, float, float, float], extra_names: frozenset[str] = frozenset()) -> list[dict]:
    """Hospitals in a box, in three light passes.

    Asking osmium for every location, or for assembled areas, holds the whole
    extract's nodes in memory, which took a 16 GB machine down on the 257 MB Gulf
    file. So: find the hospitals, then read only the ways and nodes they use.

    Entries whose lower-cased name is in ``extra_names`` are always candidates,
    whatever their tags, and skip ``keep()``; the UAE rule judges them later.
    """
    south, west, north, east = box
    found: list[dict] = []
    for item in osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION).with_filter(
        osmium.filter.KeyFilter("amenity", "healthcare", "name")
    ):
        tags = dict(item.tags)
        name = tags.get("name:en") or tags.get("name") or ""
        wanted = name.lower() in extra_names
        # Some major hospitals are mapped only as a building (Mediclinic Parkview),
        # but so are outpatient centres, so the name has to say so too.
        hospital = (
            tags.get("amenity") == "hospital"
            or tags.get("healthcare") == "hospital"
            or (tags.get("building") == "hospital" and HOSPITAL_NAME.search(name) is not None)
        )
        if not (hospital or wanted or (item.is_node() and name in MAPPED_WITHOUT_THE_TAG)):
            continue
        if not wanted and not keep(name, tags.get("emergency")):
            continue
        row = {"name": name, "emergency": tags.get("emergency") or "", "at": None, "nodes": [], "ways": []}
        if item.is_node():
            row["at"] = (item.location.lat, item.location.lon)
        elif item.is_way():
            row["nodes"] = [node.ref for node in item.nodes]
        else:
            row["ways"] = [member.ref for member in item.members if member.type == "w" and member.role in ("outer", "")]
        found.append(row)

    way_ids = {way for row in found for way in row["ways"]}
    way_nodes: dict[int, list[int]] = {}
    if way_ids:
        for way in osmium.FileProcessor(path, osmium.osm.WAY).with_filter(osmium.filter.IdFilter(way_ids)):
            way_nodes[way.id] = [node.ref for node in way.nodes]
    for row in found:
        for way in row["ways"]:
            row["nodes"].extend(way_nodes.get(way, []))

    node_ids = {node for row in found for node in row["nodes"]}
    places: dict[int, tuple[float, float]] = {}
    if node_ids:
        for node in osmium.FileProcessor(path, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(node_ids)):
            places[node.id] = (node.location.lat, node.location.lon)

    rows = []
    for row in found:
        points = [places[node] for node in row["nodes"] if node in places]
        at = row["at"] or (
            (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))
            if points
            else None
        )
        if at is not None and south <= at[0] <= north and west <= at[1] <= east:
            rows.append({"lat": round(at[0], 6), "lon": round(at[1], 6), "name": row["name"], "emergency": row["emergency"]})
    return rows


def main() -> None:
    frames = []
    for region, spec in REGIONS.items():
        if region == "uae":
            rows = extract(os.path.join(RAW, spec["file"]), spec["box"], extra_names=frozenset(UAE_MAJOR))
            rows = [row for row in rows if uae_hospital(row["name"], row["lat"], row["lon"])]
        else:
            rows = extract(os.path.join(RAW, spec["file"]), spec["box"])
        rows = merge_duplicates(rows)
        print(region, len(rows), "hospitals,", sum(1 for row in rows if row["emergency"] == "yes"), "tagged with emergency care")
        frames.append(pd.DataFrame(rows).assign(region=region))
    pd.concat(frames)[["region", "lat", "lon", "name", "emergency"]].to_csv(OUTPUT, index=False, encoding="utf-8")
    print("wrote", OUTPUT)


if __name__ == "__main__":
    main()
