"""Train the crash-severity model on the rich NYC collision dataset.

The previous model was trained from ``crashes.csv`` (six columns: date, time,
lat, lon, injured, killed) and scored below the majority-class baseline
(ROC-AUC 0.473, accuracy 0.542 vs 0.634, fatal recall 0.0). That was not a
tuning failure. Severity is driven by *what was involved* - a bicycle, a
motorcycle, unsafe speed, alcohol - and none of those columns existed.

This script trains on ``nyc_crashes_rich.csv.gz`` (NYC Open Data h9gi-nx95),
which adds vehicle types and contributing factors, and reports:

* a candidate comparison on a validation split (no test-set peeking),
* 5-fold cross-validation of the winning configuration,
* the final held-out test metrics, next to the majority-class baseline,
* an **ablation** trained on context features only (hour, place, borough).
  The gap between the two is the evidence for the original diagnosis: context
  alone cannot predict severity; crash attributes can.

Honesty note
------------
Vehicle type and contributing factor are recorded by the responding officer
*after* the crash, and the recorded factor can be shaped by how bad the crash
was. The model is therefore **retrospective**: it describes crashes that
already happened. It is not a pre-crash risk predictor, and it is not used for
``/risk`` scoring.

Usage:
    python fetch_crashes_rich.py      # once, downloads the dataset
    python train.py
"""

import gc
import json
import os
import sys
import time
from datetime import datetime, timezone

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
import sklearn  # noqa: E402
from sklearn.base import clone  # noqa: E402
from sklearn.dummy import DummyClassifier  # noqa: E402
from sklearn.ensemble import (  # noqa: E402
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, train_test_split  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from severity_features import (  # noqa: E402
    CLASS_NAMES,
    CONTEXT_FEATURES,
    SEVERITY_FEATURES,
    build_feature_frame,
    build_severity_labels,
    in_nyc_bbox,
)

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(MODULE_DIR, "nyc_crashes_rich.csv.gz")
MODEL_PATH = os.path.join(MODULE_DIR, "model.pkl")
METRICS_PATH = os.path.join(MODULE_DIR, "cv_results.json")
CONFUSION_PATH = os.path.join(MODULE_DIR, "confusion_matrix.png")
IMPORTANCE_PATH = os.path.join(MODULE_DIR, "feature_importance.png")

RANDOM_STATE = 42
SELECTION_ROWS = 200_000
IMPORTANCE_ROWS = 10_000

sns.set_theme(style="whitegrid")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


def load_dataset():
    if not os.path.exists(DATA_PATH):
        raise SystemExit(
            f"Missing {DATA_PATH}.\n"
            "Run `python fetch_crashes_rich.py` first (downloads ~637k NYC crashes)."
        )

    print(f"loading {os.path.basename(DATA_PATH)}...", flush=True)
    df = pd.read_csv(DATA_PATH, low_memory=False)
    print(f"  raw rows: {len(df):,}")

    for column in ("latitude", "longitude", "crash_date", "crash_time"):
        if column not in df:
            raise SystemExit(f"dataset is missing required column {column!r}")

    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    dates = pd.to_datetime(df["crash_date"], errors="coerce")

    keep = lat.notna() & lon.notna() & dates.notna() & in_nyc_bbox(lat, lon)
    df = df.loc[keep].copy()
    print(f"  inside NYC bbox with a valid date: {len(df):,}")

    if "collision_id" in df:
        before = len(df)
        df = df.drop_duplicates(subset="collision_id", keep="first")
        if before != len(df):
            print(f"  dropped {before - len(df):,} duplicate collision ids")

    df = df.reset_index(drop=True)
    return df


def describe_dataset(df, labels):
    counts = labels.value_counts().sort_index()
    total = int(counts.sum())
    return {
        "source": "NYC Open Data h9gi-nx95 - Motor Vehicle Collisions - Crashes",
        "file": os.path.basename(DATA_PATH),
        "rows_used": total,
        "date_range": [
            str(pd.to_datetime(df["crash_date"], errors="coerce").min())[:10],
            str(pd.to_datetime(df["crash_date"], errors="coerce").max())[:10],
        ],
        "class_counts": {CLASS_NAMES[i]: int(counts.get(i, 0)) for i in range(3)},
        "class_share": {
            CLASS_NAMES[i]: round(float(counts.get(i, 0)) / total, 6) for i in range(3)
        },
    }


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def evaluate(model, X, y, name):
    started = time.perf_counter()
    predictions = model.predict(X)
    probabilities = model.predict_proba(X)
    elapsed = time.perf_counter() - started

    labels = list(range(3))
    one_hot = np.zeros_like(probabilities)
    one_hot[np.arange(len(y)), y.to_numpy()] = 1.0

    report = classification_report(
        y, predictions, labels=labels, target_names=CLASS_NAMES, output_dict=True, zero_division=0
    )

    try:
        roc_auc = float(
            roc_auc_score(y, probabilities, multi_class="ovr", average="macro", labels=labels)
        )
    except ValueError:
        roc_auc = float("nan")

    try:
        fatal_pr_auc = float(average_precision_score((y == 2).astype(int), probabilities[:, 2]))
    except ValueError:
        fatal_pr_auc = float("nan")

    return {
        "name": name,
        "accuracy": round(float(accuracy_score(y, predictions)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y, predictions)), 4),
        "macro_f1": round(float(f1_score(y, predictions, average="macro", labels=labels)), 4),
        "roc_auc_macro": round(roc_auc, 4) if roc_auc == roc_auc else None,
        "fatal_pr_auc": round(fatal_pr_auc, 4) if fatal_pr_auc == fatal_pr_auc else None,
        "brier_score": round(float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))), 5),
        "per_class_recall": {
            name_: round(float(report[name_]["recall"]), 4) for name_ in CLASS_NAMES
        },
        "per_class_precision": {
            name_: round(float(report[name_]["precision"]), 4) for name_ in CLASS_NAMES
        },
        "per_class_f1": {name_: round(float(report[name_]["f1-score"]), 4) for name_ in CLASS_NAMES},
        "confusion_matrix": confusion_matrix(y, predictions, labels=labels).tolist(),
        "predict_seconds": round(elapsed, 2),
    }


