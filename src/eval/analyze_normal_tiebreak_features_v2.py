import argparse
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description="Analyze normal races with today_features joined by race_id+lane."
    )
    parser.add_argument("--dist-csv", default="reports/winner_rank_distribution.csv")
    parser.add_argument("--outliers-csv", default="reports/calibration_outliers.csv")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features-csv", default="data/features/today_features.csv")
    parser.add_argument("--out-diff-csv", default="reports/tiebreak_feature_diff_v2.csv")
    parser.add_argument("--out-json", default="reports/tiebreak_feature_summary_v2.json")
    parser.add_argument("--winner-higher-threshold", type=float, default=0.6)
    args = parser.parse_args()

    dist_df = pd.read_csv(args.dist_csv)
    outliers_df = pd.read_csv(args.outliers_csv)
    proba_df = pd.read_csv(args.proba_csv)
    feat_df = pd.read_csv(args.features_csv)

    # normalize join keys
    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    feat_df["lane"] = feat_df["lane"].astype(int)

    merged_df = proba_df.merge(feat_df, on=["race_id", "lane"], how="left", suffixes=("", "_feat"))
    merged_df["win_proba_norm"] = pd.to_numeric(merged_df["win_proba_norm"], errors="coerce")
    merged_df = merged_df.dropna(subset=["win_proba_norm"]).copy()

    outlier_ids = set(outliers_df["race_id"].astype(str))
    normal_df = dist_df[~dist_df["race_id"].astype(str).isin(outlier_ids)].copy()
    normal_df["actual_winner"] = normal_df["actual_winner"].astype(str).str.strip()

    exclude_cols = {"race_id", "lane", "win_proba_norm", "win_proba_raw"}
    feature_cols = [
        c
        for c in merged_df.columns
        if c not in exclude_cols and pd.api.types.is_numeric_dtype(merged_df[c])
    ]

    records = []
    for _, row in normal_df.iterrows():
        race_id = str(row["race_id"])
        actual_winner = str(row["actual_winner"])

        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue
        race_data = race_data.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        top1_row = race_data.iloc[0]

        winner_rows = race_data[race_data["lane"].astype(str) == actual_winner]
        if winner_rows.empty:
            continue
        winner_row = winner_rows.iloc[0]

        rec = {"race_id": race_id}
        for feat in feature_cols:
            w_val = winner_row.get(feat)
            t_val = top1_row.get(feat)
            if pd.notna(w_val) and pd.notna(t_val):
                rec[f"{feat}_winner"] = round(float(w_val), 6)
                rec[f"{feat}_top1"] = round(float(t_val), 6)
                rec[f"{feat}_diff"] = round(float(w_val) - float(t_val), 6)
        records.append(rec)

    diff_df = pd.DataFrame(records)
    out_diff = Path(args.out_diff_csv)
    out_json = Path(args.out_json)
    out_diff.parent.mkdir(parents=True, exist_ok=True)
    diff_df.to_csv(out_diff, index=False)

    summary = {"n_races": int(len(diff_df)), "feature_columns": feature_cols, "features": {}, "tiebreak_candidates": []}
    for feat in feature_cols:
        diff_col = f"{feat}_diff"
        if diff_col not in diff_df.columns:
            continue
        s = pd.to_numeric(diff_df[diff_col], errors="coerce").dropna()
        if len(s) == 0:
            continue
        summary["features"][feat] = {
            "diff_mean": round(float(s.mean()), 4),
            "diff_median": round(float(s.median()), 4),
            "diff_std": round(float(s.std()), 4),
            "winner_higher_rate": round(float((s > 0).mean()), 4),
        }

    candidates = {
        feat: stat for feat, stat in summary["features"].items() if stat["winner_higher_rate"] >= args.winner_higher_threshold
    }
    summary["tiebreak_candidates"] = sorted(
        candidates.keys(),
        key=lambda f: candidates[f]["winner_higher_rate"],
        reverse=True,
    )

    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[features] {len(feature_cols)}: {feature_cols}")
    print(f"[saved] {out_diff}")
    print(f"[saved] {out_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
