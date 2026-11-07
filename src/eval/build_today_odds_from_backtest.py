import argparse
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description="Build today_trifecta_odds.csv from backtest_race_results.csv (uses official_odds for actual_trifecta)"
    )
    parser.add_argument("--input", default="reports/backtest_race_results.csv", help="Source CSV that contains actual_trifecta and official_odds")
    parser.add_argument("--output", default="data/odds/today_trifecta_odds.csv", help="Output odds CSV")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        raise FileNotFoundError(f"input not found: {src}")

    df = pd.read_csv(src)
    required = {"race_id", "actual_trifecta", "official_odds"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"input missing columns: {sorted(missing)}")

    odds_df = (
        df[["race_id", "actual_trifecta", "official_odds"]]
        .dropna(subset=["race_id", "actual_trifecta", "official_odds"])
        .rename(columns={"actual_trifecta": "trifecta", "official_odds": "odds"})
    )
    odds_df["odds"] = pd.to_numeric(odds_df["odds"], errors="coerce")
    odds_df = odds_df.dropna(subset=["odds"])
    odds_df["odds_source"] = "official_result_odds"

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    odds_df.to_csv(out_path, index=False)
    print(f"today_trifecta_odds saved: {out_path} (rows: {len(odds_df)})")


if __name__ == "__main__":
    main()