def measure_calibration(model, X, y, deciles=10):
    """Compare predicted probabilities with observed frequencies on held-out data.

    Class weighting deliberately distorts absolute probabilities in exchange for
    rare-class recall, so the distortion has to be measured and published rather
    than assumed away. The reliability table shows whether the *ranking* is still
    usable even when the level is not.
    """
    probabilities = model.predict_proba(X)
    labels = y.to_numpy()

    per_class = {}
    for index, name in enumerate(CLASS_NAMES):
        mean_predicted = float(probabilities[:, index].mean())
        observed = float((labels == index).mean())
        per_class[name] = {
            "mean_predicted": round(mean_predicted, 5),
            "observed_rate": round(observed, 5),
            "ratio": round(mean_predicted / observed, 2) if observed > 0 else None,
        }

    fatal = probabilities[:, 2]
    order = np.argsort(fatal)
    reliability = []
    for rank, indices in enumerate(np.array_split(order, deciles), start=1):
        reliability.append(
            {
                "decile": rank,
                "rows": int(len(indices)),
                "mean_predicted": round(float(fatal[indices].mean()), 5),
                "observed_fatal_rate": round(float((labels[indices] == 2).mean()), 5),
            }
        )

    return {
        "per_class": per_class,
        "fatal_reliability": reliability,
        "note": (
            "Class weighting distorts absolute probabilities on purpose: it buys "
            "rare-class recall. Compare scenarios against each other or classes "
            "within a row; do not read fatal_risk as a real-world fatality rate."
        ),
    }


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #


