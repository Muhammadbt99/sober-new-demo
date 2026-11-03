"""Hourly NYC weather for the crash-risk model.

The model treats weather as a minor feature. One city-wide series is enough:
crash risk varies far more by hex and hour than by the few kilometres between
NYC weather stations. The series is cached next to this file so training is
reproducible without a network call.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "nyc_hourly_weather.csv")

LAT, LON = 40.7128, -74.0060
START = date(2020, 1, 1)
END = date(2026, 6, 11)
URL = "https://archive-api.open-meteo.com/v1/archive"

COLUMNS = ["time", "temperature", "rain", "wind_speed"]


def fetch_weather() -> pd.DataFrame:
    params = {
        "latitude": LAT,
        "longitude": LON,
        "start_date": START.isoformat(),
        "end_date": END.isoformat(),
        "hourly": "temperature_2m,precipitation,wind_speed_10m",
        "timezone": "America/New_York",
    }
    response = requests.get(URL, params=params, timeout=90)
    response.raise_for_status()
    hourly = response.json()["hourly"]
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(hourly["time"]),
            "temperature": hourly["temperature_2m"],
            "rain": hourly["precipitation"],
            "wind_speed": hourly["wind_speed_10m"],
        }
    )
    return frame


def ensure_weather(force: bool = False) -> pd.DataFrame:
    if os.path.exists(CACHE) and not force:
        frame = pd.read_csv(CACHE, parse_dates=["time"])
    else:
        frame = fetch_weather()
        frame.to_csv(CACHE, index=False)
    if list(frame.columns) != COLUMNS:
        raise ValueError(f"unexpected weather columns: {list(frame.columns)}")
    return frame


if __name__ == "__main__":
    data = ensure_weather(force=True)
    print(f"rows: {len(data):,}")
    print(f"range: {data['time'].min()} .. {data['time'].max()}")
    print(data.isna().sum().to_string())
