import argparse
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Offline rerank with tiebreak features.")
    parser.add_argument("--dist-csv", default="reports/winner_rank_distribution.csv")
    parser.add_argument("--outliers-csv", default="reports/calibration_outliers.csv")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features-csv", default="data/features/today_features.csv")
    parser.add_argument("--out-json", default="reports/rerank_hitrate_comparison.json")
    args = parser.parse_args()

    dist_df = pd.read_csv(args.dist_csv)
    outliers_df = pd.read_csv(args.outliers_csv)
    proba_df = pd.read_csv(args.proba_csv)
    feat_df = pd.read_csv(args.features_csv)

    # join keys
    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    feat_df["lane"] = feat_df["lane"].astype(int)

    merged_df = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    merged_df["win_proba_norm"] = pd.to_numeric(merged_df["win_proba_norm"], errors="coerce").fillna(0.0)

    outlier_ids = set(outliers_df["race_id"].astype(str))
    normal_df = dist_df[~dist_df["race_id"].astype(str).isin(outlier_ids)].copy()
    normal_df["actual_winner"] = normal_df["actual_winner"].astype(str).str.strip()

    tiebreak_feats = ["national_win_rate", "local_2ren_rate"]
    for feat in tiebreak_feats:
        merged_df[feat] = pd.to_numeric(merged_df.get(feat), errors="coerce")
        col_min = float(merged_df[feat].min()) if merged_df[feat].notna().any() else 0.0
        col_max = float(merged_df[feat].max()) if merged_df[feat].notna().any() else 1.0
        merged_df[f"{feat}_scaled"] = (merged_df[feat] - col_min) / (col_max - col_min + 1e-9)
        merged_df[f"{feat}_scaled"] = merged_df[f"{feat}_scaled"].fillna(0.0)

    beta_grid = [0.0, 0.05, 0.10, 0.20, 0.30, 0.50]
    combos = {
        "baseline": [],
        "national_win_rate": ["national_win_rate"],
        "local_2ren_rate": ["local_2ren_rate"],
        "national_win_rate+local_2ren": ["national_win_rate", "local_2ren_rate"],
    }

    results: dict[str, dict[str, dict[str, float | int]]] = {}
    for combo_name, feats in combos.items():
        results[combo_name] = {}
        betas = beta_grid if feats else [0.0]

        for beta in betas:
            hits = 0
            total = 0
            for _, row in normal_df.iterrows():
                race_id = str(row["race_id"])
                actual_winner = str(row["actual_winner"])
                race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
                if race_data.empty:
                    continue

                race_data["rerank_score"] = race_data["win_proba_norm"]
                for feat in feats:
                    race_data["rerank_score"] = race_data["rerank_score"] + beta * race_data[f"{feat}_scaled"]

                top1_horse = str(race_data.sort_values("rerank_score", ascending=False).iloc[0]["lane"])
                if top1_horse == actual_winner:
                    hits += 1
                total += 1

            hitrate = float(hits / total) if total > 0 else 0.0
            results[combo_name][str(beta)] = {
                "hits": int(hits),
                "total": int(total),
                "hitrate": round(hitrate, 4),
            }

    summary = {"results": results, "best": {}}
    for combo_name, beta_dict in results.items():
        best_beta = max(beta_dict, key=lambda b: beta_dict[b]["hitrate"])
        summary["best"][combo_name] = {"best_beta": best_beta, **beta_dict[best_beta]}

    baseline_hitrate = summary["best"]["baseline"]["hitrate"]
    for combo_name, best in summary["best"].items():
        best["delta_vs_baseline"] = round(float(best["hitrate"] - baseline_hitrate), 4)

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"best": summary["best"]}, ensure_ascii=False, indent=2))
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
