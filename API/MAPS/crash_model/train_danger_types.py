"""Train and gate the danger-type shares (see ``danger_types.py``).

Run from this directory, after ``fetch_crashes_rich.py`` has written an aligned
``nyc_crashes_rich.csv.gz`` (re-downloaded 2026-11-03):

    "D:\\Python and GIT\\python.exe" train_danger_types.py

Splits follow the crash model: train 2020-2024, validate 2025, test 2026.
For each danger group a fixed candidate set is compared on validation only:

    city            one citywide share, all hours       (the baseline)
    time            citywide share per band of the week
    borough         borough x band, shrunk toward time
    neighbourhood   NTA x band, shrunk toward borough

Shrinkage strengths are tuned on validation. The winner is scored once on the
2026 test year against the gate, decided before any result was seen:

    G1  test log-loss beats the citywide constant
    G2  an area level (borough, neighbourhood) also beats time-only on test,
        otherwise the group falls back to time-only and is re-gated
    G3  top-decile lift on test is at least 1.5x

A group that fails G1 or G3 ships switched off ("not_validated"). Serving
counts are refit on all years once the gate has run, as the crash model is.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timezone

import h3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, MODULE_DIR)

from crash_model.danger_types import (  # noqa: E402
    ARTIFACT_FILE,
    BAND_IDS,
    BANDS,
    GROUP_IDS,
    GROUPS,
    FACTOR_TO_GROUP,
    band_for,
)

DATA = os.path.join(MODULE_DIR, "nyc_crashes_rich.csv.gz")
CELLS = os.path.join(HERE, "cell_rates.csv")
VERSION = "danger-types-2026.11.03-2"
TRAIN_END, VALIDATION_END = pd.Timestamp("2025-01-01"), pd.Timestamp("2026-01-01")
BBOX = (40.4, 41.0, -74.5, -73.5)
STRENGTHS = [1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0]
MIN_LIFT = 1.5
EPS = 1e-6


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load_events(path: str = DATA) -> pd.DataFrame:
    """Injury-or-fatal crashes in NYC with their area, band and danger groups."""
    from fetch_crashes_rich import check_alignment

    raw = pd.read_csv(path, low_memory=False, dtype=str)
    problems = check_alignment(raw)
    if problems:
        raise SystemExit("refusing a misaligned data file: " + "; ".join(problems))

    injured = pd.to_numeric(raw["number_of_persons_injured"], errors="coerce").fillna(0)
    killed = pd.to_numeric(raw["number_of_persons_killed"], errors="coerce").fillna(0)
    lat = pd.to_numeric(raw["latitude"], errors="coerce")
    lon = pd.to_numeric(raw["longitude"], errors="coerce")
    keep = (
        ((injured > 0) | (killed > 0))
        & lat.between(BBOX[0], BBOX[1])
        & lon.between(BBOX[2], BBOX[3])
    )
    frame = raw.loc[keep].copy()
    stamp = pd.to_datetime(
        pd.to_datetime(frame["crash_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        + " "
        + frame["crash_time"].astype(str).str.strip(),
        errors="coerce",
    )
    frame["timestamp"] = stamp
    frame = frame.dropna(subset=["timestamp"])
    frame["hour"] = frame["timestamp"].dt.hour
    frame["day"] = frame["timestamp"].dt.dayofweek
    frame["band"] = [band_for(h, d) for h, d in zip(frame["hour"], frame["day"])]
    frame["cell"] = [
        h3.latlng_to_cell(a, b, 8)
        for a, b in zip(lat.loc[frame.index], lon.loc[frame.index])
    ]

    # Areas come from the crash model's own cell table, as at serving time.
    cells = pd.read_csv(CELLS, usecols=["cell", "nta_code", "borough"]).set_index("cell")
    frame["nta"] = frame["cell"].map(cells["nta_code"])
    record_borough = frame["borough"].where(
        frame["borough"].isin(["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"])
    )
    frame["area_borough"] = frame["cell"].map(cells["borough"]).fillna(record_borough)
    frame.loc[frame["area_borough"] == "UNKNOWN", "area_borough"] = np.nan

    factor_columns = [c for c in frame.columns if c.startswith("contributing_factor_vehicle_")]
    lowered = frame[factor_columns].apply(lambda col: col.str.strip().str.lower())
    for group in GROUP_IDS:
        names = {name for name, owner in FACTOR_TO_GROUP.items() if owner == group}
        frame[group] = lowered.isin(names).any(axis=1).astype(np.int8)
    return frame


# ---------------------------------------------------------------------------
# Counts and estimates
# ---------------------------------------------------------------------------


def count_tables(frame: pd.DataFrame) -> dict:
    """n (injury crashes) and k (per group) by city, borough x band, NTA x band."""

    def table(keys: list[str]) -> dict:
        grouped = frame.dropna(subset=keys).groupby(keys)
        sizes = grouped.size()
        sums = grouped[GROUP_IDS].sum()
        out: dict = {}
        for index, n in sizes.items():
            area, band = index
            out.setdefault(str(area), {})[band] = {
                "n": int(n),
                "k": {g: int(sums.loc[index, g]) for g in GROUP_IDS},
            }
        return out

    bands = frame.groupby("band")
    return {
        "city": {
            "all": {"n": int(len(frame)), "k": {g: int(frame[g].sum()) for g in GROUP_IDS}},
            "bands": {
                band: {"n": int(len(part)), "k": {g: int(part[g].sum()) for g in GROUP_IDS}}
                for band, part in bands
            },
        },
        "borough": table(["area_borough", "band"]),
        "nta": table(["nta", "band"]),
    }


def _lookup(table: dict, keys: pd.Series, bands: pd.Series, group: str) -> tuple[np.ndarray, np.ndarray]:
    n = np.zeros(len(keys))
    k = np.zeros(len(keys))
    for i, (key, band) in enumerate(zip(keys, bands)):
        entry = (table.get(str(key)) or {}).get(band) if isinstance(key, str) else None
        if entry:
            n[i], k[i] = entry["n"], entry["k"][group]
    return n, k


def arrays(frame: pd.DataFrame, counts: dict, group: str) -> dict:
    """Per-crash counts behind every candidate, looked up once."""
    total = counts["city"]["all"]
    city_band = {
        band: (entry["k"][group] + 0.5) / (entry["n"] + 1.0)
        for band, entry in counts["city"]["bands"].items()
    }
    n_b, k_b = _lookup(counts["borough"], frame["area_borough"], frame["band"], group)
    n_n, k_n = _lookup(counts["nta"], frame["nta"], frame["band"], group)
    return {
        "city": np.full(len(frame), (total["k"][group] + 0.5) / (total["n"] + 1.0)),
        "time": frame["band"].map(city_band).to_numpy(dtype=float),
        "n_b": n_b, "k_b": k_b, "n_n": n_n, "k_n": k_n,
    }


def combine(a: dict, level: str, m_borough: float = math.inf, m_nta: float = math.inf) -> np.ndarray:
    """The candidate's share for each crash: shrinkage down the area levels."""
    if level in ("city", "time"):
        return a[level]
    share = a["time"] if m_borough == math.inf else (a["k_b"] + m_borough * a["time"]) / (a["n_b"] + m_borough)
    if level == "borough":
        return share
    return share if m_nta == math.inf else (a["k_n"] + m_nta * share) / (a["n_n"] + m_nta)


