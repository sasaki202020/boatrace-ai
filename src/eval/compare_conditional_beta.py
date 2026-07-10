import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def normalize_within_race(df: pd.DataFrame, col: str, out_col: str) -> pd.DataFrame:
    out = df.copy()
    out[out_col] = out.groupby("race_id")[col].transform(lambda x: x / x.sum() if x.sum() > 0 else 0.0)
    return out


def calc_topk_rates(scored: pd.DataFrame, truth: pd.DataFrame, score_col: str) -> dict:
    rows = []
    for rid, g in scored.groupby("race_id"):
        a = truth[truth["race_id"].astype(str) == str(rid)]
        if a.empty:
            continue
        actual_first = int(a.iloc[0]["actual_first_lane"])
        lanes = g.sort_values(score_col, ascending=False)["lane"].astype(int).tolist()
        if len(lanes) < 3:
            continue
        rows.append(
            {
                "race_id": rid,
                "top1": int(lanes[0] == actual_first),
                "top2": int(actual_first in lanes[:2]),
                "top3": int(actual_first in lanes[:3]),
                "pred_top1_lane": lanes[0],
            }
        )
    df = pd.DataFrame(rows)
    return {
        "races": int(len(df)),
        "top1_rate": float(df["top1"].mean()) if len(df) else 0.0,
        "top2_rate": float(df["top2"].mean()) if len(df) else 0.0,
        "top3_rate": float(df["top3"].mean()) if len(df) else 0.0,
        "detail": df,
    }


def outlier_rank_stats(scored: pd.DataFrame, outliers: pd.DataFrame, score_col: str) -> dict:
    ranks = []
    for _, r in outliers.iterrows():
        rid = str(r["race_id"])
        aw = str(r["actual_winner"]).strip()
        g = scored[scored["race_id"].astype(str) == rid].sort_values(score_col, ascending=False).reset_index(drop=True)
        if g.empty:
            continue
        g["lane_s"] = g["lane"].astype(int).astype(str)
        m = g[g["lane_s"] == aw]
        if m.empty:
            continue
        ranks.append(int(m.index[0]) + 1)
    s = pd.Series(ranks)
    if s.empty:
        return {"count": 0, "mean_rank": None, "median_rank": None, "dist": {}}
    return {
        "count": int(len(s)),
        "mean_rank": float(s.mean()),
        "median_rank": float(s.median()),
        "dist": {str(int(k)): int(v) for k, v in s.value_counts().sort_index().items()},
    }


