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
WANTED += [f"vehicle_type_code{i}" for i in range(1, 6)]
WANTED += [f"contributing_factor_vehicle_{i}" for i in range(1, 6)]


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
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()

    print("checking dataset schema...", flush=True)
    real = available_columns()
    select = [c for c in WANTED if c in real]
    missing = [c for c in WANTED if c not in real]
    print(f"  selecting {len(select)} columns")
    if missing:
        print(f"  not in dataset (skipped): {missing}")

    where = f"crash_date >= '{args.since}'"

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
        frame = pd.DataFrame(rows)
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
    frame = pd.read_csv(TMP_PATH, low_memory=False)
    if args.max_rows:
        frame = frame.head(args.max_rows)
    frame.to_csv(OUT_PATH, index=False, compression="gzip")
    os.remove(TMP_PATH)

    size_mb = os.path.getsize(OUT_PATH) / (1024 * 1024)
    print(f"saved {len(frame):,} rows -> {OUT_PATH} ({size_mb:.1f} MB)", flush=True)
    print("columns:", list(frame.columns), flush=True)

    injured = pd.to_numeric(frame.get("number_of_persons_injured"), errors="coerce").fillna(0)
    killed = pd.to_numeric(frame.get("number_of_persons_killed"), errors="coerce").fillna(0)
    print(f"injury rows : {int((injured > 0).sum()):,}", flush=True)
    print(f"fatal rows  : {int((killed > 0).sum()):,}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
