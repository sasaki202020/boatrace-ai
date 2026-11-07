from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Allow direct execution from scripts/
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
from src.simulator.odds_loader import load_odds_for_date
from src.simulator.results_loader import load_results_for_date
from src.simulator.ticket_builder import build_buy_tickets


BUY_PATH = ROOT / "data" / "strategy_outputs" / "buy_tickets.csv"
SKIP_PATH = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
SIM_OUTPUT_PATH = ROOT / "data" / "strategy_outputs" / "simulation_results.csv"
REPORT_ROOT = ROOT / "reports" / "simulator"


def _latest_date_from_skip(skip_df: pd.DataFrame) -> str | None:
    if skip_df.empty or "date" not in skip_df.columns:
        return None
    dates = pd.to_datetime(skip_df["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.dt.date.max().isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily BOAT RACE simulation")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to latest date in skip_decisions.")
    parser.add_argument("--stake", type=int, default=100, help="Stake per ticket")
    parser.add_argument(
        "--skip-decisions",
        help="Optional path to skip_decisions.csv. Defaults to reports/daily/<date>/skip_decisions.csv when available.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = args.date
    skip_source_path: Path | None = Path(args.skip_decisions) if args.skip_decisions else None

    if target_date:
        date_skip_path = ROOT / "reports" / "daily" / target_date / "skip_decisions.csv"
        if skip_source_path is None and date_skip_path.exists():
            skip_source_path = date_skip_path
        elif skip_source_path is None and SKIP_PATH.exists():
            skip_source_path = SKIP_PATH
    else:
        if skip_source_path is None:
            if not SKIP_PATH.exists():
                raise FileNotFoundError(f"skip decisions not found: {SKIP_PATH}")
            skip_source_path = SKIP_PATH

    if skip_source_path is None or not skip_source_path.exists():
        raise FileNotFoundError(f"skip decisions not found: {skip_source_path}")

    skip_df = pd.read_csv(skip_source_path, low_memory=False)
    target_date = target_date or _latest_date_from_skip(skip_df)
    if not target_date:
        raise RuntimeError("No target date found. Pass --date or ensure skip_decisions has date values.")

    odds_df = load_odds_for_date(target_date)
    buy_df = build_buy_tickets(skip_df, odds_df, target_date=target_date)
    result_df, result_status = load_results_for_date(target_date)

    if buy_df.empty:
        buy_df.to_csv(BUY_PATH, index=False, encoding="utf-8")
        save_simulation_results(buy_df.assign(stake=pd.NA, hit=pd.NA, payout=pd.NA, profit=pd.NA), SIM_OUTPUT_PATH)
        summary = summarize_simulation(pd.DataFrame())
    else:
        save_buy = buy_df.copy()
        if "odds" not in save_buy.columns:
            save_buy["odds"] = pd.NA
        if "race_key" not in save_buy.columns and "race_id" in save_buy.columns:
            save_buy["race_key"] = save_buy["race_id"]
        save_buy.to_csv(BUY_PATH, index=False, encoding="utf-8")
        sim_df = simulate_bets(save_buy, result_df, SimulationConfig(stake_per_ticket=args.stake))
        save_simulation_results(sim_df, SIM_OUTPUT_PATH)
        summary = summarize_simulation(sim_df)

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_dir = REPORT_ROOT / target_date
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": target_date,
        "result_status": result_status.status,
        "result_source": result_status.source_path,
        "result_warning": result_status.warning,
        "buy_count": summary["buy_count"],
        "hit_count": summary["hit_count"],
        "hit_rate": summary["hit_rate"],
        "total_stake": summary["total_stake"],
        "total_payout": summary["total_payout"],
        "total_profit": summary["total_profit"],
        "roi": summary["roi"],
        "stake_per_ticket": args.stake,
    }
    (report_dir / "simulation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[simulator] skip_decisions_source={skip_source_path}")


if __name__ == "__main__":
    main()