def subset_improvement(scored_fixed: pd.DataFrame, scored_cond: pd.DataFrame, truth: pd.DataFrame) -> dict:
    # condition-matched races: actual winner lane satisfies low motor/boat condition
    merged = truth.merge(
        scored_fixed[["race_id", "lane", "low_condition", "score_fixed"]].rename(columns={"score_fixed": "sf"}),
        left_on=["race_id", "actual_first_lane"],
        right_on=["race_id", "lane"],
        how="left",
    ).drop(columns=["lane"])
    target_races = set(merged[merged["low_condition"] == 1]["race_id"].astype(str))

    def hit_count(scored: pd.DataFrame, score_col: str) -> tuple[int, int]:
        h = 0
        t = 0
        for rid, g in scored.groupby("race_id"):
            if str(rid) not in target_races:
                continue
            a = truth[truth["race_id"].astype(str) == str(rid)]
            if a.empty:
                continue
            top1 = int(g.sort_values(score_col, ascending=False).iloc[0]["lane"])
            h += int(top1 == int(a.iloc[0]["actual_first_lane"]))
            t += 1
        return h, t

    hf, tf = hit_count(scored_fixed, "score_fixed")
    hc, tc = hit_count(scored_cond, "score_cond")
    return {
        "target_races": int(len(target_races)),
        "fixed_beta_top1_hits": int(hf),
        "fixed_beta_top1_rate": float(hf / tf) if tf else 0.0,
        "conditional_beta_top1_hits": int(hc),
        "conditional_beta_top1_rate": float(hc / tc) if tc else 0.0,
        "delta_top1_rate": float((hc / tc) - (hf / tf)) if tf and tc else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="Offline compare fixed beta vs conditional beta on low motor/boat.")
    parser.add_argument("--proba", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features", default="data/features/today_features.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--outliers", default="reports/calibration_outliers.csv")
    parser.add_argument("--metrics", default="data/model_outputs/test_metrics.json")
    parser.add_argument("--out-json", default="reports/conditional_beta_comparison.json")
    parser.add_argument("--base-beta", type=float, default=0.2)
    parser.add_argument("--condition-beta", type=float, default=0.1)
    args = parser.parse_args()

    proba = pd.read_csv(args.proba)
    feat = pd.read_csv(args.features)
    bt = pd.read_csv(args.backtest)
    outliers = pd.read_csv(args.outliers)
    metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))

    for df in (proba, feat):
        df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
    proba = proba.dropna(subset=["race_id", "lane"]).copy()
    feat = feat.dropna(subset=["race_id", "lane"]).copy()
    proba["lane"] = proba["lane"].astype(int)
    feat["lane"] = feat["lane"].astype(int)

    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    bt["actual_first_lane"] = pd.to_numeric(bt["actual_trifecta"].astype(str).str.split("-").str[0], errors="coerce")
    bt = bt.dropna(subset=["actual_first_lane"]).copy()
    bt["actual_first_lane"] = bt["actual_first_lane"].astype(int)

    merged = proba.merge(feat[["race_id", "lane", "motor_2ren_rate", "boat_2ren_rate", "national_win_rate", "local_2ren_rate"]], on=["race_id", "lane"], how="left")
    for c in ["win_proba_raw", "motor_2ren_rate", "boat_2ren_rate", "national_win_rate", "local_2ren_rate"]:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")

    # start from raw normalized score to avoid depending on already-reranked win_proba_norm
    merged = normalize_within_race(merged, "win_proba_raw", "base_score")

    for feat_col in ["national_win_rate", "local_2ren_rate"]:
        fmin = float(merged[feat_col].min()) if merged[feat_col].notna().any() else 0.0
        fmax = float(merged[feat_col].max()) if merged[feat_col].notna().any() else 1.0
        merged[f"{feat_col}_scaled"] = (merged[feat_col] - fmin) / (fmax - fmin + 1e-9)
        merged[f"{feat_col}_scaled"] = merged[f"{feat_col}_scaled"].fillna(0.0)

    merged["low_condition"] = (
        (merged["motor_2ren_rate"] <= 25) | (merged["boat_2ren_rate"] <= 25)
    ).astype(int)
    merged["beta_cond"] = merged["low_condition"].map({1: args.condition_beta, 0: args.base_beta})

    merged["score_fixed"] = merged["base_score"] + args.base_beta * (
        merged["national_win_rate_scaled"] + merged["local_2ren_rate_scaled"]
    )
    merged["score_cond"] = merged["base_score"] + merged["beta_cond"] * (
        merged["national_win_rate_scaled"] + merged["local_2ren_rate_scaled"]
    )

    fixed_topk = calc_topk_rates(merged, bt, "score_fixed")
    cond_topk = calc_topk_rates(merged, bt, "score_cond")
    out_fixed = outlier_rank_stats(merged, outliers, "score_fixed")
    out_cond = outlier_rank_stats(merged, outliers, "score_cond")
    subset = subset_improvement(merged, merged, bt)

    result = {
        "condition_rule": f"if motor_2ren_rate<=25 or boat_2ren_rate<=25 then beta={args.condition_beta} else beta={args.base_beta}",
        "topk_fixed_beta_0_2": {
            "top1_rate": round(fixed_topk["top1_rate"], 4),
            "top2_rate": round(fixed_topk["top2_rate"], 4),
            "top3_rate": round(fixed_topk["top3_rate"], 4),
            "races": fixed_topk["races"],
            "beta": args.base_beta,
        },
        "topk_conditional_beta": {
            "top1_rate": round(cond_topk["top1_rate"], 4),
            "top2_rate": round(cond_topk["top2_rate"], 4),
            "top3_rate": round(cond_topk["top3_rate"], 4),
            "races": cond_topk["races"],
            "base_beta": args.base_beta,
            "condition_beta": args.condition_beta,
        },
        "delta_topk": {
            "top1_rate": round(cond_topk["top1_rate"] - fixed_topk["top1_rate"], 4),
            "top2_rate": round(cond_topk["top2_rate"] - fixed_topk["top2_rate"], 4),
            "top3_rate": round(cond_topk["top3_rate"] - fixed_topk["top3_rate"], 4),
        },
        "outlier27_rank_fixed_beta_0_2": out_fixed,
        "outlier27_rank_conditional_beta": out_cond,
        "outlier27_rank_delta": {
            "mean_rank": round((out_cond["mean_rank"] or 0) - (out_fixed["mean_rank"] or 0), 4) if out_fixed["count"] and out_cond["count"] else None,
            "median_rank": round((out_cond["median_rank"] or 0) - (out_fixed["median_rank"] or 0), 4) if out_fixed["count"] and out_cond["count"] else None,
        },
        "condition_matched_race_improvement": subset,
        "test_metrics_reference": {
            "log_loss": metrics.get("log_loss"),
            "accuracy": metrics.get("accuracy"),
            "auc_roc": metrics.get("auc_roc"),
            "note": "No retraining in this analysis; test_metrics unchanged.",
        },
    }

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
