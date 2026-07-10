import argparse
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Analyze true winner rank distribution for top1 mismatch races")
    parser.add_argument(
        "--input-csv",
        default="reports/top1_mismatch_within_top5_max40.csv",
        help="Mismatch detail CSV",
    )
    parser.add_argument(
        "--today-win",
        default="data/model_outputs/today_win_proba.csv",
        help="Per-lane win probability CSV",
    )
    parser.add_argument(
        "--target-mismatch",
        default="first_diff_but_actual_in_top40",
        help="Mismatch type to analyze",
    )
    parser.add_argument("--out-csv", default="reports/winner_rank_distribution.csv")
    parser.add_argument("--out-json", default="reports/winner_rank_distribution_summary.json")
    args = parser.parse_args()

    mismatch_df = pd.read_csv(args.input_csv)
    win_df = pd.read_csv(args.today_win)

    mismatch_df = mismatch_df[mismatch_df["mismatch_type"].astype(str) == args.target_mismatch].copy()
    mismatch_df["actual_winner"] = (
        mismatch_df["actual_trifecta"].astype(str).str.split("-").str[0]
    )

    win_df["lane"] = pd.to_numeric(win_df["lane"], errors="coerce")
    win_df["win_proba_norm"] = pd.to_numeric(win_df["win_proba_norm"], errors="coerce")
    win_df = win_df.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()
    win_df["lane"] = win_df["lane"].astype(int).astype(str)

    records = []
    for race_id, group in mismatch_df.groupby("race_id"):
        winners = group["actual_winner"].astype(str).tolist()
        if not winners:
            continue
        actual_winner = winners[0]

        race_scores = win_df[win_df["race_id"].astype(str) == str(race_id)][["lane", "win_proba_norm"]].copy()
        if race_scores.empty:
            continue
        ranked = race_scores.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        ranked["predicted_rank"] = ranked.index + 1
        ranked_lanes = ranked["lane"].tolist()

        if actual_winner not in ranked_lanes:
            continue

        pred_rank = int(ranked.loc[ranked["lane"] == actual_winner, "predicted_rank"].iloc[0])
        top1_score = float(ranked["win_proba_norm"].iloc[0])
        winner_score = float(ranked.loc[ranked["lane"] == actual_winner, "win_proba_norm"].iloc[0])
        score_gap = top1_score - winner_score

        records.append(
            {
                "race_id": race_id,
                "actual_winner": actual_winner,
                "predicted_rank": pred_rank,
                "score_gap_to_top1": score_gap,
            }
        )

    result_df = pd.DataFrame(records)
    result_df = result_df[result_df["predicted_rank"] > 0].copy()

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    result_df.to_csv(out_csv, index=False)

    rank_counts = result_df["predicted_rank"].value_counts().sort_index()
    gap_series = result_df["score_gap_to_top1"].dropna()

    if result_df.empty:
        summary = {
            "total_races": 0,
            "rank_distribution": {},
            "diagnosis": "対象データなし",
        }
    else:
        summary = {
            "target_mismatch": args.target_mismatch,
            "total_races": int(len(result_df)),
            "rank_distribution": {str(int(k)): int(v) for k, v in rank_counts.items()},
            "rank_median": float(result_df["predicted_rank"].median()),
            "rank_mean": round(float(result_df["predicted_rank"].mean()), 2),
            "rank_mode": int(result_df["predicted_rank"].mode().iloc[0]),
            "score_gap": {
                "mean": round(float(gap_series.mean()), 4),
                "median": round(float(gap_series.median()), 4),
                "p25": round(float(gap_series.quantile(0.25)), 4),
                "p75": round(float(gap_series.quantile(0.75)), 4),
                "max": round(float(gap_series.max()), 4),
            },
            "diagnosis": (
                "calibration候補（score_gap小）"
                if float(gap_series.median()) < 0.05
                else "特徴量 or モデル構造の見直しが必要（score_gap大）"
            ),
        }

    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {out_csv}")
    print(f"[saved] {out_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
