import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def main():
    parser = argparse.ArgumentParser(description="Compare prob/ev/hybrid top1 by fixed approx_prob gate")
    parser.add_argument("--ev-analysis", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--backtest-races", default="reports/backtest_race_results.csv")
    parser.add_argument("--gate", type=float, default=0.05, help="use EV top1 only when prob_top approx_prob >= gate")
    parser.add_argument(
        "--odds-delta-max",
        type=float,
        default=None,
        help="if set, also require ev_top_odds - prob_top_odds <= odds_delta_max when using EV",
    )
    parser.add_argument("--out-races", default="reports/hybrid_top1_race_comparison.csv")
    parser.add_argument("--out-summary", default="reports/hybrid_top1_summary.json")
    args = parser.parse_args()

    ev = pd.read_csv(args.ev_analysis)
    bt = pd.read_csv(args.backtest_races)

    truth = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    truth["result_available"] = to_bool(truth["result_available"])
    truth = truth[truth["result_available"]].copy()

    work = ev[["race_id", "trifecta", "approx_prob", "first_win_proba", "ev", "odds"]].copy()
    for c in ["approx_prob", "first_win_proba", "ev", "odds"]:
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.merge(truth, on="race_id", how="inner")

    prob_top = (
        work.sort_values(["race_id", "approx_prob"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()
        .rename(
            columns={
                "trifecta": "prob_top_trifecta",
                "approx_prob": "prob_top_approx_prob",
                "ev": "prob_top_ev",
                "odds": "prob_top_odds",
            }
        )
    )
    ev_top = (
        work.sort_values(["race_id", "ev"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()
        .rename(
            columns={
                "trifecta": "ev_top_trifecta",
                "approx_prob": "ev_top_approx_prob",
                "ev": "ev_top_ev",
                "odds": "ev_top_odds",
            }
        )
    )

    comp = prob_top[
        ["race_id", "actual_trifecta", "prob_top_trifecta", "prob_top_approx_prob", "prob_top_ev", "prob_top_odds"]
    ].merge(
        ev_top[["race_id", "ev_top_trifecta", "ev_top_approx_prob", "ev_top_ev", "ev_top_odds"]],
        on="race_id",
        how="inner",
    )
    comp["odds_delta"] = comp["ev_top_odds"] - comp["prob_top_odds"]
    comp["use_ev_for_hybrid"] = comp["prob_top_approx_prob"] >= float(args.gate)
    if args.odds_delta_max is not None:
        comp["use_ev_for_hybrid"] &= comp["odds_delta"] <= float(args.odds_delta_max)
    comp["hybrid_top_trifecta"] = comp["prob_top_trifecta"]
    comp.loc[comp["use_ev_for_hybrid"], "hybrid_top_trifecta"] = comp.loc[comp["use_ev_for_hybrid"], "ev_top_trifecta"]

    comp["prob_hit"] = comp["prob_top_trifecta"] == comp["actual_trifecta"]
    comp["ev_hit"] = comp["ev_top_trifecta"] == comp["actual_trifecta"]
    comp["hybrid_hit"] = comp["hybrid_top_trifecta"] == comp["actual_trifecta"]

    comp["prob_ev_same"] = comp["prob_top_trifecta"] == comp["ev_top_trifecta"]
    comp["prob_hybrid_same"] = comp["prob_top_trifecta"] == comp["hybrid_top_trifecta"]
    comp["ev_hybrid_same"] = comp["ev_top_trifecta"] == comp["hybrid_top_trifecta"]

    comp["hybrid_better_than_prob"] = (~comp["prob_hit"]) & comp["hybrid_hit"]
    comp["hybrid_worse_than_prob"] = comp["prob_hit"] & (~comp["hybrid_hit"])

    out_races = Path(args.out_races)
    out_races.parent.mkdir(parents=True, exist_ok=True)
    comp.to_csv(out_races, index=False)

    n = len(comp)
    summary = {
        "races": int(n),
        "gate": float(args.gate),
        "hits": {
            "prob_top1": int(comp["prob_hit"].sum()),
            "ev_top1": int(comp["ev_hit"].sum()),
            "hybrid_top1": int(comp["hybrid_hit"].sum()),
        },
        "hit_rates": {
            "prob_top1": float(comp["prob_hit"].mean()) if n else 0.0,
            "ev_top1": float(comp["ev_hit"].mean()) if n else 0.0,
            "hybrid_top1": float(comp["hybrid_hit"].mean()) if n else 0.0,
        },
        "agreement": {
            "prob_ev_same": int(comp["prob_ev_same"].sum()),
            "prob_ev_diff": int((~comp["prob_ev_same"]).sum()),
            "prob_hybrid_same": int(comp["prob_hybrid_same"].sum()),
            "prob_hybrid_diff": int((~comp["prob_hybrid_same"]).sum()),
            "ev_hybrid_same": int(comp["ev_hybrid_same"].sum()),
            "ev_hybrid_diff": int((~comp["ev_hybrid_same"]).sum()),
        },
        "hybrid_vs_prob": {
            "improved_races": int(comp["hybrid_better_than_prob"].sum()),
            "worsened_races": int(comp["hybrid_worse_than_prob"].sum()),
        },
        "hybrid_ev_usage": {
            "use_ev_races": int(comp["use_ev_for_hybrid"].sum()),
            "use_prob_races": int((~comp["use_ev_for_hybrid"]).sum()),
        },
    }

    out_summary = Path(args.out_summary)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    # Also print in a human-readable way
    print(f"saved: {out_races}")
    print(f"saved: {out_summary}")
    print(summary)


if __name__ == "__main__":
    main()
