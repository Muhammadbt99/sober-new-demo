"""Publish the serving tables for each region after training.

Copies a region's cell features into ``artifacts/`` and stores the quantiles
of the model's rate over every cell and hour of the week, so the API starts
without predicting the whole region. Then prints a few known places as a
sanity check: busy junction-heavy districts should outrank desert motorway.

Usage:
    python publish_regions.py
"""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from global_model.serve import ARTIFACTS, REGIONS, GlobalModel, rel_dow  # noqa: E402
from global_model.sources import WORK  # noqa: E402

CHECKS = {
    "uae": [
        ("Downtown Dubai (Burj Khalifa)", 25.1972, 55.2744),
        ("Deira (Al Rigga)", 25.2654, 55.3260),
        ("Sheikh Zayed Rd at Trade Centre", 25.2205, 55.2830),
        ("Dubai Marina", 25.0805, 55.1403),
        ("Al Barsha residential", 25.1050, 55.2030),
        ("Sharjah Al Wahda St", 25.3292, 55.3946),
        ("Abu Dhabi Corniche", 24.4720, 54.3433),
        ("Al Ain centre", 24.2075, 55.7447),
        ("E11 desert, Ghantoot", 24.8700, 54.8410),
        ("Ras Al Khaimah old town", 25.7900, 55.9430),
    ]
}


def main() -> None:
    os.makedirs(ARTIFACTS, exist_ok=True)
    for key, region in REGIONS.items():
        source = os.path.join(WORK, region.cells_file)
        if not os.path.exists(source):
            print(f"[{key}] no feature table in work/; run build_cities.py {key}")
            continue
        table = pd.read_csv(source, index_col="cell")
        # Percentiles are taken among the hexes Dubai Police actually record.
        recorded = pd.read_csv(os.path.join(WORK, "dubai_cells.csv.gz"), usecols=["cell"])["cell"]
        table["reference"] = table.index.isin(set(recorded)).astype(int)
        table.to_csv(os.path.join(ARTIFACTS, region.cells_file))
        reference_path = os.path.join(ARTIFACTS, region.reference_file)
        if os.path.exists(reference_path):
            os.remove(reference_path)

    model = GlobalModel()
    if not model.available:
        raise SystemExit(f"global model not available: {model.error}")
    for key, region_model in model.regions.items():
        # Loading without a reference file built it from every cell and hour.
        reference = region_model.reference
        np.save(os.path.join(ARTIFACTS, region_model.region.reference_file), reference)
        print(
            f"[{key}] {len(region_model.table):,} cells; rate quantiles "
            f"p10 {reference[1000]:.3f} p50 {reference[5000]:.3f} p90 {reference[9000]:.3f} p99 {reference[9900]:.3f}"
        )
        for name, lat, lon in CHECKS.get(key, []):
            found = model.region_at(lat, lon)
            if found is None:
                print(f"    {name:34s} not in the region table")
                continue
            region, row = found
            line = []
            for hour, day in ((8, 1), (17, 3), (23, 5), (3, 6)):
                rate = float(region.slot(hour, rel_dow(day, region.region.weekend_start))[row])
                line.append(f"{hour:02d}h d{day} p{float(region.percentile(rate)):5.1f}")
            record = region.table.iloc[row]
            print(
                f"    {name:34s} {' | '.join(line)}  junctions {int(record['junctions'])}, "
                f"signals {int(record['signals'])}, pop {int(record['population'])}"
            )


if __name__ == "__main__":
    main()
