from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline.pipeline_utils import ROOT
from src.simulator.execution_simulator import (
    SimulationConfig,
    save_simulation_results,
    simulate_bets,
    summarize_simulation,
)
from src.simulator.results_loader import load_results_for_date


DEFAULT_BUY_PATH = Path("data/strategy_outputs/buy_tickets.csv")
DEFAULT_INPUT_DIR = Path("data/processed/simulator_inputs")
DEFAULT_OUTPUT_DIR = Path("reports/simulator")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--buy-path", type=Path, default=DEFAULT_BUY_PATH)
    parser.add_argument("--results-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--stake", type=int, default=100)
    return parser.parse_args()


def _normalize_date_for_filename(date_str: str) -> str:
    return "".join(ch for ch in str(date_str).replace("-", "") if ch.isdigit())[:8]


def main() -> None:
    args = parse_args()
    ymd = _normalize_date_for_filename(args.date)

    if not args.buy_path.exists():
        raise FileNotFoundError(f"buy file not found: {args.buy_path}")

    buy_df = pd.read_csv(args.buy_path)
    if "date" in buy_df.columns:
        buy_df["date"] = buy_df["date"].astype(str).str.strip()
    if "race_key" not in buy_df.columns and "race_id" in buy_df.columns:
        buy_df["race_key"] = buy_df["race_id"].astype(str).str.replace(r"^(\d{8})-(\d{2})-(\d{2})$", r"d\1-c\2-r\3", regex=True)

    results_path = args.results_path
    if results_path is None:
        results_path = ROOT / "data" / "raw" / "official" / "results" / f"K{ymd[2:]}.TXT"

    result_df, result_status = load_results_for_date(args.date)
    if result_df.empty and results_path.exists():
        pass

    sim_df = simulate_bets(
        buy_df=buy_df,
        result_df=result_df,
        config=SimulationConfig(stake_per_ticket=args.stake),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sim_output_path = args.output_dir / f"simulation_results_{ymd}.csv"
    save_simulation_results(sim_df, sim_output_path)

    summary = summarize_simulation(sim_df)
    summary_df = pd.DataFrame([{"date": args.date, **summary}])
    summary_output_path = args.output_dir / f"simulation_summary_{ymd}.csv"
    summary_df.to_csv(summary_output_path, index=False, encoding="utf-8")

    print("=== Simulation Summary ===")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print("\nSaved:")
    print(f"- {sim_output_path}")
    print(f"- {summary_output_path}")
    print(f"- result_status={result_status.status}")
    print(f"- result_source={result_status.source_path}")


if __name__ == "__main__":
    main()
