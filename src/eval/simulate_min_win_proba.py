import argparse
import json
from pathlib import Path

import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes, normalize_race_key, run_backtest


def load_race_boat_counts(path: Path) -> dict:
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    if not {"race_id", "lane"}.issubset(df.columns):
        return {}
    counts = (
        df[["race_id", "lane"]]
        .dropna(subset=["race_id", "lane"])
        .groupby("race_id")["lane"]
        .nunique()
        .to_dict()
    )
    return {str(k): int(v) for k, v in counts.items()}


def parse_thresholds(raw: str, current: float) -> list[float]:
    if raw:
        vals = [float(v.strip()) for v in str(raw).split(",") if v.strip()]
    else:
        vals = [current, current - 0.05, current - 0.10, current - 0.15]
    vals = [max(0.0, v) for v in vals]
    return sorted(set(vals), reverse=True)


def simulate_decisions(
    ev_df: pd.DataFrame,
    race_boat_counts: dict,
    min_win_proba: float,
    min_ev_threshold: float,
    exclude_non_6_boats: bool,
    exclude_risk_flag: bool,
) -> pd.DataFrame:
    rows = []
    for race_id, group in ev_df.groupby("race_id"):
        g = group.sort_values("ev", ascending=False).reset_index(drop=True)
        top = g.iloc[0]
        decision = "BUY"

        if exclude_non_6_boats:
            actual_boats = race_boat_counts.get(str(race_id))
            if actual_boats is None:
                actual_boats = len(set(str(t).split("-")[0] for t in g["trifecta"]))
            if int(actual_boats) < 6:
                decision = "SKIP"

        if float(top["first_win_proba"]) < float(min_win_proba):
            decision = "SKIP"

        if float(top["ev"]) < float(min_ev_threshold):
            decision = "SKIP"

        if exclude_risk_flag and bool(top.get("risk_flag", False)):
            decision = "SKIP"

        rows.append(
            {
                "race_id": race_id,
                "decision": decision,
                "predicted_trifecta": top["trifecta"],
                "first_win_proba": float(top["first_win_proba"]),
                "approx_prob": float(top["approx_prob"]),
                "odds": float(top["odds"]),
                "ev": float(top["ev"]),
                "risk_flag": bool(top.get("risk_flag", False)),
                "normalized_race_key": normalize_race_key(race_id),
            }
        )
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Simulate min_win_proba sensitivity")
    parser.add_argument("--ev-analysis", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--historical", default="data/processed/historical_races.csv")
    parser.add_argument("--race-card", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--config", default="config/strategy_config.json")
    parser.add_argument("--thresholds", default="", help="Comma-separated min_win_proba values")
    parser.add_argument(
        "--risk-mode",
        default="both",
        choices=["both", "keep", "exclude"],
        help="keep: risk_flagを除外しない / exclude: risk_flag=Trueを除外 / both: 両方比較",
    )
    parser.add_argument("--output", default="reports/min_win_proba_comparison.csv")
    args = parser.parse_args()

    ev_path = Path(args.ev_analysis)
    hist_path = Path(args.historical)
    card_path = Path(args.race_card)
    cfg_path = Path(args.config)
    if not ev_path.exists():
        raise FileNotFoundError(f"ev analysis not found: {ev_path}")
    if not hist_path.exists():
        raise FileNotFoundError(f"historical not found: {hist_path}")
    if not cfg_path.exists():
        raise FileNotFoundError(f"config not found: {cfg_path}")

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    current = float(cfg["skip_conditions"]["min_win_proba"])
    min_ev_threshold = float(cfg["ev_calculation"]["min_ev_threshold"])
    exclude_non_6_boats = bool(cfg["skip_conditions"].get("exclude_non_6_boats", False))
    thresholds = parse_thresholds(args.thresholds, current)

    ev_df = pd.read_csv(ev_path)
    race_boat_counts = load_race_boat_counts(card_path)
    outcomes_df = build_race_outcomes(hist_path)

    results = []
    risk_modes = [False, True] if args.risk_mode == "both" else ([False] if args.risk_mode == "keep" else [True])
    for th in thresholds:
        for exclude_risk_flag in risk_modes:
            decisions = simulate_decisions(
                ev_df=ev_df,
                race_boat_counts=race_boat_counts,
                min_win_proba=th,
                min_ev_threshold=min_ev_threshold,
                exclude_non_6_boats=exclude_non_6_boats,
                exclude_risk_flag=exclude_risk_flag,
            )
            total_buy = int((decisions["decision"] == "BUY").sum())
            _, summary = run_backtest(decisions, outcomes_df)
            results.append(
                {
                    "min_win_proba": th,
                    "exclude_risk_flag": exclude_risk_flag,
                    "buy_count_total": total_buy,
                    "buy_count_result_available": int(summary["buy_count"]),
                    "result_available_rows": int(summary["result_available_rows"]),
                    "hit_rate": summary["hit_rate"],
                    "roi": summary["roi"],
                    "avg_odds": summary["avg_odds"],
                }
            )

    out_df = pd.DataFrame(results).sort_values(["min_win_proba", "exclude_risk_flag"], ascending=[False, True]).reset_index(drop=True)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    print(f"min_win_proba comparison saved: {out_path}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