def predict(frame: pd.DataFrame, counts: dict, group: str, level: str,
            m_borough: float = math.inf, m_nta: float = math.inf) -> np.ndarray:
    """Predicted share of ``group`` for each crash in ``frame``."""
    return combine(arrays(frame, counts, group), level, m_borough, m_nta)


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def deciles(y: np.ndarray, p: np.ndarray, seed: int = 20261103) -> list[dict]:
    """Calibration by predicted decile. Ties are broken at random (seeded)."""
    order = np.lexsort((np.random.default_rng(seed).random(len(p)), p))
    base = float(y.mean()) if len(y) else 0.0
    rows = []
    for index, chunk in enumerate(np.array_split(order, 10), start=1):
        observed = float(y[chunk].mean()) if len(chunk) else 0.0
        rows.append(
            {
                "decile": index,
                "predicted_share": float(p[chunk].mean()) if len(chunk) else 0.0,
                "observed_share": observed,
                "lift": observed / base if base > 0 else None,
            }
        )
    return rows


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    table = deciles(y, p)
    return {
        "log_loss": log_loss(y, p),
        "top_decile_lift": table[-1]["lift"],
        "events": int(y.sum()),
        "crashes": int(len(y)),
        "deciles": table,
    }


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def tune(train: pd.DataFrame, validation: pd.DataFrame, group: str) -> dict:
    """Pick each candidate's strengths and the best candidate, on validation only."""
    a = arrays(validation, count_tables(train), group)
    y = validation[group].to_numpy(dtype=float)
    results = {
        "city": {"log_loss": log_loss(y, combine(a, "city"))},
        "time": {"log_loss": log_loss(y, combine(a, "time"))},
    }
    best_b = min(STRENGTHS, key=lambda m: log_loss(y, combine(a, "borough", m)))
    results["borough"] = {
        "m_borough": best_b,
        "log_loss": log_loss(y, combine(a, "borough", best_b)),
    }
    best = None
    for m_b in STRENGTHS:
        for m_n in STRENGTHS:
            loss = log_loss(y, combine(a, "neighbourhood", m_b, m_n))
            if best is None or loss < best[0]:
                best = (loss, m_b, m_n)
    results["neighbourhood"] = {"m_borough": best[1], "m_nta": best[2], "log_loss": best[0]}
    chosen = min(("time", "borough", "neighbourhood"), key=lambda c: results[c]["log_loss"])
    return {"validation": results, "chosen": chosen}


