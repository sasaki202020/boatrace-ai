import argparse
import itertools
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


def approx_prob_rank_of_trifecta(lane_probs: dict[int, float], trifecta: str) -> int | None:
    try:
        a, b, c = [int(x) for x in str(trifecta).split("-")]
    except Exception:
        return None
    lanes = list(lane_probs.keys())
    scored = []
    eps = 1e-12
    for p in itertools.permutations(lanes, 3):
        p1 = lane_probs[p[0]]
        remain1 = sum(lane_probs[x] for x in lanes if x != p[0])
        remain2 = sum(lane_probs[x] for x in lanes if x not in (p[0], p[1]))
        if p1 <= 0 or remain1 <= eps or remain2 <= eps:
            ap = 0.0
        else:
            ap = p1 * (lane_probs[p[1]] / remain1) * (lane_probs[p[2]] / remain2)
        scored.append((f"{p[0]}-{p[1]}-{p[2]}", ap))
    scored.sort(key=lambda x: x[1], reverse=True)
    target = f"{a}-{b}-{c}"
    for i, (tri, _) in enumerate(scored, start=1):
        if tri == target:
            return i
    return None


def lane_probs_for_race(g: pd.DataFrame, score_col: str) -> dict[int, float]:
    probs = g.set_index("lane")[score_col].to_dict()
    total = sum(float(v) for v in probs.values())
    if total <= 0:
        return {int(k): 0.0 for k in probs}
    return {int(k): float(v) / total for k, v in probs.items()}


