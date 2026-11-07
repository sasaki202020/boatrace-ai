import argparse
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Split score-gap outliers and estimate winner uplift alpha.")
    parser.add_argument("--dist-csv", default="reports/winner_rank_distribution.csv")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--mismatch-csv", default="reports/top1_mismatch_within_top5_max40.csv")
    parser.add_argument("--out-outliers", default="reports/calibration_outliers.csv")
    parser.add_argument("--out-json", default="reports/calibration_result.json")
    parser.add_argument("--gap-threshold", type=float, default=0.1)
    args = parser.parse_args()

    dist_df = pd.read_csv(args.dist_csv)
    proba_df = pd.read_csv(args.proba_csv)
    mismatch_df = pd.read_csv(args.mismatch_csv)

    outliers = dist_df[dist_df["score_gap_to_top1"] >= args.gap_threshold].copy()
    normals = dist_df[dist_df["score_gap_to_top1"] < args.gap_threshold].copy()

    out_outliers = Path(args.out_outliers)
    out_json = Path(args.out_json)
    out_outliers.parent.mkdir(parents=True, exist_ok=True)
    outliers.to_csv(out_outliers, index=False)

    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    proba_df["win_proba_norm"] = pd.to_numeric(proba_df["win_proba_norm"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int).astype(str)

    results = []
    for _, row in normals.iterrows():
        race_id = row["race_id"]
        winner_lane = str(row["actual_winner"])

        race_proba = proba_df[proba_df["race_id"].astype(str) == str(race_id)].copy()
        if race_proba.empty:
            continue

        winner_row = race_proba[race_proba["lane"].astype(str) == winner_lane]
        if winner_row.empty:
            continue

        winner_score = float(winner_row["win_proba_norm"].iloc[0])
        top1_score = float(race_proba["win_proba_norm"].max())
        if winner_score <= 0:
            continue

        min_alpha = top1_score / winner_score + 1e-6
        results.append(
            {
                "race_id": race_id,
                "winner_score": round(winner_score, 6),
                "top1_score": round(top1_score, 6),
                "min_alpha": round(min_alpha, 4),
            }
        )

    cal_df = pd.DataFrame(results)
    if cal_df.empty:
        summary = {
            "normal_races": int(len(normals)),
            "outlier_races": int(len(outliers)),
            "mismatch_rows": int(len(mismatch_df)),
            "error": "no calibration rows",
        }
    else:
        p75 = float(cal_df["min_alpha"].quantile(0.75))
        summary = {
            "normal_races": int(len(normals)),
            "outlier_races": int(len(outliers)),
            "mismatch_rows": int(len(mismatch_df)),
            "alpha": {
                "median": round(float(cal_df["min_alpha"].median()), 4),
                "p75": round(p75, 4),
                "p90": round(float(cal_df["min_alpha"].quantile(0.90)), 4),
                "max": round(float(cal_df["min_alpha"].max()), 4),
            },
            "coverage_at_p75": round(float((cal_df["min_alpha"] <= p75).mean()), 4),
        }

    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[outliers] {len(outliers)} -> {out_outliers}")
    print(f"[saved] {out_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
