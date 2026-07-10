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
        if top["finish_position"].nunique() < 3 or top["lane"].nunique() < 3:
            continue
        top = top.sort_values("finish_position")
        rows.append(
            {
                "race_id": rid,
                "actual_trifecta": "-".join(top["lane"].astype(str).tolist()[:3]),
                "actual_first_lane": int(top.iloc[0]["lane"]),
                "date": str(g["date"].iloc[0]) if "date" in g.columns else "NA",
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


def pack(rows: list[dict], key: str) -> dict:
    df = pd.DataFrame(rows)
    n = len(df)
    if n == 0:
        return {f"{key}_count": 0, f"{key}_rate": 0.0}
    return {f"{key}_count": int(df[key].sum()), f"{key}_rate": float(df[key].mean())}


def main():
    parser = argparse.ArgumentParser(description="Compare fixed beta vs conditional beta on recent N days.")
    parser.add_argument("--proba", default="data/model_outputs/train_win_proba.csv")
    parser.add_argument("--features", default="data/features/train_features.csv")
    parser.add_argument("--historical", default="data/processed/historical_races.csv")
    parser.add_argument("--recent-n-days", type=int, default=4)
    parser.add_argument("--out-json", default="reports/conditional_beta_recent_window.json")
    args = parser.parse_args()

    proba = pd.read_csv(args.proba)
    feat = pd.read_csv(args.features)
    hist = pd.read_csv(args.historical)
    truth = build_truth_from_historical(hist)
    truth["date"] = pd.to_datetime(truth["date"], errors="coerce")
    recent_dates = sorted([d for d in truth["date"].dropna().unique()])[-args.recent_n_days :]
    truth = truth[truth["date"].isin(recent_dates)].copy()
    truth["date"] = truth["date"].dt.strftime("%Y-%m-%d")

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
        feat[["race_id", "lane", "national_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate"]],
        on=["race_id", "lane"],
        how="left",
    )
    for c in ["win_proba_raw", "national_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate"]:
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

    tmap = truth.set_index("race_id")[["actual_trifecta", "actual_first_lane", "date"]]
    rows = []
    for rid, g in merged.groupby("race_id"):
        if rid not in tmap.index:
            continue
        a = tmap.loc[rid]
        actual_tri = str(a["actual_trifecta"])
        actual_first = int(a["actual_first_lane"])
        b = g.sort_values("score_baseline", ascending=False)["lane"].astype(int).tolist()[:3]
        c = g.sort_values("score_conditional", ascending=False)["lane"].astype(int).tolist()[:3]
        if len(b) < 3 or len(c) < 3:
            continue
        rows.append(
            {
                "race_id": rid,
                "date": str(a["date"]),
                "baseline_exact": int(f"{b[0]}-{b[1]}-{b[2]}" == actual_tri),
                "conditional_exact": int(f"{c[0]}-{c[1]}-{c[2]}" == actual_tri),
                "baseline_top1": int(b[0] == actual_first),
                "conditional_top1": int(c[0] == actual_first),
                "baseline_rank": approx_prob_rank_of_trifecta(lane_probs_for_race(g, "score_baseline"), actual_tri),
                "conditional_rank": approx_prob_rank_of_trifecta(lane_probs_for_race(g, "score_conditional"), actual_tri),
            }
        )

    df = pd.DataFrame(rows)
    n = len(df)
    result = {
        "recent_n_days": args.recent_n_days,
        "recent_dates": [str(pd.Timestamp(d).date()) for d in recent_dates],
        "total_races": int(n),
        "baseline": {
            "exact_hit_count": int(df["baseline_exact"].sum()) if n else 0,
            "exact_hit_rate": round(float(df["baseline_exact"].mean()), 4) if n else 0.0,
            "top1_rate": round(float(df["baseline_top1"].mean()), 4) if n else 0.0,
            "candidate_mean_rank": round(float(pd.to_numeric(df["baseline_rank"], errors="coerce").mean()), 4) if n else None,
        },
        "conditional": {
            "exact_hit_count": int(df["conditional_exact"].sum()) if n else 0,
            "exact_hit_rate": round(float(df["conditional_exact"].mean()), 4) if n else 0.0,
            "top1_rate": round(float(df["conditional_top1"].mean()), 4) if n else 0.0,
            "candidate_mean_rank": round(float(pd.to_numeric(df["conditional_rank"], errors="coerce").mean()), 4) if n else None,
        },
        "delta": {
            "exact_hit_rate": round(float(df["conditional_exact"].mean() - df["baseline_exact"].mean()), 4) if n else 0.0,
            "top1_rate": round(float(df["conditional_top1"].mean() - df["baseline_top1"].mean()), 4) if n else 0.0,
            "candidate_mean_rank": round(
                float(pd.to_numeric(df["conditional_rank"], errors="coerce").mean() - pd.to_numeric(df["baseline_rank"], errors="coerce").mean()),
                4,
            ) if n else None,
        },
        "reproducible_improvement": bool(
            n > 0
            and (df["conditional_exact"].mean() > df["baseline_exact"].mean())
            and (df["conditional_top1"].mean() >= df["baseline_top1"].mean())
        ),
    }

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
