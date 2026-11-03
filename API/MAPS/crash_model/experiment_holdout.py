"""Holdout-only experiment: does external housing density transfer to Brooklyn?"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

import dataset
import train
from weather import ensure_weather

CONFIG = train.CANDIDATES[2]


def fit(frame, features):
    model = HistGradientBoostingRegressor(
        loss="poisson",
        learning_rate=CONFIG["learning_rate"],
        max_iter=CONFIG["max_iter"],
        max_leaf_nodes=CONFIG["max_leaf_nodes"],
        min_samples_leaf=CONFIG["min_samples_leaf"],
        l2_regularization=CONFIG["l2_regularization"],
        random_state=20261029,
    )
    model.fit(
        frame[features].astype(float),
        frame["count"].to_numpy(float),
        sample_weight=frame["weight"].to_numpy(),
    )
    return model


def main():
    crashes = dataset.load_crashes()
    weather = ensure_weather()
    mapping = dataset.cell_borough_map(crashes)
    holdout_cells = sorted(mapping[mapping == train.HOLDOUT_BOROUGH].index)
    train_crashes = crashes[~crashes["cell"].isin(holdout_cells)]
    hold_crashes = crashes[crashes["cell"].isin(holdout_cells)]

    print("building holdout frames...", flush=True)
    holdout_train = dataset.build_events(
        train_crashes,
        weather,
        yearly_crashes=train_crashes,
        cells=sorted(train_crashes["cell"].unique()),
        borough_map=mapping,
    )
    holdout_train = holdout_train[holdout_train["timestamp"] < pd.Timestamp(train.TRAIN_END)]
    holdout_eval = dataset.build_events(
        hold_crashes[hold_crashes["timestamp"] >= pd.Timestamp(train.TRAIN_END)],
        weather,
        yearly_crashes=train_crashes,
        cells=holdout_cells,
        borough_map=mapping,
    )
    print(f"train={len(holdout_train):,} eval={len(holdout_eval):,}", flush=True)

    baseline = train.EmpiricalBayes(1.0).fit(holdout_train)
    base_metrics = train.evaluate(
        holdout_eval["count"].to_numpy(float),
        baseline.predict(holdout_eval),
        holdout_eval["weight"].to_numpy(float),
        "eb",
    )
    print(f"baseline dev={base_metrics['poisson_deviance']:.6f}", flush=True)

    y = holdout_eval["count"].to_numpy(float)
    w = holdout_eval["weight"].to_numpy(float)
    all_features = dataset.FEATURES
    no_density = [f for f in all_features if f != "housing_density"]

    for label, features in (("without density", no_density), ("with density", all_features)):
        model = fit(holdout_train, features)
        mu = np.clip(model.predict(holdout_eval[features].astype(float)), 1e-9, None)
        metrics = train.evaluate(y, mu, w, label)
        print(
            f"{label:<16} dev={metrics['poisson_deviance']:.6f} "
            f"lift={metrics['top_decile_lift']:.2f} "
            f"mono={metrics['monotonic_deciles']}",
            flush=True,
        )
        print(
            "  calib pred/obs:",
            [(round(r['predicted_rate'], 5), round(r['observed_rate'], 5)) for r in metrics['calibration'][::3]],
            flush=True,
        )


if __name__ == "__main__":
    main()
