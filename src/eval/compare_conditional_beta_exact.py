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


def build_lane_probs(g: pd.DataFrame, score_col: str) -> dict[int, float]:
    lanes = g["lane"].astype(int).tolist()
    probs = g.set_index("lane")[score_col].to_dict()
    total = sum(float(probs[l]) for l in lanes)
    if total <= 0:
        return {int(l): 0.0 for l in lanes}
    return {int(l): float(probs[l]) / total for l in lanes}


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
    for i, (tri, _) in enumerate(scored, start=1):
        if tri == f"{a}-{b}-{c}":
            return i
    return None


def main():
    parser = argparse.ArgumentParser(description="Compare baseline beta=0.2 vs conditional beta=0.3 on exact-hit KPIs.")
    parser.add_argument("--proba", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features", default="data/features/today_features.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-json", default="reports/conditional_beta_exact_comparison.json")
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

    merged = proba.merge(
        feat[["race_id", "lane", "motor_2ren_rate", "boat_2ren_rate", "national_win_rate", "local_2ren_rate"]],
        on=["race_id", "lane"],
        how="left",
    )
    for c in ["win_proba_raw", "motor_2ren_rate", "boat_2ren_rate", "national_win_rate", "local_2ren_rate"]:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")

    merged = normalize_within_race(merged, "win_proba_raw", "base_score")
    for c in ["national_win_rate", "local_2ren_rate"]:
        cmin = float(merged[c].min()) if merged[c].notna().any() else 0.0
        cmax = float(merged[c].max()) if merged[c].notna().any() else 1.0
        merged[f"{c}_scaled"] = (merged[c] - cmin) / (cmax - cmin + 1e-9)
        merged[f"{c}_scaled"] = merged[f"{c}_scaled"].fillna(0.0)

    merged["low_condition"] = ((merged["motor_2ren_rate"] <= 25) | (merged["boat_2ren_rate"] <= 25)).astype(int)
    merged["score_baseline"] = merged["base_score"] + 0.2 * (
        merged["national_win_rate_scaled"] + merged["local_2ren_rate_scaled"]
    )
    merged["score_conditional"] = merged["base_score"] + merged["low_condition"].map({1: 0.3, 0: 0.2}) * (
        merged["national_win_rate_scaled"] + merged["local_2ren_rate_scaled"]
    )

    stats = {
        "baseline": {"top1_hits": 0, "exact_hits": 0, "ranks": [], "total": 0},
        "conditional": {"top1_hits": 0, "exact_hits": 0, "ranks": [], "total": 0},
        "subset_low_condition": {
            "target_races": 0,
            "baseline_exact_hits": 0,
            "conditional_exact_hits": 0,
            "baseline_top1_hits": 0,
            "conditional_top1_hits": 0,
        },
    }

    for rid, g in merged.groupby("race_id"):
        key = str(rid)
        if key not in truth.index.astype(str):
            continue
        actual = truth.loc[rid] if rid in truth.index else truth.loc[key]
        actual_tri = str(actual["actual_trifecta"])
        actual_first = int(actual["actual_first_lane"])

        # subset condition by actual winner lane
        winner_row = g[g["lane"].astype(int) == actual_first]
        is_low = False
        if not winner_row.empty:
            wr = winner_row.iloc[0]
            is_low = bool((pd.notna(wr["motor_2ren_rate"]) and wr["motor_2ren_rate"] <= 25) or (pd.notna(wr["boat_2ren_rate"]) and wr["boat_2ren_rate"] <= 25))

        for name, score_col in [("baseline", "score_baseline"), ("conditional", "score_conditional")]:
            ordered = g.sort_values(score_col, ascending=False)
            top3 = ordered["lane"].astype(int).tolist()[:3]
            if len(top3) < 3:
                continue
            stats[name]["total"] += 1
            stats[name]["top1_hits"] += int(top3[0] == actual_first)
            tri = f"{top3[0]}-{top3[1]}-{top3[2]}"
            stats[name]["exact_hits"] += int(tri == actual_tri)
            rank = approx_prob_rank_of_trifecta(build_lane_probs(g, score_col), actual_tri)
            if rank is not None:
                stats[name]["ranks"].append(rank)

            if is_low:
                stats["subset_low_condition"]["target_races"] += 1 if name == "baseline" else 0
                if name == "baseline":
                    stats["subset_low_condition"]["baseline_exact_hits"] += int(tri == actual_tri)
                    stats["subset_low_condition"]["baseline_top1_hits"] += int(top3[0] == actual_first)
                else:
                    stats["subset_low_condition"]["conditional_exact_hits"] += int(tri == actual_tri)
                    stats["subset_low_condition"]["conditional_top1_hits"] += int(top3[0] == actual_first)

    def pack(name: str):
        s = stats[name]
        t = s["total"] or 1
        return {
            "total_races": int(s["total"]),
            "top1_exact_hit_count": int(s["top1_hits"]),
            "top1_exact_hit_rate": round(float(s["top1_hits"] / t), 4),
            "exact_hit_count": int(s["exact_hits"]),
            "exact_hit_rate": round(float(s["exact_hits"] / t), 4),
            "candidate_mean_rank": round(float(pd.Series(s["ranks"]).mean()), 4) if s["ranks"] else None,
            "candidate_median_rank": round(float(pd.Series(s["ranks"]).median()), 4) if s["ranks"] else None,
        }

    subset = stats["subset_low_condition"]
    st = subset["target_races"] or 1
    result = {
        "condition_rule": "if motor_2ren_rate<=25 or boat_2ren_rate<=25 then beta=0.3 else beta=0.2",
        "baseline_beta_0_2": pack("baseline"),
        "conditional_beta": pack("conditional"),
        "delta": {
            "top1_exact_hit_rate": round(pack("conditional")["top1_exact_hit_rate"] - pack("baseline")["top1_exact_hit_rate"], 4),
            "exact_hit_rate": round(pack("conditional")["exact_hit_rate"] - pack("baseline")["exact_hit_rate"], 4),
            "candidate_mean_rank": round(
                (pack("conditional")["candidate_mean_rank"] or 0) - (pack("baseline")["candidate_mean_rank"] or 0), 4
            ) if pack("baseline")["candidate_mean_rank"] is not None and pack("conditional")["candidate_mean_rank"] is not None else None,
        },
        "subset_low_condition_112": {
            "target_races": int(subset["target_races"]),
            "baseline_exact_hit_count": int(subset["baseline_exact_hits"]),
            "conditional_exact_hit_count": int(subset["conditional_exact_hits"]),
            "baseline_exact_hit_rate": round(float(subset["baseline_exact_hits"] / st), 4),
            "conditional_exact_hit_rate": round(float(subset["conditional_exact_hits"] / st), 4),
            "delta_exact_hit_rate": round(float((subset["conditional_exact_hits"] - subset["baseline_exact_hits"]) / st), 4),
            "baseline_top1_hit_rate": round(float(subset["baseline_top1_hits"] / st), 4),
            "conditional_top1_hit_rate": round(float(subset["conditional_top1_hits"] / st), 4),
            "delta_top1_hit_rate": round(float((subset["conditional_top1_hits"] - subset["baseline_top1_hits"]) / st), 4),
        },
    }

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
