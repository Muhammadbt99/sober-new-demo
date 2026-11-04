"""Train and validate the global crash model.

Protocol, fixed before any result was seen
------------------------------------------
* **Target.** Injury crashes per H3 cell, day of the week (counted from the
  start of the local weekend) and hour, *relative to the city's own mean*.
  Cities record crashes differently and their overall level cannot be known
  for a place with no records, so the model ranks places and hours within a
  region. It never claims a crash count outside the training cities.
* **Leave one city out.** Every number below comes from a model that never saw
  the city it is scored on.
* **Selection** of the feature set: train on the other cities' 2021-2022
  crashes, score the held-out city's 2023 crashes.
* **Gate**, on later years: train on the other cities' 2021-2023 crashes, score
  the held-out city's 2024-2025 crashes. The chosen model must
    1. beat the population-and-time baseline on Poisson deviance in at least
       four of the five held-out cities,
    2. reach a mean top-decile lift of at least 2.0, and at least 1.5 in every
       held-out city,
    3. reach a mean Spearman correlation of at least 0.6 between predicted and
       observed crashes per cell.
* **Mapping check** (no crash data involved): a feature that depends on how
  thoroughly volunteers tagged a place (signals, crossings, bus stops, shops...)
  is only usable in a served region if, per street junction, the region has at
  least half as many of them as the least-mapped training city. Otherwise a
  model would read "not mapped" as "not there" and call a place safer than it
  is. The served set is the best-ranked candidate whose features all pass;
  "roads" (population, road length by class, junctions) needs no tags.
* **Gate**, on the served set (the best-ranked set is reported beside it).
* A city in ``raw/dubai`` (Dubai Police records, downloaded by hand) is scored
  as an extra held-out city in the gate and reported separately.
* **Final model**: the served feature set, refitted on every city and year.

Usage:
    python train_global.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402

from global_model.osm_features import FEATURES  # noqa: E402
from global_model.sources import CITIES, WORK, YEARS  # noqa: E402

ARTIFACTS = os.path.join(HERE, "artifacts")
VERSION = "global-2026.11.03-1"

TIME_FEATURES = ["hour", "rel_dow", "weekend"]
POPULATION = ["population", "ring_population"]
DRIVE = ["km_motorway", "km_trunk", "km_primary", "km_secondary", "km_tertiary", "km_local", "km_service"]
MAPPED = ["signals", "crossings", "bus_stops", "roundabouts", "stations", "km_walk", "km_cycle"]
PLACES = ["nightlife", "food", "shops", "schools", "fuel", "parking"]


def _with_ring(names: list[str]) -> list[str]:
    return names + [f"ring_{name}" for name in names if name in FEATURES]


CANDIDATES: dict[str, list[str]] = {
    "time_only": [],
    "population": POPULATION,
    "roads": POPULATION + _with_ring(DRIVE + ["junctions"]),
    "mapped": POPULATION + _with_ring(DRIVE + ["junctions"] + MAPPED),
    "full": POPULATION + _with_ring(DRIVE + ["junctions"] + MAPPED + PLACES),
}
BASELINE = "population"
SPATIAL_CANDIDATES = ["roads", "mapped", "full"]
#: Features whose values depend on how completely a place was tagged.
TAGGED = MAPPED + PLACES
MAPPING_MIN_SHARE = 0.5
#: The served region the mapping check is run for.
SERVED_REGION = "uae"
#: Cells with a street grid, so a region's empty desert does not dilute the ratios.
MAPPING_MIN_JUNCTIONS = 5

PARAMS = dict(
    loss="poisson",
    learning_rate=0.05,
    max_iter=400,
    max_leaf_nodes=31,
    min_samples_leaf=400,
    l2_regularization=1.0,
    random_state=7,
)

GATE = {
    "beats_baseline_in_at_least": 4,
    "mean_top_decile_lift": 2.0,
    "min_top_decile_lift": 1.5,
    "mean_cell_spearman": 0.6,
}


def log(message: str) -> None:
    print(message, flush=True)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


# --------------------------------------------------------------------------- data


class CityData:
    """One city's cells, features and crash counts per year x day x hour."""

    def __init__(self, key: str):
        self.key = key
        self.table = pd.read_csv(os.path.join(WORK, f"{key}_cells.csv.gz"), index_col="cell")
        self.counts = np.load(os.path.join(WORK, f"{key}_counts.npy"))  # cells, years, 7, 24
        with open(os.path.join(WORK, f"{key}_meta.json"), encoding="utf-8") as handle:
            self.meta = json.load(handle)
        self.name = self.meta.get("name", key)

    def years(self, years: tuple[int, ...]) -> np.ndarray:
        """Crash counts summed over ``years``: cells x 7 x 24."""
        index = [YEARS.index(year) for year in years]
        return self.counts[:, index].sum(axis=1)


