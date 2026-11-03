"""Exploratory checks for the crash-risk model. Not part of the served app."""
import os
import pandas as pd
import h3

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(HERE, "..", "nyc_crashes_rich.csv.gz"))
BBOX = (40.4, 41.0, -74.5, -73.5)


def main():
    df = pd.read_csv(DATA, low_memory=False)
    print(f"rows: {len(df):,}")
    df["crash_date"] = pd.to_datetime(df["crash_date"], errors="coerce")
    print(f"date range: {df['crash_date'].min()} .. {df['crash_date'].max()}")
    print(f"missing date: {df['crash_date'].isna().sum():,}")

    injured = pd.to_numeric(df["number_of_persons_injured"], errors="coerce").fillna(0)
    killed = pd.to_numeric(df["number_of_persons_killed"], errors="coerce").fillna(0)
    print(f"injury crashes: {(injured > 0).sum():,}")
    print(f"fatal crashes: {(killed > 0).sum():,}")
    print(f"injury or fatal: {((injured > 0) | (killed > 0)).sum():,}")

    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    inside = (
        lat.between(BBOX[0], BBOX[1])
        & lon.between(BBOX[2], BBOX[3])
        & lat.notna()
        & lon.notna()
    )
    print(f"valid coords inside NYC bbox: {inside.sum():,}")
    target = ((injured > 0) | (killed > 0)) & inside
    print(f"target rows (injury/fatal + inside bbox): {target.sum():,}")

    sample = df.loc[target, ["crash_date", "crash_time", "borough", "latitude", "longitude"]].copy()
    sample["lat"] = pd.to_numeric(sample["latitude"], errors="coerce")
    sample["lon"] = pd.to_numeric(sample["longitude"], errors="coerce")
    sample["cell"] = [
        h3.latlng_to_cell(la, lo, 8) for la, lo in zip(sample["lat"], sample["lon"])
    ]
    print(f"h3 res 8 cells: {sample['cell'].nunique():,}")
    print("borough counts:")
    print(sample["borough"].fillna("UNKNOWN").value_counts().to_string())
    print("top 10 cells by target crashes:")
    print(sample["cell"].value_counts().head(10).to_string())


if __name__ == "__main__":
    main()
