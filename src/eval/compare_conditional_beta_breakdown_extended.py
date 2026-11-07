import argparse
import itertools
import json
from pathlib import Path

import pandas as pd


def normalize_within_race(df: pd.DataFrame, col: str, out_col: str) -> pd.DataFrame:
    out = df.copy()
    out[out_col] = out.groupby("race_id")[col].transform(lambda x: x / x.sum() if x.sum() > 0 else 0.0)
    return out


def build_truth_from_historical(hist: pd.DataFrame) -> pd.DataFrame:
    h = hist.copy()
    h["lane"] = pd.to_numeric(h["lane"], errors="coerce")
    h["finish_position"] = pd.to_numeric(h["finish_position"], errors="coerce")
    h = h.dropna(subset=["race_id", "lane", "finish_position"]).copy()
    h["lane"] = h["lane"].astype(int)
    h["finish_position"] = h["finish_position"].astype(int)

    rows = []
    for rid, g in h.groupby("race_id"):
        top = g[g["finish_position"].isin([1, 2, 3])]
        if top["finish_position"].nunique() < 3:
            continue
        if top["lane"].nunique() < 3:
            continue
        top = top.sort_values("finish_position")
        tri = "-".join(top["lane"].astype(int).astype(str).tolist()[:3])
        rows.append(
            {
                "race_id": rid,
                "actual_trifecta": tri,
                "actual_first_lane": int(top.iloc[0]["lane"]),
                "date": str(g["date"].iloc[0]) if "date" in g.columns else "NA",
                "jcd": str(int(float(g["jcd"].dropna().iloc[0]))) if "jcd" in g.columns and g["jcd"].notna().any() else "NA",
            }
        )
    return pd.DataFrame(rows)


def lane_probs_for_race(g: pd.DataFrame, score_col: str) -> dict[int, float]:
    probs = g.set_index("lane")[score_col].to_dict()
    total = sum(float(v) for v in probs.values())
    if total <= 0:
        return {int(k): 0.0 for k in probs}
    return {int(k): float(v) / total for k, v in probs.items()}


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
        r1 = sum(lane_probs[x] for x in lanes if x != p[0])
        r2 = sum(lane_probs[x] for x in lanes if x not in (p[0], p[1]))
        if p1 <= 0 or r1 <= eps or r2 <= eps:
            ap = 0.0
        else:
            ap = p1 * (lane_probs[p[1]] / r1) * (lane_probs[p[2]] / r2)
        scored.append((f"{p[0]}-{p[1]}-{p[2]}", ap))
    scored.sort(key=lambda x: x[1], reverse=True)
    for i, (tri, _) in enumerate(scored, start=1):
        if tri == f"{a}-{b}-{c}":
            return i
    return None


def aggregate(rows: list[dict], group_col: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()
    out = []
    for gv, g in df.groupby(group_col):
        out.append(
            {
                group_col: gv,
                "races": int(len(g)),
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
    return pd.DataFrame(out).sort_values("races", ascending=False)


def main():
    parser = argparse.ArgumentParser(description="Extended-period breakdown for conditional beta(0.3 in low motor/boat).")
    parser.add_argument("--proba", default="data/model_outputs/train_win_proba.csv")
    parser.add_argument("--features", default="data/features/train_features.csv")
    parser.add_argument("--historical", default="data/processed/historical_races.csv")
    parser.add_argument("--out-date-csv", default="reports/conditional_beta_breakdown_by_date_extended.csv")
    parser.add_argument("--out-jcd-csv", default="reports/conditional_beta_breakdown_by_jcd_extended.csv")
    parser.add_argument("--out-json", default="reports/conditional_beta_breakdown_summary_extended.json")
    args = parser.parse_args()

    proba = pd.read_csv(args.proba)
    feat = pd.read_csv(args.features)
    hist = pd.read_csv(args.historical)

    truth = build_truth_from_historical(hist)
    truth_ids = set(truth["race_id"].astype(str))

    for df in (proba, feat):
        df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
    proba = proba.dropna(subset=["race_id", "lane"]).copy()
    feat = feat.dropna(subset=["race_id", "lane"]).copy()
    proba["lane"] = proba["lane"].astype(int)
    feat["lane"] = feat["lane"].astype(int)
    proba = proba[proba["race_id"].astype(str).isin(truth_ids)].copy()
    feat = feat[feat["race_id"].astype(str).isin(truth_ids)].copy()

    merged = proba.merge(
        feat[["race_id", "lane", "jcd", "national_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate", "date"]],
        on=["race_id", "lane"],
        how="left",
    )
    for c in ["win_proba_raw", "jcd", "national_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate"]:
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

    tmap = truth.set_index("race_id")[["actual_trifecta", "actual_first_lane", "date", "jcd"]]
    race_rows = []
    for rid, g in merged.groupby("race_id"):
        if rid not in tmap.index:
            continue
        a = tmap.loc[rid]
        actual_tri = str(a["actual_trifecta"])
        actual_first = int(a["actual_first_lane"])
        date = str(a["date"])
        jcd = str(a["jcd"])

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
                "date": date,
                "jcd": jcd,
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

    all_df = pd.DataFrame(race_rows)
    total_delta_exact = int(all_df["conditional_exact"].sum() - all_df["baseline_exact"].sum()) if not all_df.empty else 0
    pos_date = int((date_df["delta_exact_hit_rate"] > 0).sum()) if not date_df.empty else 0
    neg_date = int((date_df["delta_exact_hit_rate"] < 0).sum()) if not date_df.empty else 0
    pos_jcd = int((jcd_df["delta_exact_hit_rate"] > 0).sum()) if not jcd_df.empty else 0
    neg_jcd = int((jcd_df["delta_exact_hit_rate"] < 0).sum()) if not jcd_df.empty else 0

    summary = {
        "total_races": int(len(all_df)),
        "dates": int(all_df["date"].nunique()) if not all_df.empty else 0,
        "jcds": int(all_df["jcd"].nunique()) if not all_df.empty else 0,
        "overall_baseline_exact_hits": int(all_df["baseline_exact"].sum()) if not all_df.empty else 0,
        "overall_conditional_exact_hits": int(all_df["conditional_exact"].sum()) if not all_df.empty else 0,
        "overall_delta_exact_hits": int(total_delta_exact),
        "date_level_positive_groups": pos_date,
        "date_level_negative_groups": neg_date,
        "jcd_level_positive_groups": pos_jcd,
        "jcd_level_negative_groups": neg_jcd,
        "reproducibility_flag": bool(pos_date > 1 or pos_jcd > 1),
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {out_date}")
    print(f"[saved] {out_jcd}")
    print(f"[saved] {out_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
