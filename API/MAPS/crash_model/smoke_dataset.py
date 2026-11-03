"""Smoke-test the dataset builder on a one-year slice."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataset
from weather import ensure_weather

crashes = dataset.load_crashes()
print(f"loaded crashes: {len(crashes):,}")
cr = crashes[crashes["timestamp"] < pd.Timestamp("2021-01-01")]
print(f"2020 target crashes: {len(cr):,}")

weather = ensure_weather()
frame = dataset.build_events(cr, weather, per_hour=30)
print(f"frame: {frame.shape}")
print(frame.head(3).to_string())
print(frame.dtypes.to_string())
print(f"positives: {(frame['count'] > 0).sum():,}")
print(f"zeros: {(frame['count'] == 0).sum():,}")
print(f"hist_rate range: {frame['hist_rate'].min():.6f} .. {frame['hist_rate'].max():.6f}")
