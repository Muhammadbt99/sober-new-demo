"""Compare the crash model with a baseline whose smoothing was chosen on validation.

The gate in train.py compared the model against a baseline tuned on its own
training rows, which always picks the least smoothing. This script redoes the
comparison fairly and writes the result to ``baseline.fair`` in
crash_risk_metadata.json. It does not touch the model file.

Run from API/MAPS (about 7 to 10 minutes):

    python -u -m crash_model.fair_baseline
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dataset  # noqa: E402
import train  # noqa: E402
from train import EmpiricalBayes, evaluate, fit_model, predict_model, tune_baseline  # noqa: E402
from weather import ensure_weather  # noqa: E402


def model_deciles(metrics: dict) -> list[dict]:
    """The model's weighted deciles, in the shape model_card() serves."""
    return [
        {
            "decile": row["decile"],
            "predicted_rate": row["predicted_rate"],
            "observed_rate": row["observed_rate"],
            "lift": row["lift"],
        }
        for row in metrics["calibration"]
    ]


def temporal_fair(split, best_config: dict, shrink: float) -> dict:
    y_test = split.test["count"].to_numpy(float)
    w_test = split.test["weight"].to_numpy(float)
    combined = pd.concat([split.train, split.validation], ignore_index=True)

    model = fit_model(best_config, combined)
    model_test = evaluate(y_test, predict_model(model, split.test), w_test, best_config["name"])
    baseline = EmpiricalBayes(shrink).fit(combined)
    base_test = evaluate(y_test, baseline.predict(split.test), w_test, f"eb_shrink_{shrink}")
    return {
        "poisson_deviance": base_test["poisson_deviance"],
        "top_decile_lift": base_test["top_decile_lift"],
        "model_top_decile_lift": model_test["top_decile_lift"],
        "model_calibration": model_deciles(model_test),
        "_model_deviance": model_test["poisson_deviance"],
    }


def holdout_fair(crashes: pd.DataFrame, mapping: pd.Series, best_config: dict, shrink: float) -> dict:
    # Same construction as the Brooklyn holdout in train.main().
    holdout_cells = sorted(mapping[mapping == train.HOLDOUT_BOROUGH].index)
    train_crashes = crashes[~crashes["cell"].isin(holdout_cells)]
    holdout_crashes = crashes[crashes["cell"].isin(holdout_cells)]
    print(f"  train boroughs: {len(train_crashes):,} crashes")
    print(f"  held-out Brooklyn: {len(holdout_crashes):,} crashes in {len(holdout_cells)} cells")

    holdout_train = dataset.build_events(
        train_crashes,
        ensure_weather(),
        yearly_crashes=train_crashes,
        cells=sorted(train_crashes["cell"].unique()),
        borough_map=mapping,
    )
    holdout_train = holdout_train[holdout_train["timestamp"] < pd.Timestamp(train.TRAIN_END)]
    holdout_eval = dataset.build_events(
        holdout_crashes[holdout_crashes["timestamp"] >= pd.Timestamp(train.TRAIN_END)],
        ensure_weather(),
        yearly_crashes=train_crashes,
        cells=holdout_cells,
        borough_map=mapping,
    )
    print(f"  holdout train={len(holdout_train):,} eval={len(holdout_eval):,}")

    y_eval = holdout_eval["count"].to_numpy(float)
    w_eval = holdout_eval["weight"].to_numpy(float)
    holdout_baseline = EmpiricalBayes(shrink).fit(holdout_train)
    base = evaluate(y_eval, holdout_baseline.predict(holdout_eval), w_eval, "eb_holdout")
    holdout_model = fit_model(best_config, holdout_train)
    model = evaluate(
        y_eval, predict_model(holdout_model, holdout_eval), w_eval, f"{best_config['name']}_holdout"
    )
    return {
        "poisson_deviance": base["poisson_deviance"],
        "top_decile_lift": base["top_decile_lift"],
        "model_top_decile_lift": model["top_decile_lift"],
        "model_calibration": model_deciles(model),
        "_model_deviance": model["poisson_deviance"],
    }


def main() -> int:
    with open(train.METADATA_PATH, encoding="utf-8") as handle:
        metadata = json.load(handle)
    best_config = metadata["selected_config"]

    print("loading crashes...", flush=True)
    crashes = dataset.load_crashes()
    print("building frame...", flush=True)
    frame = dataset.build_events(crashes, ensure_weather())
    split = dataset.temporal_split(frame, train.TRAIN_END, train.VALIDATION_END)
    print(
        f"  split: train={len(split.train):,} validation={len(split.validation):,} "
        f"test={len(split.test):,}",
        flush=True,
    )

    print("\nfair baseline (shrink chosen on validation):", flush=True)
    _, fair_shrink = tune_baseline(split.train, split.validation)
    fair_shrink = float(fair_shrink)
    print(f"  fair shrink_hours = {fair_shrink}", flush=True)

    print("\ntemporal test (model and baseline both fit on train+validation):", flush=True)
    temporal = temporal_fair(split, best_config, fair_shrink)
    print(f"  model deviance     {temporal.pop('_model_deviance'):.6f}")
    print(f"  baseline deviance  {temporal['poisson_deviance']:.6f}")
    print(f"  baseline lift      {temporal['top_decile_lift']:.3f}")
    print(f"  model lift         {temporal['model_top_decile_lift']:.3f}", flush=True)

    print("\nspatial holdout (Brooklyn):", flush=True)
    mapping = dataset.cell_borough_map(crashes)
    holdout = holdout_fair(crashes, mapping, best_config, fair_shrink)
    print(f"  model deviance     {holdout.pop('_model_deviance'):.6f}")
    print(f"  baseline deviance  {holdout['poisson_deviance']:.6f}")
    print(f"  baseline lift      {holdout['top_decile_lift']:.3f}")
    print(f"  model lift         {holdout['model_top_decile_lift']:.3f}", flush=True)

    fair = {
        "shrink_hours": fair_shrink,
        "temporal_test": temporal,
        "holdout": holdout,
    }
    metadata["baseline"]["fair"] = fair
    with open(train.METADATA_PATH, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
    print(f"\nwrote fair baseline to {train.METADATA_PATH}")
    print(json.dumps(fair, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
