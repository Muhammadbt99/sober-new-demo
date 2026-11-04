"""Score the global crash model on Dubai Police incidents it has never seen.

Protocol, fixed before any Dubai score was seen
-----------------------------------------------
* Same split as the gate: the five cities' 2021-2023 crashes train the model,
  Dubai's 2024-2025 records score it. Nothing is tuned on Dubai, and the served
  model is not changed.
* Primary target: collisions the police rate serious or moderate (the closest
  thing in this file to the injury crashes the model learned from).
* Secondary target: every collision, including minor ones.
* Breakdowns, double parking, debris, jams and other non-collisions are dropped.
* Study area: the training cities' rule, applied to primary-target records.

Usage:
    python validate_dubai.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import h3  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from global_model.build_cities import study_area  # noqa: E402
from global_model.osm_features import RESOLUTION  # noqa: E402
from global_model.sources import RAW, WORK, YEARS  # noqa: E402
from global_model.train_global import (  # noqa: E402
    ARTIFACTS, BASELINE, CANDIDATES, CityData, evaluate, fit, predict, time_only,
)
from global_model.sources import CITIES  # noqa: E402

COLLISION = re.compile("صدم|اصطدام|دهس|تدهور|ضد مجهول|سقوط مركبة")
SEVERE = re.compile(r"(?:بليغ|متوسط)\s*$")
#: An exact coordinate repeated this often is a placeholder, not a place.
PLACEHOLDER_REPEATS = 50
TRAIN_YEARS = (2021, 2022, 2023)
TEST_YEARS = (2024, 2025)
WEEKEND_START = 5  # Saturday
SERVED = "roads"


def log(message: str) -> None:
    print(message, flush=True)


def load(years=YEARS) -> tuple[pd.DataFrame, dict]:
    paths = sorted(glob.glob(os.path.join(RAW, "dubai", "*.csv")))
    raw = pd.concat(pd.read_csv(p, encoding="utf-8-sig", low_memory=False) for p in paths)
    notes = {"files": len(paths), "records": int(len(raw))}
    raw = raw.drop_duplicates("acci_id")
    notes["after_duplicate_ids"] = int(len(raw))

    # Most rows hold latitude in acci_x; the feed fixed this during 2026.
    swapped = raw["acci_x"].between(22, 27) & raw["acci_y"].between(51, 57)
    raw["lat"] = raw["acci_y"].where(~swapped, raw["acci_x"])
    raw["lon"] = raw["acci_x"].where(~swapped, raw["acci_y"])
    notes["swapped_coordinates"] = int(swapped.sum())
    located = raw["lat"].between(24.0, 26.2) & raw["lon"].between(54.0, 56.5)
    notes["unlocatable"] = int((~located).sum())
    raw = raw[located].copy()

    repeats = raw.groupby(["lat", "lon"])["acci_id"].transform("size")
    notes["placeholder_coordinates"] = int((repeats >= PLACEHOLDER_REPEATS).sum())
    raw = raw[repeats < PLACEHOLDER_REPEATS].copy()

    time = pd.to_datetime(raw["acci_time"])
    raw["year"], raw["hour"], raw["dow"] = time.dt.year, time.dt.hour, time.dt.dayofweek
    raw = raw[raw["year"].isin(years)].copy()
    names = raw["acci_name"].astype(str)
    raw["collision"] = names.str.contains(COLLISION)
    raw["severe"] = raw["collision"] & names.str.contains(SEVERE)
    notes["records_2021_2025"] = int(len(raw))
    notes["collisions_2021_2025"] = int(raw["collision"].sum())
    notes["severe_2021_2025"] = int(raw["severe"].sum())
    notes["severe_by_year"] = {int(k): int(v) for k, v in raw[raw["severe"]].groupby("year").size().items()}
    raw["cell"] = [
        h3.latlng_to_cell(a, b, RESOLUTION) for a, b in zip(raw["lat"].to_numpy(), raw["lon"].to_numpy())
    ]
    return raw, notes


def counts_for(rows: pd.DataFrame, cells: list[str]) -> np.ndarray:
    index = {cell: i for i, cell in enumerate(cells)}
    place = rows["cell"].map(index)
    rows = rows[place.notna()]
    out = np.zeros((len(cells), 7, 24), dtype=np.int64)
    day = (rows["dow"].to_numpy() - WEEKEND_START) % 7
    np.add.at(out, (place.dropna().astype(int).to_numpy(), day, rows["hour"].to_numpy()), 1)
    return out


class Dubai:
    def __init__(self, table: pd.DataFrame):
        self.table = table


def main() -> None:
    raw, notes = load()
    log(json.dumps(notes, indent=1))
    uae = pd.read_csv(os.path.join(WORK, "uae_cells.csv.gz"), index_col="cell")
    area = study_area(raw.loc[raw["severe"], "cell"])
    cells = [cell for cell in area if cell in uae.index]
    notes["study_cells"] = len(cells)
    notes["study_cells_without_features"] = len(area) - len(cells)
    city = Dubai(uae.loc[cells])

    test = raw[raw["year"].isin(TEST_YEARS)]
    targets = {
        "serious_or_moderate_collisions": counts_for(test[test["severe"]], cells),
        "all_collisions": counts_for(test[test["collision"]], cells),
    }
    cities = {key: CityData(key) for key in CITIES}
    train = [(c, c.years(TRAIN_YEARS)) for c in cities.values()]

    predictions = {"time_only": time_only(train, city)}
    for name in (BASELINE, SERVED):
        log(f"training {name} on the five cities, 2021-2023")
        predictions[name] = predict(fit(train, CANDIDATES[name]), city, CANDIDATES[name])

    results: dict = {}
    for target, observed in targets.items():
        results[target] = {name: evaluate(p, observed) for name, p in predictions.items()}
        for name, row in results[target].items():
            rho = "n/a" if row["cell_spearman"] is None else f"{row['cell_spearman']:.2f}"
            log(
                f"{target:32s} {name:10s} crashes {row['crashes']:7,} lift {row['top_decile_lift']:.2f} "
                f"rho {rho} capture {row['top10_cells_capture']:.0%} dev {row['deviance']:.5f}"
            )
        log("  deciles: " + " ".join(f"{d['lift']:.2f}" for d in results[target][SERVED]["deciles"]))

    out = {
        "protocol": "five cities 2021-2023 -> Dubai 2024-2025; nothing tuned on Dubai",
        "data": notes,
        "served_feature_set": SERVED,
        "results": results,
    }
    with open(os.path.join(ARTIFACTS, "dubai_validation.json"), "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
    log("wrote artifacts/dubai_validation.json")


if __name__ == "__main__":
    main()
