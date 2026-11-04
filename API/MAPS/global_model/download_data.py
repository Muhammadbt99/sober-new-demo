"""Download the open data behind the global crash model.

Everything lands in ``global_model/raw/<source>/`` (git-ignored). Files that
already exist are skipped, so the script can be re-run after a failure.

Sources
-------
Crash records (injury crashes with coordinates and local time):
  * New York   - already in ``API/MAPS/nyc_crashes_rich.csv.gz``
  * Chicago    - City of Chicago Traffic Crashes (SODA 85ca-t3if)
  * London     - DfT STATS19 collision files (all of Great Britain)
  * Paris      - ONISR BAAC "caracteristiques" files (all of France)
  * Berlin     - Destatis Unfallatlas (all of Germany)
  * Dubai      - Dubai Police traffic incidents. The portal needs a sign-in,
                 so this one is downloaded by hand into raw/dubai/.

Cell features:
  * Kontur population on H3 resolution 8 (per country)
  * OpenStreetMap extracts (BBBike city extracts, Geofabrik GCC states)

Usage:
    python download_data.py            # everything
    python download_data.py chicago    # one source
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
USER_AGENT = "SOBER-global-model/1.0 (research download)"

YEARS = [2021, 2022, 2023, 2024, 2025]

KONTUR = "https://geodata-eu-central-1-kontur-public.s3.amazonaws.com/kontur_datasets/kontur_population_{cc}_20220630.gpkg.gz"
BBBIKE = "https://download.bbbike.org/osm/bbbike/{city}/{city}.osm.pbf"
STATS19 = "https://data.dft.gov.uk/road-accidents-safety-data/dft-road-casualty-statistics-collision-{year}.csv"
UNFALLATLAS = "https://www.opengeodata.nrw.de/produkte/transport_verkehr/unfallatlas/Unfallorte{year}_EPSG25832_CSV.zip"
BAAC_DATASET = "https://www.data.gouv.fr/api/1/datasets/53698f4ca3a729239d2036df/"
GEOFABRIK_GCC = "https://download.geofabrik.de/asia/gcc-states-latest.osm.pbf"
CHICAGO = "https://data.cityofchicago.org/resource/85ca-t3if.json"
CHICAGO_FIELDS = [
    "crash_record_id",
    "crash_date",
    "latitude",
    "longitude",
    "injuries_total",
    "injuries_fatal",
    "most_severe_injury",
]


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def fetch(url: str, path: str, *, optional: bool = False) -> bool:
    """Stream ``url`` to ``path`` through a .part file. Returns True if present."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  have {os.path.relpath(path, RAW)} ({os.path.getsize(path) / 1e6:.1f} MB)")
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    started = time.time()
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            last = 0.0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if time.time() - last > 15:
                    last = time.time()
                    share = f" {done / total:.0%}" if total else ""
                    print(f"    {os.path.basename(path)}: {done / 1e6:.0f} MB{share}", flush=True)
    except urllib.error.HTTPError as exc:
        if os.path.exists(part):
            os.remove(part)
        if optional and exc.code == 404:
            print(f"  skip {url} (404)")
            return False
        raise
    os.replace(part, path)
    print(f"  got  {os.path.relpath(path, RAW)} ({os.path.getsize(path) / 1e6:.1f} MB, {time.time() - started:.0f}s)")
    return True


def download_kontur() -> None:
    for cc in ["AE", "US", "GB", "FR", "DE"]:
        fetch(KONTUR.format(cc=cc), os.path.join(RAW, "kontur", f"kontur_population_{cc}.gpkg.gz"))


def download_osm() -> None:
    for city in ["NewYork", "Chicago", "London", "Paris", "Berlin"]:
        fetch(BBBIKE.format(city=city), os.path.join(RAW, "osm", f"{city}.osm.pbf"))
    fetch(GEOFABRIK_GCC, os.path.join(RAW, "osm", "gcc-states.osm.pbf"))


def download_stats19() -> None:
    for year in YEARS:
        fetch(STATS19.format(year=year), os.path.join(RAW, "stats19", f"collision-{year}.csv"), optional=True)


def download_unfallatlas() -> None:
    for year in YEARS:
        fetch(UNFALLATLAS.format(year=year), os.path.join(RAW, "unfallatlas", f"Unfallorte{year}_CSV.zip"), optional=True)


def download_baac() -> None:
    with urllib.request.urlopen(_request(BAAC_DATASET), timeout=60) as response:
        dataset = json.load(response)
    wanted = {}
    for resource in dataset.get("resources", []):
        title = (resource.get("title") or "").lower()
        if not title.endswith(".csv") or not ("caract" in title or "carct" in title):
            continue
        for year in YEARS:
            if str(year) in title:
                wanted[year] = resource["url"]
    for year in YEARS:
        if year in wanted:
            fetch(wanted[year], os.path.join(RAW, "baac", f"caract-{year}.csv"))
        else:
            print(f"  skip BAAC {year} (not published)")


def download_chicago() -> None:
    path = os.path.join(RAW, "chicago", "crashes_injury_2021_2025.csv")
    if os.path.exists(path):
        print(f"  have {os.path.relpath(path, RAW)}")
        return
    import pandas as pd

    where = (
        "injuries_total > 0 AND crash_date >= '2021-01-01T00:00:00' "
        "AND crash_date < '2026-01-01T00:00:00'"
    )
    frames = []
    offset = 0
    page = 50_000
    while True:
        query = urllib.parse.urlencode(
            {
                "$select": ",".join(CHICAGO_FIELDS),
                "$where": where,
                "$order": "crash_record_id",
                "$limit": page,
                "$offset": offset,
            }
        )
        with urllib.request.urlopen(_request(f"{CHICAGO}?{query}"), timeout=120) as response:
            rows = json.load(response)
        # SODA leaves out empty fields, so fix the column order explicitly
        # (the NYC download once lost its alignment this way).
        frames.append(pd.DataFrame(rows).reindex(columns=CHICAGO_FIELDS))
        print(f"    chicago rows {offset + len(rows):,}", flush=True)
        if len(rows) < page:
            break
        offset += page
    frame = pd.concat(frames, ignore_index=True)
    lat = pd.to_numeric(frame["latitude"], errors="coerce")
    lon = pd.to_numeric(frame["longitude"], errors="coerce")
    inside = lat.between(41.6, 42.1) & lon.between(-88.0, -87.5)
    if inside.mean() < 0.95:
        raise SystemExit(f"Chicago coordinates look misaligned ({inside.mean():.1%} inside the city)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frame.to_csv(path + ".part", index=False)
    os.replace(path + ".part", path)
    print(f"  got  chicago/{os.path.basename(path)} ({len(frame):,} injury crashes)")


SOURCES = {
    "kontur": download_kontur,
    "chicago": download_chicago,
    "stats19": download_stats19,
    "baac": download_baac,
    "unfallatlas": download_unfallatlas,
    "osm": download_osm,
}


def main(names: list[str]) -> None:
    for name in names or list(SOURCES):
        print(f"[{name}]", flush=True)
        SOURCES[name]()


if __name__ == "__main__":
    main(sys.argv[1:])
