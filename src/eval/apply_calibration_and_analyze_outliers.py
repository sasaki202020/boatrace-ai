import argparse
import json
from pathlib import Path

import pandas as pd


def safe_div(a: float, b: float) -> float:
    return float(a / b) if b else 0.0


def main():
    parser = argparse.ArgumentParser(
        description="Apply winner-alpha simulation and analyze outlier race structure"
    )
    parser.add_argument("--outliers-csv", default="reports/calibration_outliers.csv")
    parser.add_argument("--dist-csv", default="reports/winner_rank_distribution.csv")
    parser.add_argument("--mismatch-csv", default="reports/top1_mismatch_within_top5_max40.csv")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--out-hitrate", default="reports/calibration_applied_hitrate.json")
    parser.add_argument("--out-outlier-csv", default="reports/outlier_analysis.csv")
    parser.add_argument("--out-outlier-json", default="reports/outlier_analysis_summary.json")
    parser.add_argument("--gap-threshold", type=float, default=0.1)
    args = parser.parse_args()

    outliers_df = pd.read_csv(args.outliers_csv)
    dist_df = pd.read_csv(args.dist_csv)
    mismatch_df = pd.read_csv(args.mismatch_csv)
    proba_df = pd.read_csv(args.proba_csv)

    # Normalize columns
    mismatch_df["actual_winner"] = (
        mismatch_df["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
    )
    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    proba_df["win_proba_norm"] = pd.to_numeric(proba_df["win_proba_norm"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int).astype(str)

    # Split with same threshold for reproducibility
    outliers = dist_df[dist_df["score_gap_to_top1"] >= args.gap_threshold].copy()
    normals = dist_df[dist_df["score_gap_to_top1"] < args.gap_threshold].copy()
    outlier_ids = set(outliers["race_id"].astype(str))

    # =========================================================
    # PART1: alpha-wise hitrate simulation on normal races
    # =========================================================
    alpha_candidates = [1.0, 1.01, 1.02, 1.05, 1.10, 1.20, 1.50]
    hitrate_results: dict[str, dict[str, float | int]] = {}

    for alpha in alpha_candidates:
        hits = 0
        total = 0
        for _, row in normals.iterrows():
            race_id = str(row["race_id"])
            actual_winner = str(row["actual_winner"])
            race_proba = proba_df[proba_df["race_id"].astype(str) == race_id].copy()
            if race_proba.empty:
                continue
            mask = race_proba["lane"].astype(str) == actual_winner
            if not mask.any():
                continue

            race_proba.loc[mask, "win_proba_norm"] = race_proba.loc[mask, "win_proba_norm"] * alpha
            top1_horse = str(race_proba.sort_values("win_proba_norm", ascending=False).iloc[0]["lane"])
            if top1_horse == actual_winner:
                hits += 1
            total += 1

        hitrate_results[str(alpha)] = {
            "hits": int(hits),
            "total": int(total),
            "hitrate": round(safe_div(hits, total), 4),
        }

    out_hitrate = Path(args.out_hitrate)
    out_hitrate.parent.mkdir(parents=True, exist_ok=True)
    out_hitrate.write_text(json.dumps(hitrate_results, ensure_ascii=False, indent=2), encoding="utf-8")

    # =========================================================
    # PART2: outlier structure analysis
    # =========================================================
    outlier_detail = []
    for _, row in outliers_df.iterrows():
        race_id = str(row["race_id"])
        actual_winner = str(row["actual_winner"])
        race_proba = proba_df[proba_df["race_id"].astype(str) == race_id].copy()
        if race_proba.empty:
            continue
        race_proba = race_proba.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        winner_rows = race_proba[race_proba["lane"].astype(str) == actual_winner]
        if winner_rows.empty:
            continue

        n_horses = int(len(race_proba))
        top1_score = float(race_proba.iloc[0]["win_proba_norm"])
        top1_horse = str(race_proba.iloc[0]["lane"])
        winner_rank = int(winner_rows.index[0]) + 1
        winner_score = float(winner_rows.iloc[0]["win_proba_norm"])

        mean_score = float(race_proba["win_proba_norm"].mean())
        std_score = float(race_proba["win_proba_norm"].std())
        top1_zscore = (top1_score - mean_score) / (std_score + 1e-9)

        outlier_detail.append(
            {
                "race_id": race_id,
                "n_horses": n_horses,
                "actual_winner": actual_winner,
                "winner_rank": winner_rank,
                "winner_score": round(winner_score, 6),
                "top1_score": round(top1_score, 6),
                "score_gap": round(float(row["score_gap_to_top1"]), 4),
                "top1_zscore": round(float(top1_zscore), 3),
                "top1_horse": top1_horse,
                "is_outlier_race_id": race_id in outlier_ids,
            }
        )

    oa_df = pd.DataFrame(outlier_detail)
    out_oa_csv = Path(args.out_outlier_csv)
    out_oa_json = Path(args.out_outlier_json)
    out_oa_csv.parent.mkdir(parents=True, exist_ok=True)
    oa_df.to_csv(out_oa_csv, index=False)

    if oa_df.empty:
        oa_summary = {"outlier_count": 0, "error": "no outlier detail rows"}
    else:
        oa_summary = {
            "outlier_count": int(len(oa_df)),
            "winner_rank_dist": {str(int(k)): int(v) for k, v in oa_df["winner_rank"].value_counts().sort_index().items()},
            "top1_zscore": {
                "mean": round(float(oa_df["top1_zscore"].mean()), 3),
                "median": round(float(oa_df["top1_zscore"].median()), 3),
                "p75": round(float(oa_df["top1_zscore"].quantile(0.75)), 3),
            },
            "hypothesis": (
                "top1過大評価（特徴量バイアス）"
                if float(oa_df["top1_zscore"].median()) > 2.0
                else "識別力不足（スコア拮抗）"
            ),
        }
    out_oa_json.write_text(json.dumps(oa_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[PART1] alpha別命中率")
    print(json.dumps(hitrate_results, ensure_ascii=False, indent=2))
    print("\n[PART2] 外れ値分析")
    print(json.dumps(oa_summary, ensure_ascii=False, indent=2))
    print(f"\n[saved] {out_hitrate}")
    print(f"[saved] {out_oa_csv}")
    print(f"[saved] {out_oa_json}")


if __name__ == "__main__":
    main()
