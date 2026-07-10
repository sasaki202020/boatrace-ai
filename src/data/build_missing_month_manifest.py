from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data" / "processed" / "historical_races.csv"
OUT = ROOT / "data" / "raw" / "official" / "missing_months_manifest.csv"


def month_range(start: str, end: str) -> list[str]:
    periods = pd.period_range(start=start, end=end, freq="M")
    return [str(p) for p in periods]


def to_b_code(yyyy_mm: str) -> str:
    y, m = yyyy_mm.split("-")
    yy = y[2:]
    return f"B{yy}{m}01.LZH"


def main() -> None:
    if not HIST.exists():
        raise FileNotFoundError(f"historical file not found: {HIST}")

    df = pd.read_csv(HIST)
    if "date" not in df.columns:
        raise ValueError("historical_races.csv must contain date column")

    d = pd.to_datetime(df["date"], errors="coerce")
    d = d.dropna()
    if d.empty:
        raise ValueError("date column has no valid values")

    min_month = str(d.min().to_period("M"))
    max_month = str(d.max().to_period("M"))

    all_months = month_range(min_month, max_month)
    have_months = set(d.dt.to_period("M").astype(str))

    rows = []
    for m in all_months:
        rows.append(
            {
                "month": m,
                "status": "have" if m in have_months else "missing",
                "suggested_file": to_b_code(m),
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["month", "status", "suggested_file"])
        writer.writeheader()
        writer.writerows(rows)

    missing = sum(1 for r in rows if r["status"] == "missing")
    print(f"[saved] {OUT}")
    print(f"month_range: {min_month} .. {max_month}")
    print(f"months_total: {len(rows)}")
    print(f"months_missing: {missing}")


if __name__ == "__main__":
    main()

