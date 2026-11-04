"""Download the rich NYC Motor Vehicle Collisions dataset (h9gi-nx95).

The bundled ``crashes.csv`` has only six columns, which is why severity is
unpredictable from it: vehicle type and contributing factor - the two strongest
severity signals - are missing. This script fetches the full public dataset
(2.27M rows, 20 columns) and keeps the columns needed for severity modelling.

Output: ``nyc_crashes_rich.csv.gz`` in this directory.

Usage:
    python fetch_crashes_rich.py                 # 2020-01-01 onwards
    python fetch_crashes_rich.py --max-rows 5000 # smoke test
    python fetch_crashes_rich.py --since 2023-01-01T00:00:00
    python fetch_crashes_rich.py --until 2026-06-12T00:00:00 --out other.csv.gz

Column order (fixed 2026-11-03)
-------------------------------
The API returns JSON records and leaves out every field that is empty. Each
page used to be built with ``pd.DataFrame(rows)``, whose columns follow the
order in which keys first appear on *that page*, and pages after the first were
appended without a header. A page whose first record had no borough therefore
shifted every later value one column to the left under the first page's header:
nine of thirteen pages in the 2026-10-25 download had vehicle types sitting in
the contributing-factor columns, and two had longitudes in the latitude column.
Every page is now written in one fixed column order, and the finished file is
checked before it is kept (``check_alignment``).
"""

import argparse
import os
import sys
import time

import pandas as pd
import requests

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(MODULE_DIR, "nyc_crashes_rich.csv.gz")
TMP_PATH = os.path.join(MODULE_DIR, "nyc_crashes_rich.partial.csv")

DATASET = "h9gi-nx95"
ROWS_URL = f"https://data.cityofnewyork.us/resource/{DATASET}.json"
META_URL = f"https://data.cityofnewyork.us/api/views/{DATASET}.json"
PAGE = 50000

WANTED = [
    "collision_id",
    "crash_date",
    "crash_time",
    "borough",
    "latitude",
    "longitude",
    "number_of_persons_injured",
    "number_of_persons_killed",
]
# The dataset's own spelling: the first two vehicle columns have no underscore
# before the number, the last three do. A guessed spelling is silently skipped.
WANTED += ["vehicle_type_code1", "vehicle_type_code2"]
WANTED += [f"vehicle_type_code_{i}" for i in range(3, 6)]
WANTED += [f"contributing_factor_vehicle_{i}" for i in range(1, 6)]

BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"}
# Values that are vehicle types, never contributing factors.
VEHICLE_HINT = (
    r"^(sedan|station wagon|taxi|bike|bus|pick-?up|box truck|motorcycle|e-bike|"
    r"e-scooter|moped|tractor truck|ambulance|garbage|dump|flat bed|convertible)"
)


def check_alignment(frame):
    """Return a list of problems if any column holds another column's values.

    A misaligned page shows up as latitudes outside New York, boroughs that are
    not boroughs, or vehicle types in the contributing-factor columns.
    """
    problems = []
    lat = pd.to_numeric(frame.get("latitude"), errors="coerce")
    present = frame.get("latitude").notna()
    outside = present & ~(lat.between(40.4, 41.0) | (lat == 0))
    if outside.mean() > 0.001:
        problems.append(f"latitude outside NYC in {outside.mean():.1%} of rows")
    borough = frame.get("borough")
    if borough is not None:
        wrong = borough.notna() & ~borough.isin(BOROUGHS)
        if wrong.mean() > 0.001:
            problems.append(f"borough not a borough in {wrong.mean():.1%} of rows")
    for column in [c for c in frame.columns if c.startswith("contributing_factor")]:
        values = frame[column].dropna().astype(str)
        if len(values) and values.str.match(VEHICLE_HINT, case=False).mean() > 0.001:
            problems.append(f"vehicle types inside {column}")
    return problems


def available_columns():
    """Return the dataset's real field names, so $select cannot 400 on a guess."""
    meta = requests.get(META_URL, timeout=60).json()
    return {col["fieldName"] for col in meta.get("columns", [])}


def fetch_page(offset, select, where, attempts=5):
    params = {
        "$select": ",".join(select),
        "$where": where,
        "$order": "collision_id",
        "$limit": PAGE,
        "$offset": offset,
    }
    last_error = None
    for attempt in range(attempts):
        try:
            response = requests.get(ROWS_URL, params=params, timeout=180)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        wait = 2 ** attempt
        print(f"  retry {attempt + 1}/{attempts} in {wait}s ({last_error})", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"page at offset {offset} failed: {last_error}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2020-01-01T00:00:00")
    parser.add_argument("--until", default=None, help="exclusive upper bound on crash_date")
    parser.add_argument("--out", default=OUT_PATH, help="output .csv.gz path")
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    out_path = os.path.abspath(args.out)

    print("checking dataset schema...", flush=True)
    real = available_columns()
    select = [c for c in WANTED if c in real]
    missing = [c for c in WANTED if c not in real]
    print(f"  selecting {len(select)} columns")
    if missing:
        print(f"  not in dataset (skipped): {missing}")

    where = f"crash_date >= '{args.since}'"
    if args.until:
        where += f" AND crash_date < '{args.until}'"

    if os.path.exists(TMP_PATH):
        os.remove(TMP_PATH)

    total = 0
    offset = 0
    started = time.time()
    while True:
        print(f"fetching offset {offset}...", flush=True)
        rows = fetch_page(offset, select, where)
        if not rows:
            break
        # One fixed column order for every page (see the module docstring).
        frame = pd.DataFrame(rows).reindex(columns=select)
        frame.to_csv(
            TMP_PATH,
            mode="a",
            header=not os.path.exists(TMP_PATH) or offset == 0,
            index=False,
        )
        total += len(rows)
        print(f"  +{len(rows)} (total {total:,}) in {time.time() - started:.0f}s", flush=True)
        offset += PAGE
        if args.max_rows and total >= args.max_rows:
            print("  reached --max-rows", flush=True)
            break
        if len(rows) < PAGE:
            break
        time.sleep(0.3)

    print(f"\nread {total:,} rows; converting to gzip...", flush=True)
    frame = pd.read_csv(TMP_PATH, low_memory=False, dtype=str)
    if args.max_rows:
        frame = frame.head(args.max_rows)
    problems = check_alignment(frame)
    if problems:
        # Keep the partial file for inspection; never ship a misaligned file.
        print("ALIGNMENT CHECK FAILED: " + "; ".join(problems), flush=True)
        return 1
    frame.to_csv(out_path, index=False, compression="gzip")
    os.remove(TMP_PATH)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"saved {len(frame):,} rows -> {out_path} ({size_mb:.1f} MB)", flush=True)
    print("columns:", list(frame.columns), flush=True)

    injured = pd.to_numeric(frame.get("number_of_persons_injured"), errors="coerce").fillna(0)
    killed = pd.to_numeric(frame.get("number_of_persons_killed"), errors="coerce").fillna(0)
    print(f"injury rows : {int((injured > 0).sum()):,}", flush=True)
    print(f"fatal rows  : {int((killed > 0).sum()):,}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
