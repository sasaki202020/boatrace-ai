from __future__ import annotations

import argparse
import json
import sys

from src.evaluation.backtest_range import run_backtest_range


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Run a range backtest.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stake", type=int, default=100)
    parser.add_argument("--prediction-source", default="auto", choices=["auto", "live", "ui_recovered", "backfill", "all"])
    args = parser.parse_args()
    result = run_backtest_range(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd, stake_per_buy=args.stake, prediction_source=args.prediction_source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
