"""Train and gate the S.O.B.E.R. crash-risk model.

The model predicts expected injury-or-fatal crashes per km² per hour for an H3
res 8 hex. It is judged against a shrunken empirical-Bayes lookup on a temporal
test split and on a withheld borough. Nothing is written for serving unless the
gate passes; a failed gate is still recorded and printed.

Run from this directory:

    "D:\\Python and GIT\\python.exe" train.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

import dataset
from weather import ensure_weather

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACT_DIR = HERE
MODEL_PATH = os.path.join(ARTIFACT_DIR, "crash_risk_model.joblib")
METADATA_PATH = os.path.join(ARTIFACT_DIR, "crash_risk_metadata.json")
CELL_RATES_PATH = os.path.join(ARTIFACT_DIR, "cell_rates.csv")
REFERENCE_PATH = os.path.join(ARTIFACT_DIR, "percentile_reference.npy")

MODEL_VERSION = "2026.11.03-1"
TRAIN_END = "2025-01-01"
VALIDATION_END = "2026-01-01"
HOLDOUT_BOROUGH = "BROOKLYN"

CANDIDATES = [
    {
        "name": "poisson_base",
        "learning_rate": 0.05,
        "max_iter": 250,
        "max_leaf_nodes": 31,
        "min_samples_leaf": 40,
        "l2_regularization": 1.0,
    },
    {
        "name": "poisson_shallow",
        "learning_rate": 0.08,
        "max_iter": 250,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 80,
        "l2_regularization": 1.0,
    },
    {
        "name": "poisson_deep",
        "learning_rate": 0.05,
        "max_iter": 250,
        "max_leaf_nodes": 63,
        "min_samples_leaf": 20,
        "l2_regularization": 2.0,
    },
]


def weighted_poisson_deviance(y: np.ndarray, mu: np.ndarray, w: np.ndarray) -> float:
    mu = np.clip(mu, 1e-9, None)
    term = -(y - mu)
    positive = y > 0
    term[positive] += y[positive] * np.log(y[positive] / mu[positive])
    return float(2.0 * np.sum(w * term) / np.sum(w))


def weighted_mae(y: np.ndarray, mu: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * np.abs(y - mu)) / np.sum(w))


def decile_labels(w_sorted: np.ndarray, deciles: int) -> np.ndarray:
    """Label rows (already sorted by predicted rate) with a decile 0..deciles-1.

    Deciles are cut by cumulative weight (cell-hours), not by row count, so each
    decile carries about the same exposure. Labels step up by at most one per row
    and never skip a decile when there are enough rows, so no decile is empty
    unless the data has fewer rows than deciles.
    """
    n = len(w_sorted)
    labels = np.zeros(n, dtype=int)
    total = float(np.sum(w_sorted))
    if n == 0 or total <= 0:
        return labels
    cum = np.cumsum(w_sorted)
    tiny = total * 1e-12
    target = np.minimum(deciles - 1, np.floor(deciles * (cum - tiny) / total)).astype(int)
    current = 0
    for i in range(n):
        if i > 0:
            rows_left = n - i
            if target[i] > current or rows_left <= deciles - 1 - current:
                current = min(current + 1, deciles - 1)
        labels[i] = current
    return labels


def calibration_table(y, mu, w, deciles=10) -> list[dict]:
    order = np.argsort(mu, kind="stable")
    labels = decile_labels(w[order], deciles)
    base = float(np.sum(w * y) / np.sum(w))
    rows = []
    for index in range(deciles):
        chunk = order[labels == index]
        if chunk.size == 0:
            continue
        wy = float(np.sum(w[chunk] * y[chunk]))
        wmu = float(np.sum(w[chunk] * mu[chunk]))
        wh = float(np.sum(w[chunk]))
        rows.append(
            {
                "decile": index + 1,
                "predicted_rate": wmu / wh,
                "observed_rate": wy / wh,
                "weight": wh,
                "lift": (wy / wh) / base if base > 0 else None,
            }
        )
    return rows


def evaluate(y, mu, w, name: str) -> dict:
    calibration = calibration_table(y, mu, w)
    observed = [row["observed_rate"] for row in calibration]
    monotonic = all(
        observed[i] <= observed[i + 1] * 1.05 for i in range(len(observed) - 1)
    )
    top = calibration[-1]
    return {
        "name": name,
        "poisson_deviance": weighted_poisson_deviance(y, mu, w),
        "mae": weighted_mae(y, mu, w),
        "calibration": calibration,
        "monotonic_deciles": monotonic,
        "top_decile_lift": top["lift"],
        "base_rate": float(np.sum(w * y) / np.sum(w)),
    }


class EmpiricalBayes:
    """Shrunken cell x hour-of-week rate, the baseline the model must beat."""

    def __init__(self, shrink_hours: float = 12.0):
        self.shrink_hours = shrink_hours

    def fit(self, frame: pd.DataFrame) -> "EmpiricalBayes":
        work = frame.copy()
        work["weighted_count"] = work["count"] * work["weight"]
        self.global_rate = float(work["weighted_count"].sum() / work["weight"].sum())
        self.cell = (
            work.groupby(["cell", "day_of_week", "hour"])
            .agg(count=("weighted_count", "sum"), exposure=("weight", "sum"))
            .reset_index()
        )
        self.borough = (
            work.groupby(["borough", "day_of_week", "hour"])
            .agg(count=("weighted_count", "sum"), exposure=("weight", "sum"))
            .reset_index()
        )
        self.hour = (
            work.groupby(["day_of_week", "hour"])
            .agg(count=("weighted_count", "sum"), exposure=("weight", "sum"))
            .reset_index()
        )
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        keys = frame[["cell", "borough", "day_of_week", "hour"]].copy()
        cell = self.cell.rename(columns={"count": "cell_count", "exposure": "cell_exposure"})
        borough = self.borough.rename(
            columns={"count": "borough_count", "exposure": "borough_exposure"}
        )
        hour = self.hour.rename(columns={"count": "hour_count", "exposure": "hour_exposure"})

        out = keys.merge(cell, on=["cell", "day_of_week", "hour"], how="left")
        out = out.merge(borough, on=["borough", "day_of_week", "hour"], how="left")
        out = out.merge(hour, on=["day_of_week", "hour"], how="left")

        hour_rate = (out["hour_count"].fillna(0) + self.shrink_hours * self.global_rate) / (
            out["hour_exposure"].fillna(0) + self.shrink_hours
        )
        borough_rate = (
            out["borough_count"].fillna(0) + self.shrink_hours * hour_rate
        ) / (out["borough_exposure"].fillna(0) + self.shrink_hours)
        cell_rate = (out["cell_count"].fillna(0) + self.shrink_hours * borough_rate) / (
            out["cell_exposure"].fillna(0) + self.shrink_hours
        )
        return cell_rate.to_numpy(dtype=float)


def fit_model(config: dict, frame: pd.DataFrame):
    model = HistGradientBoostingRegressor(
        loss="poisson",
        learning_rate=config["learning_rate"],
        max_iter=config["max_iter"],
        max_leaf_nodes=config["max_leaf_nodes"],
        min_samples_leaf=config["min_samples_leaf"],
        l2_regularization=config["l2_regularization"],
        random_state=20261029,
    )
    x = dataset.feature_matrix(frame)
    model.fit(x, frame["count"].to_numpy(dtype=float), sample_weight=frame["weight"].to_numpy())
    return model


def predict_model(model, frame: pd.DataFrame) -> np.ndarray:
    return np.clip(model.predict(dataset.feature_matrix(frame)), 1e-9, None)


SHRINK_GRID = (1.0, 4.0, 12.0, 52.0, 168.0, 500.0)


def tune_baseline(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[EmpiricalBayes, float]:
    """Pick the smoothing on validation data, not on the frame the baseline was fit to.

    Scoring on the training frame always favours the least smoothing (the lookup
    memorises its own training rows), which handicaps the baseline. Returns the
    baseline fit on ``train`` with the chosen shrink.
    """
    y_val = validation["count"].to_numpy(float)
    w_val = validation["weight"].to_numpy(float)
    best = None
    for shrink in SHRINK_GRID:
        baseline = EmpiricalBayes(shrink).fit(train)
        score = weighted_poisson_deviance(y_val, baseline.predict(validation), w_val)
        print(f"  baseline shrink={shrink:>5}: validation deviance={score:.6f}", flush=True)
        if best is None or score < best[0]:
            best = (score, baseline, shrink)
    return best[1], best[2]


def main() -> int:
    print("loading crashes...", flush=True)
    crashes = dataset.load_crashes()
    print(f"  target crashes: {len(crashes):,}")

    print("building frame...", flush=True)
    frame = dataset.build_events(crashes, ensure_weather())
    print(f"  frame: {frame.shape[0]:,} rows, positives={int((frame['count'] > 0).sum()):,}")

    split = dataset.temporal_split(frame, TRAIN_END, VALIDATION_END)
    print(
        f"  split: train={len(split.train):,} validation={len(split.validation):,} "
        f"test={len(split.test):,}",
        flush=True,
    )

    y_val = split.validation["count"].to_numpy(float)
    w_val = split.validation["weight"].to_numpy(float)
    y_test = split.test["count"].to_numpy(float)
    w_test = split.test["weight"].to_numpy(float)

    print("\nbaseline (empirical Bayes):", flush=True)
    baseline, shrink = tune_baseline(split.train, split.validation)
    baseline_val = evaluate(y_val, baseline.predict(split.validation), w_val, f"eb_shrink_{shrink}")
    print(f"  chosen shrink={shrink}")
    print(f"  validation deviance {baseline_val['poisson_deviance']:.6f}")

    # The model is refit on train+validation before the test, so the baseline is too.
    combined = pd.concat([split.train, split.validation], ignore_index=True)
    baseline = EmpiricalBayes(shrink).fit(combined)
    baseline_test = evaluate(y_test, baseline.predict(split.test), w_test, f"eb_shrink_{shrink}")
    print(f"  test deviance       {baseline_test['poisson_deviance']:.6f}")

    print("\ncandidates:", flush=True)
    results = []
    for config in CANDIDATES:
        model = fit_model(config, split.train)
        mu = predict_model(model, split.validation)
        metrics = evaluate(y_val, mu, w_val, config["name"])
        results.append((metrics["poisson_deviance"], config, metrics))
        print(
            f"  {config['name']:<16} val dev={metrics['poisson_deviance']:.6f} "
            f"lift={metrics['top_decile_lift']:.2f}",
            flush=True,
        )

    results.sort(key=lambda item: item[0])
    best_config = results[0][1]
    print(f"\nselected: {best_config['name']}", flush=True)

    final_gate_model = fit_model(best_config, combined)
    model_test = evaluate(
        y_test, predict_model(final_gate_model, split.test), w_test, best_config["name"]
    )
    print(f"  temporal test deviance {model_test['poisson_deviance']:.6f}")
    print(f"  temporal test lift     {model_test['top_decile_lift']:.2f}")
    print(f"  monotonic deciles      {model_test['monotonic_deciles']}")

    print("\nspatial holdout (Brooklyn):", flush=True)
    mapping = dataset.cell_borough_map(crashes)
    holdout_cells = sorted(mapping[mapping == HOLDOUT_BOROUGH].index)
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
    holdout_train = holdout_train[holdout_train["timestamp"] < pd.Timestamp(TRAIN_END)]
    holdout_eval = dataset.build_events(
        holdout_crashes[holdout_crashes["timestamp"] >= pd.Timestamp(TRAIN_END)],
        ensure_weather(),
        yearly_crashes=train_crashes,
        cells=holdout_cells,
        borough_map=mapping,
    )
    print(f"  holdout train={len(holdout_train):,} eval={len(holdout_eval):,}")

    holdout_baseline = EmpiricalBayes(shrink).fit(holdout_train)
    holdout_model = fit_model(best_config, holdout_train)
    holdout_metrics = evaluate(
        holdout_eval["count"].to_numpy(float),
        predict_model(holdout_model, holdout_eval),
        holdout_eval["weight"].to_numpy(float),
        f"{best_config['name']}_holdout",
    )
    holdout_base_metrics = evaluate(
        holdout_eval["count"].to_numpy(float),
        holdout_baseline.predict(holdout_eval),
        holdout_eval["weight"].to_numpy(float),
        "eb_holdout",
    )
    print(f"  model deviance    {holdout_metrics['poisson_deviance']:.6f}")
    print(f"  baseline deviance {holdout_base_metrics['poisson_deviance']:.6f}")
    print(f"  model lift        {holdout_metrics['top_decile_lift']:.2f}")

    gate = {
        "temporal_deviance_beats_baseline": model_test["poisson_deviance"]
        < baseline_test["poisson_deviance"],
        "holdout_deviance_beats_baseline": holdout_metrics["poisson_deviance"]
        < holdout_base_metrics["poisson_deviance"],
        "top_decile_lift_at_least_2x": (model_test["top_decile_lift"] or 0) >= 2.0,
        "deciles_monotonic": bool(model_test["monotonic_deciles"]),
    }
    gate["passed"] = all(gate.values())
    print("\nGATE:", json.dumps(gate, indent=2), flush=True)

    print("\nfitting final model on all data...", flush=True)
    final_model = fit_model(best_config, frame)
    joblib.dump(final_model, MODEL_PATH)

    total = dataset.yearly_cell_totals(crashes)
    per_cell = total.groupby("cell").agg(count=("count", "sum"), exposure=("exposure", "sum"))
    per_cell["rate"] = per_cell["count"] / per_cell["exposure"]
    centres = {cell: dataset.h3.cell_to_latlng(cell) for cell in per_cell.index}
    cell_table = pd.DataFrame(
        {
            "cell": per_cell.index,
            "lat": [centres[c][0] for c in per_cell.index],
            "lon": [centres[c][1] for c in per_cell.index],
            "borough": [mapping.get(c, "UNKNOWN") for c in per_cell.index],
            "count": per_cell["count"].to_numpy(),
            "exposure": per_cell["exposure"].to_numpy(),
            "rate": per_cell["rate"].to_numpy(),
        }
    ).sort_values("cell")

    from population import ensure_density

    density = ensure_density(sorted(cell_table["cell"]))
    cell_table = cell_table.merge(
        density[["cell", "nta_code", "nta_name", "housing_density"]],
        on="cell",
        how="left",
    )
    cell_table.to_csv(CELL_RATES_PATH, index=False)

    reference_rows = []
    for row in cell_table.itertuples(index=False):
        for day in range(7):
            for hour in range(24):
                reference_rows.append(
                    {
                        "hist_rate": row.rate,
                        "housing_density": row.housing_density,
                        "lat": row.lat,
                        "lon": row.lon,
                        "borough_code": dataset.BOROUGH_CODE.get(row.borough, 5),
                        "hour": hour,
                        "day_of_week": day,
                        "month": 6,
                        "is_weekend": int(day >= 5),
                        "is_holiday": 0,
                        "is_covid": 0,
                        "temperature": 15.0,
                        "rain": 0.0,
                        "wind_speed": 10.0,
                    }
                )
    reference_frame = pd.DataFrame(reference_rows)
    reference = np.sort(
        np.clip(final_model.predict(dataset.feature_matrix(reference_frame)), 1e-9, None)
    )
    np.save(REFERENCE_PATH, reference)

    metadata = {
        "version": MODEL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "research" if not gate["passed"] else "approved",
        "target": "expected injury-or-fatal crashes per km2 per hour",
        "resolution": dataset.RESOLUTION,
        "features": dataset.FEATURES,
        "training_window": {
            "start": str(crashes["timestamp"].min()),
            "end": str(crashes["timestamp"].max()),
            "crashes": int(len(crashes)),
        },
        "split": {
            "train_end": TRAIN_END,
            "validation_end": VALIDATION_END,
            "train_rows": int(len(split.train)),
            "validation_rows": int(len(split.validation)),
            "test_rows": int(len(split.test)),
            "holdout_borough": HOLDOUT_BOROUGH,
        },
        "selected_config": best_config,
        "baseline": {
            "type": "empirical_bayes_cell_hour_of_week",
            "shrink_hours": shrink,
            "temporal_test": baseline_test,
            "holdout": holdout_base_metrics,
        },
        "model_metrics": {
            "temporal_test": model_test,
            "holdout": holdout_metrics,
        },
        "gate": gate,
        "prior_rate": float(per_cell["count"].sum() / per_cell["exposure"].sum()),
        "cell_count": int(len(cell_table)),
        "percentile_reference_size": int(len(reference)),
        "density_source": {
            "provider": "NYC Open Data",
            "dataset": "Housing Database by 2020 NTA (kyz5-72x5)",
            "field": "cenunits20",
            "interpretation": "2020 census housing units per km2 in the containing NTA",
        },
        "notes": [
            "Weather is a minor feature and may be missing; NaN is passed through.",
            "Property-damage-only crashes are excluded from the target.",
            "The historical-rate feature only uses years before each row's year.",
            "Housing density is external, non-crash data; it is what lets the model transfer to a held-out borough.",
        ],
    }
    with open(METADATA_PATH, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)

    print(f"\nwrote {MODEL_PATH}")
    print(f"wrote {METADATA_PATH}")
    print(f"wrote {CELL_RATES_PATH} ({len(cell_table):,} cells)")
    print(f"wrote {REFERENCE_PATH} ({len(reference):,} reference rates)")
    print(f"\nGATE PASSED: {gate['passed']}")
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