def candidates():
    """Named configurations, all fitted on the same selection sample."""
    return [
        ("dummy_majority", DummyClassifier(strategy="most_frequent")),
        (
            "logreg_balanced",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    (
                        "model",
                        LogisticRegression(
                            max_iter=400,
                            class_weight="balanced",
                            random_state=RANDOM_STATE,
                        ),
                    ),
                ]
            ),
        ),
        (
            "random_forest_balanced",
            RandomForestClassifier(
                n_estimators=200,
                min_samples_leaf=5,
                max_features="sqrt",
                class_weight="balanced_subsample",
                # n_jobs=-1 exhausted the Windows commit limit on this machine.
                # Tree seeds come from random_state, so this changes speed only.
                n_jobs=2,
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "extra_trees_balanced",
            ExtraTreesClassifier(
                n_estimators=300,
                min_samples_leaf=3,
                max_features="sqrt",
                class_weight="balanced",
                n_jobs=2,
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "hist_gb_unweighted",
            HistGradientBoostingClassifier(
                max_iter=250,
                learning_rate=0.08,
                max_leaf_nodes=63,
                min_samples_leaf=40,
                early_stopping=True,
                validation_fraction=0.1,
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "hist_gb_balanced",
            HistGradientBoostingClassifier(
                max_iter=250,
                learning_rate=0.08,
                max_leaf_nodes=63,
                min_samples_leaf=40,
                early_stopping=True,
                validation_fraction=0.1,
                class_weight="balanced",
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "hist_gb_fatal10",
            HistGradientBoostingClassifier(
                max_iter=250,
                learning_rate=0.08,
                max_leaf_nodes=63,
                min_samples_leaf=40,
                early_stopping=True,
                validation_fraction=0.1,
                class_weight={0: 1.0, 1: 1.0, 2: 10.0},
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "hist_gb_fatal30",
            HistGradientBoostingClassifier(
                max_iter=250,
                learning_rate=0.08,
                max_leaf_nodes=63,
                min_samples_leaf=40,
                early_stopping=True,
                validation_fraction=0.1,
                class_weight={0: 1.0, 1: 1.0, 2: 30.0},
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "hist_gb_fatal30_slow",
            HistGradientBoostingClassifier(
                max_iter=500,
                learning_rate=0.04,
                max_leaf_nodes=127,
                min_samples_leaf=60,
                l2_regularization=1.0,
                early_stopping=True,
                validation_fraction=0.1,
                class_weight={0: 1.0, 1: 1.0, 2: 30.0},
                random_state=RANDOM_STATE,
            ),
        ),
        (
            "hist_gb_fatal30_deep",
            HistGradientBoostingClassifier(
                max_iter=400,
                learning_rate=0.06,
                max_leaf_nodes=255,
                min_samples_leaf=20,
                l2_regularization=0.5,
                early_stopping=True,
                validation_fraction=0.1,
                class_weight={0: 1.0, 1: 1.0, 2: 30.0},
                random_state=RANDOM_STATE,
            ),
        ),
    ]


def fit_and_score(name, estimator, X_fit, y_fit, X_eval, y_eval):
    started = time.perf_counter()
    estimator.fit(X_fit, y_fit)
    fit_seconds = round(time.perf_counter() - started, 1)
    result = evaluate(estimator, X_eval, y_eval, name)
    result["fit_seconds"] = fit_seconds
    print(
        f"  {name:24s} acc={result['accuracy']:.4f} macroF1={result['macro_f1']:.4f} "
        f"fatalRecall={result['per_class_recall']['fatal_risk']:.4f} "
        f"fatalPR={result['fatal_pr_auc']} ({fit_seconds}s)",
        flush=True,
    )
    return result


# --------------------------------------------------------------------------- #
# Plots
# --------------------------------------------------------------------------- #


def plot_confusion(matrix, path):
    array = np.asarray(matrix, dtype=float)
    row_totals = array.sum(axis=1, keepdims=True)
    share = np.divide(array, row_totals, out=np.zeros_like(array), where=row_totals > 0)

    annotations = np.empty_like(array, dtype=object)
    for i in range(array.shape[0]):
        for j in range(array.shape[1]):
            annotations[i, j] = f"{int(array[i, j]):,}\n{share[i, j]:.0%}"

    short = ["Property", "Injury", "Fatal"]
    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=140)
    sns.heatmap(
        share,
        annot=annotations,
        fmt="",
        cmap="Blues",
        cbar_kws={"label": "share of true class"},
        xticklabels=short,
        yticklabels=short,
        ax=ax,
        vmin=0,
        vmax=1,
    )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Crash-severity confusion matrix (held-out test set)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {os.path.basename(path)}", flush=True)


def plot_importance(importance, path, top_n=20):
    ranked = sorted(importance.items(), key=lambda item: item[1]["mean"], reverse=True)[:top_n]
    names = [item[0] for item in ranked][::-1]
    means = [item[1]["mean"] for item in ranked][::-1]
    errors = [item[1]["std"] for item in ranked][::-1]

    fig, ax = plt.subplots(figsize=(7.4, 6.4), dpi=140)
    colours = ["#2e9e5b" if value > 0 else "#e0524e" for value in means]
    ax.barh(names, means, xerr=errors, color=colours, error_kw={"elinewidth": 0.8, "capsize": 2})
    ax.axvline(0, color="#333333", linewidth=0.8)
    ax.set_xlabel("Drop in macro-F1 when the feature is shuffled")
    ax.set_title("Permutation importance (held-out test set)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {os.path.basename(path)}", flush=True)


def calibrate_only():
    """Regenerate the calibration report for the model already on disk.

    A full retrain would refit an identical model; this path exists so a change
    to *reporting* still produces the artifact from code rather than a hand
    edit. It rebuilds the same split, so the numbers describe the same test rows.
    """
    started = time.perf_counter()
    df = load_dataset()
    labels = build_severity_labels(df)
    print("building features...", flush=True)
    features = build_feature_frame(df)
    _, X_test, _, y_test = train_test_split(
        features, labels, test_size=0.20, stratify=labels, random_state=RANDOM_STATE
    )
    del df, features, labels
    gc.collect()

    model = joblib.load(MODEL_PATH)
    calibration = measure_calibration(model, X_test, y_test)
    fatal_ratio = calibration["per_class"]["fatal_risk"]["ratio"]

    with open(METRICS_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)

    payload["calibration"] = calibration
    honesty = [
        line
        for line in payload.get("honesty", [])
        if not line.startswith("Predicted probabilities")
    ]
    honesty.append(
        "Predicted probabilities are class-weighted, not calibrated: the fatal "
        f"score averages {fatal_ratio}x the observed fatality rate. The ranking "
        "is informative; the absolute value is not a real-world fatality rate."
    )
    payload["honesty"] = honesty

    with open(METRICS_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")

    print(
        f"calibration written in {time.perf_counter() - started:.0f}s "
        f"(fatal score {fatal_ratio}x the observed rate)",
        flush=True,
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main():
    started = time.perf_counter()
    df = load_dataset()
    labels = build_severity_labels(df)
    print("building features...", flush=True)
    features = build_feature_frame(df)
    print(f"  feature matrix: {features.shape[0]:,} x {features.shape[1]}", flush=True)

    dataset_info = describe_dataset(df, labels)
    print("  class counts:", dataset_info["class_counts"], flush=True)

    # 70 / 10 / 20 stratified split. The validation split chooses the model;
    # the test split is touched exactly once, for the headline numbers.
    X_train_val, X_test, y_train_val, y_test = train_test_split(
        features, labels, test_size=0.20, stratify=labels, random_state=RANDOM_STATE
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val,
        y_train_val,
        test_size=0.125,
        stratify=y_train_val,
        random_state=RANDOM_STATE,
    )
    print(
        f"  split: train={len(X_train):,} val={len(X_val):,} test={len(X_test):,}",
        flush=True,
    )

    # A fixed stratified sample keeps the eight-way comparison affordable and
    # fair: every candidate sees exactly the same rows.
    if len(X_train) > SELECTION_ROWS:
        X_select, _, y_select, _ = train_test_split(
            X_train,
            y_train,
            train_size=SELECTION_ROWS,
            stratify=y_train,
            random_state=RANDOM_STATE,
        )
    else:
        X_select, y_select = X_train, y_train
    print(f"  selection sample: {len(X_select):,}", flush=True)

    print("\n=== candidate comparison (fitted on selection sample, scored on validation) ===")
    results = []
    fitted = {}
    for name, estimator in candidates():
        result = fit_and_score(name, estimator, X_select, y_select, X_val, y_val)
        results.append(result)
        fitted[name] = estimator

    by_macro_f1 = sorted(
        results,
        key=lambda item: (item["macro_f1"], item["per_class_recall"]["fatal_risk"]),
        reverse=True,
    )
    baseline_accuracy = next(
        item for item in results if item["name"] == "dummy_majority"
    )["accuracy"]

    # A model that is less accurate than "always predict property damage" is
    # not deployable, however good its rare-class recall is. Prefer the best
    # macro-F1 among models that at least match the baseline; if none do, fall
    # back to the unconstrained best and say so rather than hiding it.
    eligible = [item for item in by_macro_f1 if item["accuracy"] >= baseline_accuracy]
    ranked = eligible or by_macro_f1

    print(f"\n  ranking by macro-F1 (baseline accuracy {baseline_accuracy:.4f}):")
    for position, result in enumerate(by_macro_f1, 1):
        mark = "" if result in eligible else "  [below baseline accuracy]"
        print(
            f"    {position}. {result['name']:24s} macroF1={result['macro_f1']:.4f} "
            f"acc={result['accuracy']:.4f} fatalRecall={result['per_class_recall']['fatal_risk']:.4f}"
            f"{mark}"
        )
    if eligible and eligible[0]["name"] != by_macro_f1[0]["name"]:
        print(
            f"  note: {by_macro_f1[0]['name']} had the highest macro-F1 "
            f"({by_macro_f1[0]['macro_f1']:.4f}) but is less accurate than the "
            "baseline, so it is not selected."
        )

    best_name = ranked[0]["name"]
    best_estimator = fitted[best_name]
    print(f"\n  winner: {best_name}", flush=True)

    print("\n=== 5-fold cross-validation of the winner (selection sample) ===")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    fold_accuracy = []
    fold_macro_f1 = []
    fold_fatal_recall = []
    for fold, (train_index, valid_index) in enumerate(cv.split(X_select, y_select), 1):
        clone_model = clone(best_estimator)
        clone_model.fit(X_select.iloc[train_index], y_select.iloc[train_index])
        predictions = clone_model.predict(X_select.iloc[valid_index])
        truth = y_select.iloc[valid_index].to_numpy()
        fold_accuracy.append(float(accuracy_score(truth, predictions)))
        fold_macro_f1.append(float(f1_score(truth, predictions, average="macro", labels=[0, 1, 2])))
        fold_fatal_recall.append(
            float(((predictions == 2) & (truth == 2)).sum() / max(1, int((truth == 2).sum())))
        )
        print(
            f"  fold {fold}: acc={fold_accuracy[-1]:.4f} macroF1={fold_macro_f1[-1]:.4f} "
            f"fatalRecall={fold_fatal_recall[-1]:.4f}",
            flush=True,
        )

    print("\n=== final fit on train+validation, scored once on test ===")
    final_model = clone(best_estimator)
    final_model.fit(X_train_val, y_train_val)
    final = evaluate(final_model, X_test, y_test, best_name)
    print(
        f"  accuracy={final['accuracy']:.4f} macroF1={final['macro_f1']:.4f} "
        f"fatalRecall={final['per_class_recall']['fatal_risk']:.4f} "
        f"rocAUC={final['roc_auc_macro']} fatalPR={final['fatal_pr_auc']}",
        flush=True,
    )

    calibration = measure_calibration(final_model, X_test, y_test)
    fatal_ratio = calibration["per_class"]["fatal_risk"]["ratio"]
    print(
        f"  calibration: fatal score is {fatal_ratio}x the observed rate "
        "(class weighting, not a probability)",
        flush=True,
    )

    print("\n=== ablation: context features only (no vehicle type, no contributing factor) ===")
    ablation_model = HistGradientBoostingClassifier(
        max_iter=250,
        learning_rate=0.08,
        max_leaf_nodes=63,
        min_samples_leaf=40,
        early_stopping=True,
        validation_fraction=0.1,
        class_weight="balanced",
        random_state=RANDOM_STATE,
    )
    ablation_model.fit(X_train_val[CONTEXT_FEATURES], y_train_val)
    ablation = evaluate(ablation_model, X_test[CONTEXT_FEATURES], y_test, "context_only")
    print(
        f"  accuracy={ablation['accuracy']:.4f} macroF1={ablation['macro_f1']:.4f} "
        f"fatalRecall={ablation['per_class_recall']['fatal_risk']:.4f} "
        f"rocAUC={ablation['roc_auc_macro']}",
        flush=True,
    )

    print("\n=== permutation importance (test subsample) ===")
    if len(X_test) > IMPORTANCE_ROWS:
        X_imp, _, y_imp, _ = train_test_split(
            X_test, y_test, train_size=IMPORTANCE_ROWS, stratify=y_test, random_state=RANDOM_STATE
        )
    else:
        X_imp, y_imp = X_test, y_test
    importance = permutation_importance(
        final_model,
        X_imp,
        y_imp,
        scoring="f1_macro",
        n_repeats=3,
        random_state=RANDOM_STATE,
        # Joblib's process pool exhausted the Windows paging file here; scoring
        # 37 features on 10k rows is fast enough in-process.
        n_jobs=1,
    )
    importance_payload = {
        feature: {
            "mean": round(float(mean), 6),
            "std": round(float(std), 6),
        }
        for feature, mean, std in zip(SEVERITY_FEATURES, importance.importances_mean, importance.importances_std)
    }
    for feature, values in sorted(
        importance_payload.items(), key=lambda item: item[1]["mean"], reverse=True
    )[:10]:
        print(f"  {feature:28s} {values['mean']:+.4f} +/- {values['std']:.4f}")

    # ------------------------------------------------------------------ #
    # Artifacts
    # ------------------------------------------------------------------ #
    print("\n=== writing artifacts ===")
    joblib.dump(final_model, MODEL_PATH)
    print(f"  wrote {os.path.basename(MODEL_PATH)}", flush=True)
    plot_confusion(final["confusion_matrix"], CONFUSION_PATH)
    plot_importance(importance_payload, IMPORTANCE_PATH)

    majority_share = float((y_test == 0).mean())
    baseline = next(item for item in results if item["name"] == "dummy_majority")

    payload = {
        "model": type(final_model).__name__,
        "model_configuration": best_name,
        "status": (
            "research only - needs post-crash attributes, so it is excluded from /risk"
        ),
        "experimental": True,
        "target": "crash_severity",
        "classes": CLASS_NAMES,
        "features": SEVERITY_FEATURES,
        "context_features": CONTEXT_FEATURES,
        "dataset": dataset_info,
        "split": {
            "train": int(len(X_train)),
            "validation": int(len(X_val)),
            "test": int(len(X_test)),
            "train_final": int(len(X_train_val)),
        },
        "training_records": int(len(X_train_val)),
        "n_folds": 5,
        "cv_accuracy_mean": round(float(np.mean(fold_accuracy)), 4),
        "cv_accuracy_std": round(float(np.std(fold_accuracy)), 4),
        "cv_scores_per_fold": [round(value, 4) for value in fold_accuracy],
        "cv_macro_f1_mean": round(float(np.mean(fold_macro_f1)), 4),
        "cv_macro_f1_std": round(float(np.std(fold_macro_f1)), 4),
        "cv_macro_f1_per_fold": [round(value, 4) for value in fold_macro_f1],
        "cv_fatal_recall_mean": round(float(np.mean(fold_fatal_recall)), 4),
        "accuracy": final["accuracy"],
        "balanced_accuracy": final["balanced_accuracy"],
        "macro_f1": final["macro_f1"],
        "baseline_accuracy": round(float(baseline["accuracy"]), 4),
        "baseline_macro_f1": round(float(baseline["macro_f1"]), 4),
        "majority_class_share": round(majority_share, 4),
        "roc_auc_macro": final["roc_auc_macro"],
        "fatal_pr_auc": final["fatal_pr_auc"],
        "brier_score": final["brier_score"],
        "per_class_recall": final["per_class_recall"],
        "per_class_precision": final["per_class_precision"],
        "per_class_f1": final["per_class_f1"],
        "confusion_matrix": final["confusion_matrix"],
        "candidate_comparison": results,
        "ablation_context_only": ablation,
        "calibration": calibration,
        "permutation_importance": importance_payload,
        "importance_method": "permutation importance (drop in macro-F1)",
        "honesty": [
            "Vehicle type and contributing factor are recorded after the crash, so this is a retrospective severity model, not a pre-crash risk predictor.",
            "Contributing factors can be shaped by the outcome (an officer may record unsafe speed because the crash was severe), so the association is not causal.",
            "Injury and fatality counts are used only to build the label; no count is a feature.",
            "The ablation shows what context-only features achieve: that is the ceiling the previous model was working against.",
            (
                "Predicted probabilities are class-weighted, not calibrated: the fatal "
                f"score averages {fatal_ratio}x the observed fatality rate. The ranking "
                "is informative; the absolute value is not a real-world fatality rate."
            ),
        ],
        "limitations": [
            "Trained only on NYC crash records; no non-crash examples.",
            "Crashes without usable coordinates are excluded, so the model never scores them.",
            "Severity labels describe crashes that already happened, not crash likelihood.",
            "The model is excluded from /risk scoring and is research-only.",
        ],
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sklearn_version": sklearn.__version__,
        "dataset_file": os.path.basename(DATA_PATH),
    }
    with open(METRICS_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"  wrote {os.path.basename(METRICS_PATH)}", flush=True)

    print(f"\ndone in {time.perf_counter() - started:.0f}s")
    print(
        f"  vs baseline: accuracy {final['accuracy']:.4f} vs {baseline['accuracy']:.4f} | "
        f"macro-F1 {final['macro_f1']:.4f} vs {baseline['macro_f1']:.4f}"
    )


if __name__ == "__main__":
    if "--calibrate-only" in sys.argv:
        calibrate_only()
    else:
        main()
