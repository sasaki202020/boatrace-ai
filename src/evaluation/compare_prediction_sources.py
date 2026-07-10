from __future__ import annotations

import argparse
import json
import sys

from src.evaluation.backtest_range import run_backtest_range


def compare_prediction_sources(*, start_date: str, end_date: str, jcd: str = "all", stake_per_buy: int = 100) -> dict:
    return run_backtest_range(
        start_date=start_date,
        end_date=end_date,
        jcd=jcd,
        stake_per_buy=stake_per_buy,
        prediction_source="all",
    )


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Compare live, ui_recovered, and backfill prediction sources.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stake", type=int, default=100)
    args = parser.parse_args()
    result = compare_prediction_sources(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd, stake_per_buy=args.stake)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