def gate(fit: pd.DataFrame, test: pd.DataFrame, group: str, tuned: dict) -> dict:
    counts = count_tables(fit)
    y = test[group].to_numpy(dtype=float)
    params = tuned["validation"]
    candidates = {
        "city": metrics(y, predict(test, counts, group, "city")),
        "time": metrics(y, predict(test, counts, group, "time")),
        "borough": metrics(
            y, predict(test, counts, group, "borough", params["borough"]["m_borough"])
        ),
        "neighbourhood": metrics(
            y,
            predict(
                test, counts, group, "neighbourhood",
                params["neighbourhood"]["m_borough"], params["neighbourhood"]["m_nta"],
            ),
        ),
    }
    level = tuned["chosen"]
    checks: dict = {}
    if level != "time":
        checks["area_beats_time_only"] = (
            candidates[level]["log_loss"] <= candidates["time"]["log_loss"]
        )
        if not checks["area_beats_time_only"]:
            level = "time"
    checks["beats_city_constant"] = candidates[level]["log_loss"] < candidates["city"]["log_loss"]
    checks["top_decile_lift_at_least_1_5"] = (candidates[level]["top_decile_lift"] or 0) >= MIN_LIFT
    passed = checks["beats_city_constant"] and checks["top_decile_lift_at_least_1_5"]
    return {
        "test": {
            name: {key: value for key, value in result.items() if key != "deciles"}
            for name, result in candidates.items()
        },
        "test_deciles": candidates[level]["deciles"],
        "level": level,
        "checks": checks,
        "passed": bool(passed),
    }


def city_type_rates(spec: dict, counts: dict) -> dict:
    """Mean expected rate of each type over every model cell x hour of the week.

    The reference is the crash model's typical conditions (June, 15 C, dry,
    light wind), the same as its percentile reference, so "2x a typical NYC
    cell-hour" means the same thing in both places.
    """
    from crash_model.model import RiskModel

    model = RiskModel()
    cells = model.cells
    nta = cells["nta_code"].astype(str).to_numpy()
    borough = cells["borough"].astype(str).to_numpy()
    totals = {g: 0.0 for g in GROUP_IDS}
    hours = 0
    for day in range(7):
        for hour in range(24):
            surface = model.score_cells(
                hour, day, month=6, on_date=date(2025, 6, 11), weather=(15.0, 0.0, 10.0)
            )
            rate = np.asarray([cell["expected_rate"] for cell in surface])
            band = band_for(hour, day)
            frame = pd.DataFrame(
                {"band": band, "area_borough": borough, "nta": nta}
            ).replace({"area_borough": {"UNKNOWN": np.nan}})
            for group in GROUP_IDS:
                level = spec[group]["level"]
                share = predict(
                    frame, counts, group, level,
                    spec[group].get("m_borough") or math.inf,
                    spec[group].get("m_nta") or math.inf,
                )
                totals[group] += float(np.mean(rate * share))
            hours += 1
    return {group: totals[group] / hours for group in GROUP_IDS}


