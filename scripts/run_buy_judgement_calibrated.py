from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.strategy.buy_judgement_calibrated import BuyJudgementConfig, judge_buys, summarize_judgement


DEFAULT_INPUT_PATH = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_OUTPUT_PATH = Path("data/strategy_outputs/skip_decisions_rejudged.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BUY judgement using raw or calibrated probability")
    parser.add_argument("--input-path", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--buy-min-ev", type=float, default=0.1)
    parser.add_argument("--buy-min-prob", type=float, default=0.0)
    parser.add_argument("--max-buy-count", type=int, default=3)
    parser.add_argument("--prob-source", choices=["raw", "calibrated"], default="calibrated")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input_path.exists():
        raise FileNotFoundError(f"input not found: {args.input_path}")

    df = pd.read_csv(args.input_path)
    judged_df = judge_buys(
        candidate_df=df,
        config=BuyJudgementConfig(
            buy_min_ev=args.buy_min_ev,
            buy_min_prob=args.buy_min_prob,
            max_buy_count=args.max_buy_count,
            prob_source=args.prob_source,
        ),
    )

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    judged_df.to_csv(args.output_path, index=False, encoding="utf-8")

    summary = summarize_judgement(judged_df)
    print("=== BUY Judgement Summary ===")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"\nSaved: {args.output_path}")


if __name__ == "__main__":
    main()
