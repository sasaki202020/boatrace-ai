import argparse
import json
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Apply rerank block to today_win_proba and verify top1 changes.")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features-csv", default="data/features/today_features.csv")
    parser.add_argument("--out-proba", default="data/model_outputs/today_win_proba_reranked.csv")
    parser.add_argument("--out-json", default="reports/rerank_patch_verification.json")
    parser.add_argument("--beta", type=float, default=0.2)
    args = parser.parse_args()

    tiebreak_feats = ["national_win_rate", "local_2ren_rate"]

    proba_df = pd.read_csv(args.proba_csv)
    feat_df = pd.read_csv(args.features_csv)
    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    feat_df["lane"] = feat_df["lane"].astype(int)

    merged_df = proba_df.merge(feat_df[["race_id", "lane"] + tiebreak_feats], on=["race_id", "lane"], how="left")
    merged_df["win_proba_norm"] = pd.to_numeric(merged_df["win_proba_norm"], errors="coerce").fillna(0.0)

    for feat in tiebreak_feats:
        merged_df[feat] = pd.to_numeric(merged_df[feat], errors="coerce")
        fmin = float(merged_df[feat].min()) if merged_df[feat].notna().any() else 0.0
        fmax = float(merged_df[feat].max()) if merged_df[feat].notna().any() else 1.0
        merged_df[f"{feat}_scaled"] = (merged_df[feat] - fmin) / (fmax - fmin + 1e-9)
        merged_df[f"{feat}_scaled"] = merged_df[f"{feat}_scaled"].fillna(0.0)

    merged_df["rerank_score"] = merged_df["win_proba_norm"].copy()
    for feat in tiebreak_feats:
        merged_df["rerank_score"] = merged_df["rerank_score"] + args.beta * merged_df[f"{feat}_scaled"]

    before_top1 = (
        merged_df.sort_values("win_proba_norm", ascending=False)
        .groupby("race_id")
        .first()["lane"]
        .rename("top1_before")
    )
    after_top1 = (
        merged_df.sort_values("rerank_score", ascending=False)
        .groupby("race_id")
        .first()["lane"]
        .rename("top1_after")
    )
    comparison = pd.concat([before_top1, after_top1], axis=1)
    changed = int((comparison["top1_before"] != comparison["top1_after"]).sum())
    total = int(len(comparison))

    out_df = proba_df.copy()
    rerank_series = merged_df.groupby("race_id")["rerank_score"].transform(
        lambda x: x / x.sum() if x.sum() > 0 else 0.0
    )
    out_df["win_proba_norm"] = rerank_series.values

    out_proba = Path(args.out_proba)
    out_json = Path(args.out_json)
    out_proba.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_proba, index=False)

    verification = {
        "beta": args.beta,
        "tiebreak_feats": tiebreak_feats,
        "total_races": total,
        "top1_changed": changed,
        "top1_unchanged": total - changed,
        "change_rate": round(float(changed / total), 4) if total else 0.0,
    }
    out_json.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    print(f"[saved] {out_proba}")
    print(f"[saved] {out_json}")


if __name__ == "__main__":
    main()
