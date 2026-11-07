from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
HIST_CSV = ROOT / "data" / "processed" / "historical_races.csv"
TODAY_CSV = ROOT / "data" / "processed" / "today_races.csv"


def main() -> None:
    if not HIST_CSV.exists():
        raise FileNotFoundError(f"historical file not found: {HIST_CSV}")

    hist = pd.read_csv(HIST_CSV, low_memory=False)
    if "date" not in hist.columns:
        raise ValueError("historical_races.csv must contain 'date' column")

    dt = pd.to_datetime(hist["date"], errors="coerce")
    if dt.notna().sum() == 0:
        raise ValueError("no valid date found in historical_races.csv")

    latest_date = dt.max().date()
    today_rows = hist.loc[dt.dt.date == latest_date].copy()
    if today_rows.empty:
        raise ValueError(f"no rows found for latest date: {latest_date}")

    # Keep deterministic order for reproducible downstream outputs.
    sort_cols = [c for c in ["date", "race_id", "lane"] if c in today_rows.columns]
    if sort_cols:
        today_rows = today_rows.sort_values(sort_cols).reset_index(drop=True)

    TODAY_CSV.parent.mkdir(parents=True, exist_ok=True)
    today_rows.to_csv(TODAY_CSV, index=False)

    race_count = today_rows["race_id"].nunique() if "race_id" in today_rows.columns else None
    print(f"[saved] {TODAY_CSV}")
    print(f"[stats] date={latest_date} rows={len(today_rows)} race_count={race_count}")


if __name__ == "__main__":
    main()
