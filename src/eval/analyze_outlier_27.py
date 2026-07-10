import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def numeric_feature_columns(df: pd.DataFrame) -> list[str]:
    ignore = {"race_id", "lane", "date", "win_proba_raw", "win_proba_norm"}
    cols = []
    for c in df.columns:
        if c in ignore:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def safe_float(v):
    try:
        return float(v)
    except Exception:
        return np.nan


def main():
    parser = argparse.ArgumentParser(description="Analyze 27 outlier races (feature extremeness and drift).")
    parser.add_argument("--outliers-csv", default="reports/calibration_outliers.csv")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features-csv", default="data/features/today_features.csv")
    parser.add_argument("--out-summary-csv", default="reports/outlier27_summary.csv")
    parser.add_argument("--out-pattern-json", default="reports/outlier27_pattern_summary.json")
    args = parser.parse_args()

    outliers = pd.read_csv(args.outliers_csv).copy()
    proba = pd.read_csv(args.proba_csv).copy()
    feat = pd.read_csv(args.features_csv).copy()

    proba["lane"] = pd.to_numeric(proba["lane"], errors="coerce")
    feat["lane"] = pd.to_numeric(feat["lane"], errors="coerce")
    proba = proba.dropna(subset=["race_id", "lane"]).copy()
    feat = feat.dropna(subset=["race_id", "lane"]).copy()
    proba["lane"] = proba["lane"].astype(int)
    feat["lane"] = feat["lane"].astype(int)

    merged = proba.merge(feat, on=["race_id", "lane"], how="left", suffixes=("", "_feat"))
    feature_cols = numeric_feature_columns(merged)
    for c in feature_cols:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")

    # global stats for drift/extreme detection
    stats = {}
    for c in feature_cols:
        s = merged[c].dropna()
        if len(s) == 0:
            continue
        stats[c] = {"mean": float(s.mean()), "std": float(s.std() if s.std() > 0 else 1e-9)}

    rows = []
    extreme_counter = {}
    outlier_ids = set(outliers["race_id"].astype(str))

    for _, o in outliers.iterrows():
        race_id = str(o["race_id"])
        actual_winner = str(o["actual_winner"]).strip()
        race_df = merged[merged["race_id"].astype(str) == race_id].copy()
        if race_df.empty:
            continue

        race_df = race_df.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        top1 = race_df.iloc[0]
        winner_rows = race_df[race_df["lane"].astype(str) == actual_winner]
        if winner_rows.empty:
            continue
        winner = winner_rows.iloc[0]

        winner_prob = safe_float(winner.get("win_proba_norm"))
        top1_prob = safe_float(top1.get("win_proba_norm"))
        prob_gap = safe_float(top1_prob - winner_prob)

        # identify most extreme feature on winner row (|z| max)
        best_feat = None
        best_abs_z = -1.0
        best_z = np.nan
        for c in feature_cols:
            if c not in stats:
                continue
            val = safe_float(winner.get(c))
            if np.isnan(val):
                continue
            z = (val - stats[c]["mean"]) / (stats[c]["std"] + 1e-9)
            if abs(z) > best_abs_z:
                best_abs_z = abs(z)
                best_z = z
                best_feat = c

        if best_feat is not None:
            extreme_counter[best_feat] = extreme_counter.get(best_feat, 0) + 1

        # winner-top1 feature drift
        diff_abs = []
        for c in feature_cols:
            w = safe_float(winner.get(c))
            t = safe_float(top1.get(c))
            if np.isnan(w) or np.isnan(t):
                continue
            diff_abs.append(abs(w - t))
        mean_feature_diff_abs = float(np.mean(diff_abs)) if diff_abs else np.nan

        rows.append(
            {
                "race_id": race_id,
                "is_outlier": race_id in outlier_ids,
                "actual_winner_lane": int(float(actual_winner)),
                "pred_top1_lane": int(top1["lane"]),
                "winner_label": 1,
                "pred_top1_label": int(str(top1["lane"]) == actual_winner),
                "winner_win_proba_norm": winner_prob,
                "pred_top1_win_proba_norm": top1_prob,
                "prob_gap_top1_minus_winner": prob_gap,
                "score_gap_to_top1": safe_float(o.get("score_gap_to_top1")),
                "winner_predicted_rank": int(o.get("predicted_rank")),
                "winner_most_extreme_feature": best_feat,
                "winner_most_extreme_zscore": float(best_z) if not np.isnan(best_z) else np.nan,
                "winner_most_extreme_abs_zscore": float(best_abs_z) if best_feat is not None else np.nan,
                "winner_vs_top1_mean_abs_feature_diff": mean_feature_diff_abs,
            }
        )

    summary_df = pd.DataFrame(rows).sort_values(
        ["winner_most_extreme_abs_zscore", "prob_gap_top1_minus_winner"], ascending=[False, False]
    )

    out_csv = Path(args.out_summary_csv)
    out_json = Path(args.out_pattern_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(out_csv, index=False)

    n = len(summary_df)
    pattern = {
        "outlier_count": int(n),
        "top_extreme_features": sorted(
            [{"feature": k, "count": v} for k, v in extreme_counter.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10],
        "prob_gap": {
            "mean": round(float(summary_df["prob_gap_top1_minus_winner"].mean()), 4) if n else None,
            "median": round(float(summary_df["prob_gap_top1_minus_winner"].median()), 4) if n else None,
            "p75": round(float(summary_df["prob_gap_top1_minus_winner"].quantile(0.75)), 4) if n else None,
            "max": round(float(summary_df["prob_gap_top1_minus_winner"].max()), 4) if n else None,
        },
        "winner_rank_dist": {
            str(int(k)): int(v)
            for k, v in summary_df["winner_predicted_rank"].value_counts().sort_index().items()
        } if n else {},
        "extreme_abs_zscore": {
            "mean": round(float(summary_df["winner_most_extreme_abs_zscore"].mean()), 4) if n else None,
            "median": round(float(summary_df["winner_most_extreme_abs_zscore"].median()), 4) if n else None,
            "p75": round(float(summary_df["winner_most_extreme_abs_zscore"].quantile(0.75)), 4) if n else None,
            "max": round(float(summary_df["winner_most_extreme_abs_zscore"].max()), 4) if n else None,
        },
        "drift_mean_abs_feature_diff": {
            "mean": round(float(summary_df["winner_vs_top1_mean_abs_feature_diff"].mean()), 4) if n else None,
            "median": round(float(summary_df["winner_vs_top1_mean_abs_feature_diff"].median()), 4) if n else None,
        },
    }
    out_json.write_text(json.dumps(pattern, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {out_csv}")
    print(f"[saved] {out_json}")
    print(json.dumps(pattern, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