def main() -> int:
    print("loading events...", flush=True)
    events = load_events()
    print(f"  injury crashes: {len(events):,}  ({events['timestamp'].min()} -> {events['timestamp'].max()})")
    print("  recorded share: " + ", ".join(f"{g} {events[g].mean():.2%}" for g in GROUP_IDS))

    train = events[events["timestamp"] < TRAIN_END]
    validation = events[(events["timestamp"] >= TRAIN_END) & (events["timestamp"] < VALIDATION_END)]
    test = events[events["timestamp"] >= VALIDATION_END]
    fit = events[events["timestamp"] < VALIDATION_END]
    print(f"  train {len(train):,}  validation {len(validation):,}  test {len(test):,}", flush=True)

    report: dict = {}
    for group in GROUP_IDS:
        tuned = tune(train, validation, group)
        gated = gate(fit, test, group, tuned)
        chosen = tuned["validation"].get(gated["level"], {})
        report[group] = {
            "level": gated["level"],
            "validation_choice": tuned["chosen"],
            "m_borough": chosen.get("m_borough"),
            "m_nta": chosen.get("m_nta"),
            "status": "validated" if gated["passed"] else "not_validated",
            "gate": gated["checks"],
            "validation": tuned["validation"],
            "test": gated["test"],
            "test_deciles": gated["test_deciles"],
        }
        t = gated["test"]
        print(
            f"\n{group:16} chosen={tuned['chosen']:13} served={gated['level']:13} "
            f"status={report[group]['status']}\n"
            f"  test log-loss  city {t['city']['log_loss']:.5f}  time {t['time']['log_loss']:.5f}  "
            f"borough {t['borough']['log_loss']:.5f}  nta {t['neighbourhood']['log_loss']:.5f}\n"
            f"  test lift      city {t['city']['top_decile_lift']:.2f}  time {t['time']['top_decile_lift']:.2f}  "
            f"borough {t['borough']['top_decile_lift']:.2f}  nta {t['neighbourhood']['top_decile_lift']:.2f}"
            f"  (test events {t['time']['events']})\n"
            f"  gate {gated['checks']}",
            flush=True,
        )

    print("\nrefitting serving counts on all years...", flush=True)
    counts = count_tables(events)
    print("computing typical-cell-hour rates per type...", flush=True)
    rates = city_type_rates(report, counts)
    for group in GROUP_IDS:
        report[group]["city_type_rate"] = rates[group]

    artifact = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "file": os.path.basename(DATA),
            "source": "NYC Open Data h9gi-nx95, re-downloaded 2026-11-03 with aligned columns",
            "window": [str(events["timestamp"].min()), str(events["timestamp"].max())],
            "injury_crashes": int(len(events)),
            "split": {"train_end": str(TRAIN_END.date()), "validation_end": str(VALIDATION_END.date()),
                      "train": int(len(train)), "validation": int(len(validation)), "test": int(len(test))},
        },
        "target": "share of injury-or-fatal crashes whose recorded contributing factors include the group",
        "inputs": ["area (2020 NTA or borough of the H3 cell)", "band of the week (6 bands)"],
        "bands": BANDS,
        "definitions": {g: GROUPS[g]["factors"] for g in GROUP_IDS},
        "gate_rules": {
            "beats_city_constant": "test log-loss below one citywide share",
            "area_beats_time_only": "an area level is kept only if it beats time-only on test",
            "top_decile_lift_at_least_1_5": f"top-decile lift on test >= {MIN_LIFT}",
        },
        "groups": report,
        "counts": counts,
    }
    path = os.path.join(HERE, ARTIFACT_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=1)
    size_kb = os.path.getsize(path) / 1024
    print(f"\nwrote {path} ({size_kb:.0f} KB)")
    print("validated: " + ", ".join(g for g in GROUP_IDS if report[g]["status"] == "validated"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
