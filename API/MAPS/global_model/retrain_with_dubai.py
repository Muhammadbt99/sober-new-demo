"""Add Dubai as a sixth training city, only if it earns its place.

Rules, fixed before any score of the retrained model was seen. The feature set
stays "roads". The current model stays unless ALL of these pass:

A. Later years: five cities + Dubai 2021-2023 beats five cities 2021-2023 on
   Dubai 2024-2025 (lower deviance and higher top-decile lift).
B. Unseen districts: the same, on Dubai hexes held out of training (5 folds by
   H3 resolution-5 parent), so memorising streets cannot pass.
C. No harm: leave-one-city-out across six cities, 2021-2023 -> 2024-2025, still
   passes the original gate.
D. Untouched year: the final six-city model beats the currently served model
   on Dubai 2026 (deviance and lift).

Usage:
    python retrain_with_dubai.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import h3  # noqa: E402
import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from global_model.build_cities import study_area  # noqa: E402
from global_model.sources import CITIES, WORK, YEARS  # noqa: E402
from global_model.train_global import (  # noqa: E402
    ARTIFACTS, BASELINE, CANDIDATES, GATE, CityData, evaluate, fit, leave_one_out, predict, summary,
)
from global_model.validate_dubai import (  # noqa: E402
    SERVED, TEST_YEARS, TRAIN_YEARS, WEEKEND_START, counts_for, load,
)

VERSION = "global-2026.11.04-1"
FOLDS = 5
BACKUP = os.path.join(WORK, "before_dubai")
FILES = ["global_crash_model.joblib", "global_crash_metadata.json"]


def log(message: str) -> None:
    print(message, flush=True)


class Part:
    """A city restricted to some of its cells."""

    def __init__(self, city, keep: np.ndarray):
        self.table = city.table[keep]


def build_dubai() -> CityData:
    raw, notes = load()
    uae = pd.read_csv(os.path.join(WORK, "uae_cells.csv.gz"), index_col="cell")
    severe = raw[raw["severe"]]
    cells = [cell for cell in study_area(severe["cell"]) if cell in uae.index]
    counts = np.stack(
        [counts_for(severe[severe["year"] == year], cells) for year in YEARS], axis=1
    ).astype(np.int32)
    uae.loc[cells].to_csv(os.path.join(WORK, "dubai_cells.csv.gz"))
    np.save(os.path.join(WORK, "dubai_counts.npy"), counts)
    meta = {
        "key": "dubai", "name": "Dubai", "country": "AE", "weekend_start": WEEKEND_START,
        "years": list(YEARS), "cells": len(cells), "crashes_in_area": int(counts.sum()), "data": notes,
    }
    with open(os.path.join(WORK, "dubai_meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
    log(f"dubai: {len(cells):,} cells, {int(counts.sum()):,} collisions in the study area")
    return CityData("dubai")


def better(new: dict, old: dict) -> bool:
    return new["deviance"] < old["deviance"] and new["top_decile_lift"] > old["top_decile_lift"]


def brief(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "deciles"}


def show(label: str, old: dict, new: dict) -> None:
    log(
        f"{label}: lift {old['top_decile_lift']:.2f} -> {new['top_decile_lift']:.2f}, "
        f"rho {old['cell_spearman']:.2f} -> {new['cell_spearman']:.2f}, "
        f"deviance {old['deviance']:.5f} -> {new['deviance']:.5f}: {'PASS' if better(new, old) else 'FAIL'}"
    )


def main() -> None:
    started = time.time()
    dubai = build_dubai()
    five = {key: CityData(key) for key in CITIES}
    features = CANDIDATES[SERVED]
    base = [(c, c.years(TRAIN_YEARS)) for c in five.values()]
    dubai_train = dubai.years(TRAIN_YEARS)
    dubai_test = dubai.years(TEST_YEARS)
    old_pred = predict(fit(base, features), dubai, features)

    # A: later years.
    new_pred = predict(fit(base + [(dubai, dubai_train)], features), dubai, features)
    a_old, a_new = evaluate(old_pred, dubai_test), evaluate(new_pred, dubai_test)
    show("A later years", a_old, a_new)

    # B: unseen districts.
    parents = sorted({h3.cell_to_parent(c, 5) for c in dubai.table.index})
    rng = np.random.default_rng(7)
    fold_of = dict(zip(rng.permutation(parents), np.arange(len(parents)) % FOLDS))
    folds = np.array([fold_of[h3.cell_to_parent(c, 5)] for c in dubai.table.index])
    oof = np.zeros_like(old_pred)
    for fold in range(FOLDS):
        out = folds == fold
        model = fit(base + [(Part(dubai, ~out), dubai_train[~out])], features)
        oof[out] = predict(model, Part(dubai, out), features)
        log(f"  fold {fold}: {int(out.sum())} cells")
    b_old, b_new = evaluate(old_pred, dubai_test), evaluate(oof, dubai_test)
    show("B unseen districts", b_old, b_new)

    # C: no harm.
    six = {**five, "dubai": dubai}
    per_city = leave_one_out(six, TRAIN_YEARS, TEST_YEARS, ["time_only", BASELINE, SERVED])
    gate_summary = summary(per_city, SERVED, list(six))
    checks = {
        "beats_baseline": gate_summary["beats_baseline_in"] >= GATE["beats_baseline_in_at_least"] + 1,
        "mean_top_decile_lift": gate_summary["mean_top_decile_lift"] >= GATE["mean_top_decile_lift"],
        "min_top_decile_lift": gate_summary["min_top_decile_lift"] >= GATE["min_top_decile_lift"],
        "mean_cell_spearman": gate_summary["mean_cell_spearman"] >= GATE["mean_cell_spearman"],
    }
    checks["passed"] = all(checks.values())
    log(f"C six-city gate: {gate_summary} {checks}")

    # D: the untouched year, served model against final model.
    os.makedirs(BACKUP, exist_ok=True)
    if not os.path.exists(os.path.join(BACKUP, FILES[0])):
        for name in FILES:
            shutil.copy2(os.path.join(ARTIFACTS, name), os.path.join(BACKUP, name))
    served = joblib.load(os.path.join(BACKUP, FILES[0]))
    final = fit([(c, c.counts.sum(axis=1)) for c in six.values()], features)
    raw, _ = load(years=(2026,))
    observed = counts_for(raw[raw["severe"]], list(dubai.table.index))
    d_old = evaluate(predict(served, dubai, features), observed)
    d_new = evaluate(predict(final, dubai, features), observed)
    show("D Dubai 2026", d_old, d_new)

    rules = {
        "later_years": better(a_new, a_old),
        "unseen_districts": better(b_new, b_old),
        "six_city_gate": checks["passed"],
        "untouched_2026": better(d_new, d_old),
    }
    adopted = all(rules.values())
    report = {
        "version": VERSION,
        "adopted": adopted,
        "rules": rules,
        "later_years": {"before": brief(a_old), "after": brief(a_new)},
        "unseen_districts": {"before": brief(b_old), "after": brief(b_new)},
        "untouched_2026": {"before": brief(d_old), "after": brief(d_new)},
    }
    with open(os.path.join(ARTIFACTS, "dubai_retrain.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    log(f"rules {rules}; adopted: {adopted}")
    if adopted:
        with open(os.path.join(BACKUP, FILES[1]), encoding="utf-8") as handle:
            meta = json.load(handle)
        meta["version"] = VERSION
        meta["created_at"] = datetime.now(timezone.utc).isoformat()
        meta["target"] = "crashes per H3 res-8 cell-hour, relative to the training city's mean"
        meta["training"]["dubai"] = {
            "name": "Dubai", "country": "AE", "cells": len(dubai.table),
            "crashes": int(dubai.counts.sum()), "years": list(YEARS),
        }
        meta["protocol"]["gate"] = "other cities 2021-2023 -> held-out city 2024-2025 (six cities)"
        meta["gate"].update(summary=gate_summary, checks=checks, per_city=per_city, best_summary=None)
        meta["dubai_validation"] = None
        joblib.dump(final, os.path.join(ARTIFACTS, FILES[0]), compress=3)
        with open(os.path.join(ARTIFACTS, FILES[1]), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2)
    log(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