def design(table: pd.DataFrame, features: list[str]) -> np.ndarray:
    """Rows: every cell x 7 days x 24 hours, cell-major."""
    cells = len(table)
    static = table[features].to_numpy(dtype=np.float32) if features else np.zeros((cells, 0), np.float32)
    static = np.repeat(static, 7 * 24, axis=0)
    rel_dow = np.tile(np.repeat(np.arange(7), 24), cells).astype(np.float32)
    hour = np.tile(np.tile(np.arange(24), 7), cells).astype(np.float32)
    weekend = (rel_dow < 2).astype(np.float32)
    return np.column_stack([static, hour, rel_dow, weekend])


def fit(train: list[tuple[CityData, np.ndarray]], features: list[str]) -> HistGradientBoostingRegressor:
    """Relative target (count / city mean), every city weighted equally."""
    xs, ys, ws = [], [], []
    for city, counts in train:
        y = counts.reshape(-1).astype(np.float64)
        mean = y.mean()
        if mean <= 0:
            continue
        xs.append(design(city.table, features))
        ys.append(y / mean)
        ws.append(np.full(len(y), 1.0 / len(y)))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    w = np.concatenate(ws)
    w *= len(w) / w.sum()
    model = HistGradientBoostingRegressor(**PARAMS)
    model.fit(x, y, sample_weight=w)
    return model


# --------------------------------------------------------------------------- metrics


def poisson_deviance(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.clip(mu, 1e-12, None)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / mu), 0.0)
    return float(np.mean(2.0 * (term - (y - mu))))


