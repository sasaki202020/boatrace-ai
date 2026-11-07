import argparse
import itertools
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def generate_candidates_for_race(group: pd.DataFrame, top_n_win: int, max_combos: int) -> pd.DataFrame:
    work = group.copy()
    work["win_proba_norm"] = pd.to_numeric(work["win_proba_norm"], errors="coerce").fillna(0.0)
    total_prob = work["win_proba_norm"].sum()
    if total_prob <= 0:
        return pd.DataFrame(columns=["race_id", "trifecta", "approx_prob"])

    work["win_proba_norm"] = work["win_proba_norm"] / total_prob
    lanes = work["lane"].tolist()
    probs = work.set_index("lane")["win_proba_norm"].to_dict()
    top_boats = work.sort_values("win_proba_norm", ascending=False).head(top_n_win)["lane"].tolist()

    eps = 1e-10
    rows = []
    for c in itertools.permutations(lanes, 3):
        if c[0] not in top_boats:
            continue
        p1 = probs[c[0]]
        remain_after_first = sum(probs[l] for l in lanes if l != c[0])
        remain_after_second = sum(probs[l] for l in lanes if l not in (c[0], c[1]))
        if p1 <= 0 or remain_after_first <= eps or remain_after_second <= eps:
            continue
        p2 = probs[c[1]] / remain_after_first
        p3 = probs[c[2]] / remain_after_second
        approx_prob = min(p1 * p2 * p3, 1.0)
        if approx_prob <= 0:
            continue
        rows.append({"race_id": work["race_id"].iloc[0], "trifecta": f"{c[0]}-{c[1]}-{c[2]}", "approx_prob": approx_prob})

    rows = sorted(rows, key=lambda x: x["approx_prob"], reverse=True)[:max_combos]
    out = pd.DataFrame(rows)
    if not out.empty:
        out["rank"] = out.index + 1
    return out


def main():
    parser = argparse.ArgumentParser(description="Rank distribution of actual trifecta within candidate set")
    parser.add_argument("--today-win", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--top-n-win", type=int, default=4)
    parser.add_argument("--max-combos", type=int, default=30)
    parser.add_argument("--out-hit-ranks", default="reports/candidate_rank_hits_top4_max30.csv")
    parser.add_argument("--out-rank-dist", default="reports/candidate_rank_distribution_top4_max30.csv")
    parser.add_argument("--out-summary", default="reports/candidate_rank_distribution_top4_max30_summary.json")
    args = parser.parse_args()

    win = pd.read_csv(args.today_win)
    bt = pd.read_csv(args.backtest)
    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()

    race_ids = set(bt["race_id"].astype(str))
    win = win[win["race_id"].astype(str).isin(race_ids)].copy()
    win["lane"] = pd.to_numeric(win["lane"], errors="coerce")
    win["win_proba_norm"] = pd.to_numeric(win["win_proba_norm"], errors="coerce")
    win = win.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    all_candidates = []
    for _, g in win.groupby("race_id"):
        cand = generate_candidates_for_race(g, top_n_win=args.top_n_win, max_combos=args.max_combos)
        if not cand.empty:
            all_candidates.append(cand)
    if all_candidates:
        candidates = pd.concat(all_candidates, ignore_index=True)
    else:
        candidates = pd.DataFrame(columns=["race_id", "trifecta", "approx_prob", "rank"])

    merged = bt.merge(candidates, on="race_id", how="left")
    merged["is_actual_hit_row"] = merged["trifecta"].astype(str) == merged["actual_trifecta"].astype(str)
    hit_rows = merged[merged["is_actual_hit_row"]].copy()
    hit_rows = hit_rows[["race_id", "actual_trifecta", "rank", "approx_prob"]].rename(
        columns={"approx_prob": "actual_trifecta_approx_prob"}
    )
    hit_rows["rank"] = pd.to_numeric(hit_rows["rank"], errors="coerce").astype("Int64")

    races_total = int(bt["race_id"].nunique())
    hit_races = int(hit_rows["race_id"].nunique())
    miss_races = races_total - hit_races

    top5 = int((hit_rows["rank"] <= 5).sum())
    top10 = int((hit_rows["rank"] <= 10).sum())
    top20 = int((hit_rows["rank"] <= 20).sum())
    top30 = int((hit_rows["rank"] <= 30).sum())

    rank_dist = (
        hit_rows["rank"]
        .value_counts()
        .sort_index()
        .rename_axis("rank")
        .reset_index(name="count")
    )

    in_set_not_top10 = int(((hit_rows["rank"] > 10) & (hit_rows["rank"] <= 30)).sum())
    in_set_not_top10_rate_within_hits = float(in_set_not_top10 / hit_races) if hit_races else None
    in_set_not_top10_rate_all_races = float(in_set_not_top10 / races_total) if races_total else None

    out_hit = Path(args.out_hit_ranks)
    out_dist = Path(args.out_rank_dist)
    out_sum = Path(args.out_summary)
    for p in [out_hit, out_dist, out_sum]:
        p.parent.mkdir(parents=True, exist_ok=True)

    hit_rows.sort_values("rank").to_csv(out_hit, index=False)
    rank_dist.to_csv(out_dist, index=False)

    summary = {
        "config": {"top_n_win": args.top_n_win, "max_combos": args.max_combos},
        "races_total": races_total,
        "actual_in_candidates_races": hit_races,
        "actual_not_in_candidates_races": miss_races,
        "actual_in_candidates_rate": float(hit_races / races_total) if races_total else None,
        "top5_count": top5,
        "top10_count": top10,
        "top20_count": top20,
        "top30_count": top30,
        "top5_rate_all_races": float(top5 / races_total) if races_total else None,
        "top10_rate_all_races": float(top10 / races_total) if races_total else None,
        "top20_rate_all_races": float(top20 / races_total) if races_total else None,
        "top30_rate_all_races": float(top30 / races_total) if races_total else None,
        "in_set_but_not_top10_count": in_set_not_top10,
        "in_set_but_not_top10_rate_within_in_set": in_set_not_top10_rate_within_hits,
        "in_set_but_not_top10_rate_all_races": in_set_not_top10_rate_all_races,
    }
    out_sum.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_hit}")
    print(f"saved: {out_dist}")
    print(f"saved: {out_sum}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
