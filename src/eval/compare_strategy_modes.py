import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategy.generate_trifecta_candidates import TrifectaGenerator
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


def resolve_odds_path(explicit_path: str, live_path: str, fallback_path: str) -> str:
    explicit = str(explicit_path or "").strip()
    if explicit:
        return explicit
    if os.path.exists(live_path):
        return live_path
    return fallback_path


def summarize(frame: pd.DataFrame) -> dict[str, object]:
    work = frame.copy()
    if "ev" in work.columns:
        work["ev"] = pd.to_numeric(work["ev"], errors="coerce")
    else:
        work["ev"] = 0.0
    if "adjusted_score" in work.columns:
        work["adjusted_score"] = pd.to_numeric(work["adjusted_score"], errors="coerce")
    else:
        work["adjusted_score"] = 0.0
    if "decision" in work.columns:
        work["decision"] = work["decision"].astype(str).str.upper()
    else:
        work["decision"] = "SKIP"
    if "high_ev_suspect_flag" in work.columns:
        work["high_ev_suspect_flag"] = work["high_ev_suspect_flag"].astype(str).str.lower().isin({"true", "1", "yes"})
    else:
        work["high_ev_suspect_flag"] = False
    if "rescue_applied" in work.columns:
        work["rescue_applied"] = work["rescue_applied"].astype(str).str.lower().isin({"true", "1", "yes"})
    else:
        work["rescue_applied"] = False
    return {
        "total_candidates": int(len(work)),
        "buy_count": int((work["decision"] == "BUY").sum()),
        "watch_count": int((work["decision"] == "WATCH").sum()),
        "skip_count": int((work["decision"] == "SKIP").sum()),
        "pending_count": int((work["decision"] == "PENDING").sum()),
        "avg_ev": float(work["ev"].mean()) if len(work) else None,
        "avg_adjusted_score": float(work["adjusted_score"].mean()) if len(work) else None,
        "suspect_high_ev_count": int(work["high_ev_suspect_flag"].sum()),
        "rescue_applied_count": int(work["rescue_applied"].sum()),
    }


def run_scenario(
    *,
    scenario_name: str,
    candidate_generation_mode: str,
    use_unified_score: bool,
    win_proba_path: str,
    odds_path: str,
    race_card_path: str,
    config_path: str,
    temp_root: str,
) -> dict[str, object]:
    generator = TrifectaGenerator(config_path=config_path)
    generator.candidate_generation_mode = candidate_generation_mode
    candidates = generator.generate(win_proba_path)
    if candidates is None or candidates.empty:
        return {
            "scenario": scenario_name,
            "candidate_generation_mode": candidate_generation_mode,
            "use_unified_score": use_unified_score,
            "error": "no_candidates",
        }

    evaluator = StrategyEvaluator(config_path=config_path)
    evaluator.use_unified_score = bool(use_unified_score)

    tmp_path = Path(temp_root) / f"{scenario_name}_candidates.csv"
    candidates.to_csv(tmp_path, index=False)
    ev_df = evaluator.build_ev_analysis(str(tmp_path), odds_path=odds_path)
    race_boat_counts = evaluator._load_race_boat_counts(race_card_path)
    skip_df = evaluator.build_skip_decisions(
        ev_df,
        race_boat_counts=race_boat_counts,
        race_card_path=race_card_path,
    )

    summary = summarize(skip_df)
    summary.update(
        {
            "scenario": scenario_name,
            "candidate_generation_mode": candidate_generation_mode,
            "use_unified_score": bool(use_unified_score),
        }
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare legacy/expanded candidate generation and unified score decisions.")
    parser.add_argument("--win-proba-path", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--odds-path", default="", help="Optional explicit odds CSV path.")
    parser.add_argument("--live-odds-path", default="data/strategy_outputs/live_odds.csv")
    parser.add_argument("--fallback-odds-path", default="data/odds/today_trifecta_odds.csv")
    parser.add_argument("--race-card-path", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--config-path", default="config/strategy_config.json")
    parser.add_argument("--out-dir", default="reports/strategy_mode_compare")
    args = parser.parse_args()

    odds_path = resolve_odds_path(args.odds_path, args.live_odds_path, args.fallback_odds_path)
    scenarios = [
        {"scenario": "legacy_approx", "candidate_generation_mode": "legacy", "use_unified_score": False},
        {"scenario": "legacy_unified", "candidate_generation_mode": "legacy", "use_unified_score": True},
        {"scenario": "expanded_approx", "candidate_generation_mode": "expanded", "use_unified_score": False},
        {"scenario": "expanded_unified", "candidate_generation_mode": "expanded", "use_unified_score": True},
    ]

    rows = []
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = out_dir / "_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    for scenario in scenarios:
        rows.append(
            run_scenario(
                scenario_name=scenario["scenario"],
                candidate_generation_mode=scenario["candidate_generation_mode"],
                use_unified_score=scenario["use_unified_score"],
                win_proba_path=args.win_proba_path,
                odds_path=odds_path,
                race_card_path=args.race_card_path,
                config_path=args.config_path,
                temp_root=str(tmp_root),
            )
        )

    summary_df = pd.DataFrame(rows)
    summary_csv = out_dir / "strategy_mode_compare.csv"
    summary_json = out_dir / "strategy_mode_compare_summary.json"
    summary_df.to_csv(summary_csv, index=False)

    baseline = summary_df[summary_df["scenario"] == "legacy_approx"]
    baseline_row = baseline.iloc[0] if not baseline.empty else None
    comparison = {}
    if baseline_row is not None:
        for _, row in summary_df.iterrows():
            if row["scenario"] == "legacy_approx":
                continue
            comparison[row["scenario"]] = {
                "total_candidates_delta": int(row["total_candidates"] - baseline_row["total_candidates"]),
                "buy_count_delta": int(row["buy_count"] - baseline_row["buy_count"]),
                "watch_count_delta": int(row["watch_count"] - baseline_row["watch_count"]),
                "skip_count_delta": int(row["skip_count"] - baseline_row["skip_count"]),
                "pending_count_delta": int(row["pending_count"] - baseline_row["pending_count"]),
                "avg_ev_delta": float((row["avg_ev"] or 0.0) - (baseline_row["avg_ev"] or 0.0)),
                "avg_adjusted_score_delta": float((row["avg_adjusted_score"] or 0.0) - (baseline_row["avg_adjusted_score"] or 0.0)),
                "suspect_high_ev_count_delta": int(row["suspect_high_ev_count"] - baseline_row["suspect_high_ev_count"]),
                "rescue_applied_count_delta": int(row["rescue_applied_count"] - baseline_row["rescue_applied_count"]),
            }

    summary_json.write_text(
        json.dumps(
            {
                "odds_path": odds_path,
                "scenarios": rows,
                "comparison_vs_legacy_approx": comparison,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"saved: {summary_csv}")
    print(f"saved: {summary_json}")
    print(summary_df.to_string(index=False))
    if comparison:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
