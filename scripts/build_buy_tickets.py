from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline.pipeline_utils import ROOT
from src.simulator.odds_loader import load_odds_for_date
from src.strategy.buy_ticket_builder import (
    attach_actual_odds,
    build_buy_tickets_from_skip_decisions,
    save_buy_tickets,
)


DEFAULT_SKIP_PATH = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_ODDS_DIR = Path("data/processed/simulator_inputs")
DEFAULT_OUTPUT_PATH = Path("data/strategy_outputs/buy_tickets.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--skip-path", type=Path, default=DEFAULT_SKIP_PATH, help="Path to skip_decisions.csv")
    parser.add_argument("--odds-path", type=Path, default=None, help="Optional explicit odds csv")
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output path for buy_tickets.csv")
    return parser.parse_args()


def _normalize_date_for_filename(date_str: str) -> str:
    return "".join(ch for ch in str(date_str) if ch.isdigit())[:8]


def main() -> None:
    args = parse_args()

    if not args.skip_path.exists():
        raise FileNotFoundError(f"skip_decisions not found: {args.skip_path}")

    skip_df = pd.read_csv(args.skip_path, low_memory=False)
    print(f"[build_buy_tickets] loaded skip_decisions rows={len(skip_df)}")
    print(f"[build_buy_tickets] columns={list(skip_df.columns)}")

    buy_df = build_buy_tickets_from_skip_decisions(skip_df=skip_df, target_date=args.date)

    if buy_df.empty:
        save_buy_tickets(buy_df, args.output_path)
        print("[build_buy_tickets] no BUY rows for target date")
        print(f"[build_buy_tickets] saved: {args.output_path}")
        return

    odds_path = args.odds_path
    if odds_path is None:
        odds_df = load_odds_for_date(args.date)
        if not odds_df.empty:
            buy_df = attach_actual_odds(buy_df=buy_df, odds_df=odds_df)
        else:
            print("[build_buy_tickets] odds file not found, keep odds as-is")
    else:
        if odds_path.exists():
            odds_df = pd.read_csv(odds_path)
            buy_df = attach_actual_odds(buy_df=buy_df, odds_df=odds_df)
        else:
            print(f"[build_buy_tickets] odds file not found, keep odds as-is: {odds_path}")

    save_buy_tickets(buy_df, args.output_path)

    missing_odds = int(buy_df["odds"].isna().sum()) if "odds" in buy_df.columns else len(buy_df)

    print(f"[build_buy_tickets] saved: {args.output_path}")
    print(f"[build_buy_tickets] rows={len(buy_df)}")
    print(f"[build_buy_tickets] missing_odds={missing_odds}")
    print(buy_df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