def evaluate(predicted: np.ndarray, observed: np.ndarray) -> dict:
    """Score relative predictions against counts (cells x 7 x 24).

    Only the ranking is tested: the predictions are rescaled so their total is
    the observed total before the deviance is taken.
    """
    y = observed.reshape(-1).astype(np.float64)
    mu = predicted.reshape(-1).astype(np.float64)
    mu = mu * (y.sum() / mu.sum())
    order = np.argsort(-mu, kind="stable")
    top = order[: max(1, len(order) // 10)]
    lift = (y[top].mean() / y.mean()) if y.mean() > 0 else float("nan")

    cell_pred = predicted.reshape(len(observed), -1).sum(axis=1)
    cell_obs = observed.reshape(len(observed), -1).sum(axis=1)
    rho = spearmanr(cell_pred, cell_obs).correlation
    # A prediction that is the same in every cell has no ranking (time only).
    rho = float(rho) if np.isfinite(rho) else None
    best = np.argsort(-cell_pred, kind="stable")[: max(1, len(cell_pred) // 10)]
    capture = cell_obs[best].sum() / max(1, cell_obs.sum())

    # Deciles of cell-hours, for the calibration table.
    deciles = []
    for part in np.array_split(order[::-1], 10):
        deciles.append(
            {
                "predicted": float(mu[part].mean()),
                "observed": float(y[part].mean()),
                "lift": float(y[part].mean() / y.mean()) if y.mean() > 0 else None,
            }
        )
    return {
        "deviance": poisson_deviance(y, mu),
        "top_decile_lift": float(lift),
        "cell_spearman": rho,
        "top10_cells_capture": float(capture),
        "crashes": int(y.sum()),
        "cells": int(len(observed)),
        "deciles": deciles,
    }


def predict(model, city: CityData, features: list[str]) -> np.ndarray:
    return model.predict(design(city.table, features)).reshape(len(city.table), 7, 24)


def time_only(train: list[tuple[CityData, np.ndarray]], city: CityData) -> np.ndarray:
    """No spatial information: the pooled weekly profile, the same in every cell."""
    profile = np.zeros((7, 24))
    for _, counts in train:
        share = counts.sum(axis=0)
        profile += share / share.sum()
    return np.broadcast_to(profile, (len(city.table), 7, 24)).copy()


def leave_one_out(
    cities: dict[str, CityData],
    train_years: tuple[int, ...],
    test_years: tuple[int, ...],
    candidates: list[str],
    extra_test: dict[str, tuple[CityData, np.ndarray]] | None = None,
) -> dict:
    """{candidate: {held_out_city: metrics}} for one train/test split."""
    results: dict[str, dict[str, dict]] = {name: {} for name in candidates}
    targets = list(cities)
    for held_out in targets:
        train = [(c, c.years(train_years)) for key, c in cities.items() if key != held_out]
        test_counts = cities[held_out].years(test_years)
        for name in candidates:
            started = time.time()
            if name == "time_only":
                predicted = time_only(train, cities[held_out])
            else:
                model = fit(train, CANDIDATES[name])
                predicted = predict(model, cities[held_out], CANDIDATES[name])
            results[name][held_out] = evaluate(predicted, test_counts)
            r = results[name][held_out]
            log(
                f"    {held_out:8s} {name:10s} dev {r['deviance']:.5f} lift {r['top_decile_lift']:.2f} "
                f"rho {_fmt(r['cell_spearman'])} capture {r['top10_cells_capture']:.0%} ({time.time() - started:.0f}s)"
            )
    if extra_test:
        all_train = [(c, c.years(train_years)) for c in cities.values()]
        for key, (city, counts) in extra_test.items():
            for name in candidates:
                if name == "time_only":
                    predicted = time_only(all_train, city)
                else:
                    model = fit(all_train, CANDIDATES[name])
                    predicted = predict(model, city, CANDIDATES[name])
                results[name][key] = evaluate(predicted, counts)
                r = results[name][key]
                log(f"    {key:8s} {name:10s} dev {r['deviance']:.5f} lift {r['top_decile_lift']:.2f} rho {_fmt(r['cell_spearman'])}")
    return results


def summary(results: dict, name: str, cities: list[str]) -> dict:
    rows = [results[name][city] for city in cities]
    base = [results[BASELINE][city] for city in cities]
    return {
        "mean_deviance_gain_vs_baseline": float(
            np.mean([1 - r["deviance"] / b["deviance"] for r, b in zip(rows, base)])
        ),
        "beats_baseline_in": int(sum(r["deviance"] < b["deviance"] for r, b in zip(rows, base))),
        "mean_top_decile_lift": float(np.mean([r["top_decile_lift"] for r in rows])),
        "min_top_decile_lift": float(np.min([r["top_decile_lift"] for r in rows])),
        "mean_cell_spearman": float(np.mean([r["cell_spearman"] for r in rows])),
    }


# --------------------------------------------------------------------------- main


def load_dubai() -> tuple[CityData, np.ndarray] | None:
    """Dubai Police incidents, if they were downloaded and built (test only)."""
    path = os.path.join(WORK, "dubai_counts.npy")
    if not os.path.exists(path):
        return None
    city = CityData("dubai")
    return city, city.counts.sum(axis=1)


def mapping_check(cities: dict[str, CityData]) -> dict:
    """Per tagged feature: count per street junction, training cities vs the region."""
    path = os.path.join(WORK, f"{SERVED_REGION}_cells.csv.gz")
    region = pd.read_csv(path, index_col="cell")
    region = region[region["junctions"] >= MAPPING_MIN_JUNCTIONS]
    out = {}
    for feature in TAGGED:
        per_city = {
            key: float(city.table[feature].sum() / max(1.0, city.table["junctions"].sum()))
            for key, city in cities.items()
        }
        region_ratio = float(region[feature].sum() / max(1.0, region["junctions"].sum()))
        floor = min(per_city.values())
        out[feature] = {
            "training_cities": per_city,
            "region": region_ratio,
            "share_of_least_mapped_city": region_ratio / floor if floor > 0 else None,
            "usable": bool(floor > 0 and region_ratio >= MAPPING_MIN_SHARE * floor),
        }
    return out


def served_candidate(ranking_order: list[str], mapping: dict) -> str:
    """The best-ranked candidate whose tagged features are all mapped in the region."""
    for name in ranking_order:
        tagged = [f for f in CANDIDATES[name] if f in mapping or f.removeprefix("ring_") in mapping]
        if all(mapping[f.removeprefix("ring_")]["usable"] for f in tagged):
            return name
    return "roads"


def main() -> None:
    started = time.time()
    cities = {key: CityData(key) for key in CITIES}
    for key, city in cities.items():
        log(f"{key:8s} {len(city.table):,} cells, {int(city.counts.sum()):,} crashes")

    log("\nselection: other cities 2021-2022 -> held-out city 2023")
    selection = leave_one_out(cities, (2021, 2022), (2023,), ["time_only", BASELINE, *SPATIAL_CANDIDATES])
    held = list(cities)
    ranking = {name: summary(selection, name, held) for name in SPATIAL_CANDIDATES}
    for name, s in ranking.items():
        log(f"  {name:8s} {s}")
    best_gain = max(s["mean_deviance_gain_vs_baseline"] for s in ranking.values())
    # The simplest candidate within half a point of the best one wins.
    chosen = next(
        name
        for name in SPATIAL_CANDIDATES
        if ranking[name]["mean_deviance_gain_vs_baseline"] >= best_gain - 0.005
    )
    log(f"  best on the training cities: {chosen}")
    best = chosen
    ordered = sorted(
        SPATIAL_CANDIDATES,
        key=lambda name: -ranking[name]["mean_deviance_gain_vs_baseline"],
    )
    mapping = mapping_check(cities)
    for feature, row in mapping.items():
        log(
            f"  mapping {feature:12s} region {row['region']:.3f}/junction, "
            f"{row['share_of_least_mapped_city'] or 0:.2f}x the least-mapped city -> "
            f"{'usable' if row['usable'] else 'NOT usable'}"
        )
    # The best set if its tags are mapped in the region; else the best-ranked one that is.
    chosen = best if served_candidate([best], mapping) == best else served_candidate(ordered, mapping)
    log(f"  served in {SERVED_REGION}: {chosen}")

    log("\ngate: other cities 2021-2023 -> held-out city 2024-2025")
    dubai = load_dubai()
    extra = {"dubai": dubai} if dubai else None
    gate_sets = ["time_only", BASELINE, chosen] + ([best] if best != chosen else [])
    gate_results = leave_one_out(cities, (2021, 2022, 2023), (2024, 2025), gate_sets, extra)
    gate_summary = summary(gate_results, chosen, held)
    best_summary = summary(gate_results, best, held) if best != chosen else None
    gate = {
        "beats_baseline": gate_summary["beats_baseline_in"] >= GATE["beats_baseline_in_at_least"],
        "mean_top_decile_lift": gate_summary["mean_top_decile_lift"] >= GATE["mean_top_decile_lift"],
        "min_top_decile_lift": gate_summary["min_top_decile_lift"] >= GATE["min_top_decile_lift"],
        "mean_cell_spearman": gate_summary["mean_cell_spearman"] >= GATE["mean_cell_spearman"],
    }
    gate["passed"] = all(gate.values())
    log(f"  {gate_summary}")
    log(f"  GATE: {gate}")

    log("\nfinal fit: every city, every year")
    final = fit([(c, c.counts.sum(axis=1)) for c in cities.values()], CANDIDATES[chosen])

    os.makedirs(ARTIFACTS, exist_ok=True)
    features = CANDIDATES[chosen] + TIME_FEATURES
    metadata = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "approved" if gate["passed"] else "not_approved",
        "target": "injury crashes per H3 res-8 cell-hour, relative to the training city's mean",
        "resolution": 8,
        "features": features,
        "static_features": CANDIDATES[chosen],
        "feature_set": chosen,
        "best_on_training_cities": best,
        "mapping_check": {
            "region": SERVED_REGION,
            "rule": (
                f"a tagged feature is usable when the region has at least {MAPPING_MIN_SHARE:g}x "
                "the least-mapped training city's count per street junction"
            ),
            "features": mapping,
        },
        "params": {k: v for k, v in PARAMS.items()},
        "training": {
            key: {
                "name": city.name,
                "country": city.meta["country"],
                "cells": int(len(city.table)),
                "crashes": int(city.counts.sum()),
                "years": list(YEARS),
            }
            for key, city in cities.items()
        },
        "protocol": {
            "selection": "other cities 2021-2022 -> held-out city 2023",
            "gate": "other cities 2021-2023 -> held-out city 2024-2025",
            "gate_rules": GATE,
            "baseline": BASELINE,
        },
        "selection": {
            "ranking": ranking,
            "chosen": chosen,
            "per_city": {
                name: {city: {k: v for k, v in r.items() if k != "deciles"} for city, r in rows.items()}
                for name, rows in selection.items()
            },
        },
        "gate": {
            "summary": gate_summary,
            "checks": gate,
            "per_city": gate_results,
            "best_summary": best_summary,
        },
        "dubai_validation": gate_results[chosen].get("dubai"),
    }
    joblib.dump(final, os.path.join(ARTIFACTS, "global_crash_model.joblib"), compress=3)
    with open(os.path.join(ARTIFACTS, "global_crash_metadata.json"), "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
    log(f"\nwrote {ARTIFACTS} ({time.time() - started:.0f}s); GATE PASSED: {gate['passed']}")


if __name__ == "__main__":
    main()