def aggregate(rows: list[dict], group_col: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()
    grouped = []
    for gv, g in df.groupby(group_col):
        n = len(g)
        grouped.append(
            {
                group_col: gv,
                "races": int(n),
                "baseline_exact_hit_count": int(g["baseline_exact"].sum()),
                "baseline_exact_hit_rate": float(g["baseline_exact"].mean()),
                "conditional_exact_hit_count": int(g["conditional_exact"].sum()),
                "conditional_exact_hit_rate": float(g["conditional_exact"].mean()),
                "delta_exact_hit_rate": float(g["conditional_exact"].mean() - g["baseline_exact"].mean()),
                "baseline_top1_rate": float(g["baseline_top1"].mean()),
                "conditional_top1_rate": float(g["conditional_top1"].mean()),
                "delta_top1_rate": float(g["conditional_top1"].mean() - g["baseline_top1"].mean()),
                "baseline_mean_rank": float(g["baseline_rank"].mean()),
                "conditional_mean_rank": float(g["conditional_rank"].mean()),
                "delta_mean_rank": float(g["conditional_rank"].mean() - g["baseline_rank"].mean()),
            }
        )
    return pd.DataFrame(grouped).sort_values("races", ascending=False)


def main():
    parser = argparse.ArgumentParser(description="Breakdown of conditional beta effect by date/jcd.")
    parser.add_argument("--proba", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features", default="data/features/today_features.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-date-csv", default="reports/conditional_beta_breakdown_by_date.csv")
    parser.add_argument("--out-jcd-csv", default="reports/conditional_beta_breakdown_by_jcd.csv")
    parser.add_argument("--out-json", default="reports/conditional_beta_breakdown_summary.json")
    args = parser.parse_args()

    proba = pd.read_csv(args.proba)
    feat = pd.read_csv(args.features)
    bt = pd.read_csv(args.backtest)
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
    truth = bt.set_index("race_id")[["actual_trifecta", "actual_first_lane"]]

    keep_cols = ["race_id", "lane", "jcd", "national_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate"]
    feat_keep = [c for c in keep_cols if c in feat.columns]
    merged = proba.merge(feat[feat_keep], on=["race_id", "lane"], how="left")
    for c in ["win_proba_raw", "national_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate", "jcd"]:
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="coerce")
    merged = normalize_within_race(merged, "win_proba_raw", "base_score")

    for c in ["national_win_rate", "local_2ren_rate"]:
        cmin = float(merged[c].min()) if merged[c].notna().any() else 0.0
        cmax = float(merged[c].max()) if merged[c].notna().any() else 1.0
        merged[f"{c}_scaled"] = (merged[c] - cmin) / (cmax - cmin + 1e-9)
        merged[f"{c}_scaled"] = merged[f"{c}_scaled"].fillna(0.0)

    merged["low_condition"] = ((merged["motor_2ren_rate"] <= 25) | (merged["boat_2ren_rate"] <= 25)).astype(int)
    merged["score_baseline"] = merged["base_score"] + 0.2 * (merged["national_win_rate_scaled"] + merged["local_2ren_rate_scaled"])
    merged["score_conditional"] = merged["base_score"] + merged["low_condition"].map({1: 0.3, 0: 0.2}) * (
        merged["national_win_rate_scaled"] + merged["local_2ren_rate_scaled"]
    )

    race_rows = []
    for rid, g in merged.groupby("race_id"):
        key = str(rid)
        if key not in truth.index.astype(str):
            continue
        a = truth.loc[rid] if rid in truth.index else truth.loc[key]
        actual_tri = str(a["actual_trifecta"])
        actual_first = int(a["actual_first_lane"])

        b_ord = g.sort_values("score_baseline", ascending=False)
        c_ord = g.sort_values("score_conditional", ascending=False)
        b_top3 = b_ord["lane"].astype(int).tolist()[:3]
        c_top3 = c_ord["lane"].astype(int).tolist()[:3]
        if len(b_top3) < 3 or len(c_top3) < 3:
            continue

        b_tri = f"{b_top3[0]}-{b_top3[1]}-{b_top3[2]}"
        c_tri = f"{c_top3[0]}-{c_top3[1]}-{c_top3[2]}"
        b_rank = approx_prob_rank_of_trifecta(lane_probs_for_race(g, "score_baseline"), actual_tri)
        c_rank = approx_prob_rank_of_trifecta(lane_probs_for_race(g, "score_conditional"), actual_tri)

        race_rows.append(
            {
                "race_id": rid,
                "date": str(g["date"].iloc[0]) if "date" in g.columns else "NA",
                "jcd": str(int(g["jcd"].dropna().iloc[0])) if "jcd" in g.columns and g["jcd"].notna().any() else "NA",
                "baseline_exact": int(b_tri == actual_tri),
                "conditional_exact": int(c_tri == actual_tri),
                "baseline_top1": int(b_top3[0] == actual_first),
                "conditional_top1": int(c_top3[0] == actual_first),
                "baseline_rank": int(b_rank) if b_rank is not None else 999,
                "conditional_rank": int(c_rank) if c_rank is not None else 999,
            }
        )

    date_df = aggregate(race_rows, "date")
    jcd_df = aggregate(race_rows, "jcd")

    out_date = Path(args.out_date_csv)
    out_jcd = Path(args.out_jcd_csv)
    out_json = Path(args.out_json)
    out_date.parent.mkdir(parents=True, exist_ok=True)
    date_df.to_csv(out_date, index=False)
    jcd_df.to_csv(out_jcd, index=False)

    # bias check: contribution concentration by group
    total_delta_exact = int(pd.DataFrame(race_rows)["conditional_exact"].sum() - pd.DataFrame(race_rows)["baseline_exact"].sum()) if race_rows else 0
    top_date_contrib = None
    if not date_df.empty:
        date_df["delta_exact_count"] = date_df["conditional_exact_hit_count"] - date_df["baseline_exact_hit_count"]
        top = date_df.iloc[0]
        top_date_contrib = {
            "date": top["date"],
            "delta_exact_count": int(top["delta_exact_count"]),
            "share_of_total_delta_exact": float(int(top["delta_exact_count"]) / total_delta_exact) if total_delta_exact != 0 else None,
        }
    top_jcd_contrib = None
    if not jcd_df.empty:
        jcd_df["delta_exact_count"] = jcd_df["conditional_exact_hit_count"] - jcd_df["baseline_exact_hit_count"]
        top = jcd_df.iloc[0]
        top_jcd_contrib = {
            "jcd": top["jcd"],
            "delta_exact_count": int(top["delta_exact_count"]),
            "share_of_total_delta_exact": float(int(top["delta_exact_count"]) / total_delta_exact) if total_delta_exact != 0 else None,
        }

    summary = {
        "total_races": int(len(race_rows)),
        "overall_baseline_exact_hits": int(pd.DataFrame(race_rows)["baseline_exact"].sum()) if race_rows else 0,
        "overall_conditional_exact_hits": int(pd.DataFrame(race_rows)["conditional_exact"].sum()) if race_rows else 0,
        "overall_delta_exact_hits": int(total_delta_exact),
        "top_date_contribution": top_date_contrib,
        "top_jcd_contribution": top_jcd_contrib,
        "bias_flag": bool(
            (top_date_contrib and top_date_contrib.get("share_of_total_delta_exact") is not None and abs(top_date_contrib["share_of_total_delta_exact"]) > 0.8)
            or (top_jcd_contrib and top_jcd_contrib.get("share_of_total_delta_exact") is not None and abs(top_jcd_contrib["share_of_total_delta_exact"]) > 0.8)
        ),
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {out_date}")
    print(f"[saved] {out_jcd}")
    print(f"[saved] {out_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
