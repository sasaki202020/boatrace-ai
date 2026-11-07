import argparse
import itertools
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def build_full_candidates_top4(group: pd.DataFrame, top_n_win: int = 4) -> tuple[pd.DataFrame, list[int]]:
    work = group.copy()
    work["win_proba_norm"] = pd.to_numeric(work["win_proba_norm"], errors="coerce").fillna(0.0)
    total_prob = work["win_proba_norm"].sum()
    if total_prob <= 0:
        return pd.DataFrame(columns=["trifecta", "first_lane", "approx_prob", "rank"]), []
    work["win_proba_norm"] = work["win_proba_norm"] / total_prob

    lanes = work["lane"].astype(int).tolist()
    probs = work.set_index("lane")["win_proba_norm"].to_dict()
    top_boats = (
        work.sort_values("win_proba_norm", ascending=False)
        .head(top_n_win)["lane"]
        .astype(int)
        .tolist()
    )

    eps = 1e-10
    rows = []
    for c in itertools.permutations(lanes, 3):
        if int(c[0]) not in top_boats:
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
        rows.append(
            {
                "trifecta": f"{int(c[0])}-{int(c[1])}-{int(c[2])}",
                "first_lane": int(c[0]),
                "approx_prob": approx_prob,
            }
        )
    full_df = pd.DataFrame(rows).sort_values("approx_prob", ascending=False).reset_index(drop=True)
    if not full_df.empty:
        full_df["rank"] = full_df.index + 1
    return full_df, top_boats


def main():
    parser = argparse.ArgumentParser(description="Breakdown of candidate-out races at (top_n=4, max=30)")
    parser.add_argument("--today-win", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-detail", default="reports/candidate_out_breakdown_top4_max30.csv")
    parser.add_argument("--out-summary", default="reports/candidate_out_breakdown_top4_max30_summary.json")
    args = parser.parse_args()

    win = pd.read_csv(args.today_win)
    bt = pd.read_csv(args.backtest)
    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    parts = bt["actual_trifecta"].astype(str).str.split("-", expand=True)
    bt["actual_first"] = pd.to_numeric(parts[0], errors="coerce").astype("Int64")

    race_ids = set(bt["race_id"].astype(str))
    win = win[win["race_id"].astype(str).isin(race_ids)].copy()
    win["lane"] = pd.to_numeric(win["lane"], errors="coerce")
    win["win_proba_norm"] = pd.to_numeric(win["win_proba_norm"], errors="coerce")
    win = win.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    detail_rows = []
    for race_id, g in win.groupby("race_id"):
        t = bt[bt["race_id"].astype(str) == str(race_id)]
        if t.empty:
            continue
        actual_trifecta = str(t.iloc[0]["actual_trifecta"])
        actual_first = t.iloc[0]["actual_first"]
        full80, top4 = build_full_candidates_top4(g, top_n_win=4)

        if full80.empty:
            continue
        top30 = full80.head(30).copy()

        in_top30 = bool((top30["trifecta"].astype(str) == actual_trifecta).any())
        if in_top30:
            continue  # only candidate-out races

        in_top4_first = bool(pd.notna(actual_first) and int(actual_first) in top4)
        actual_rank_series = full80.loc[full80["trifecta"].astype(str) == actual_trifecta, "rank"]
        actual_rank_full = int(actual_rank_series.iloc[0]) if not actual_rank_series.empty else None

        # Breakdown categories for out races
        if not in_top4_first:
            category = "A_actual_first_out_of_top4"
        else:
            first_present_in_top30 = bool(
                (top30["first_lane"].astype("Int64") == int(actual_first)).any()
            )
            if first_present_in_top30:
                category = "B_first_in_top4_but_second_third_order_miss"
            else:
                category = "C_first_in_top4_but_cut_by_max30_block"

        detail_rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": actual_trifecta,
                "actual_first": int(actual_first) if pd.notna(actual_first) else None,
                "top4_first_lanes": "-".join(str(x) for x in top4),
                "actual_first_in_top4": in_top4_first,
                "actual_rank_in_full80": actual_rank_full,
                "actual_in_top30": False,
                "category": category,
            }
        )

    detail = pd.DataFrame(detail_rows)
    out_detail = Path(args.out_detail)
    out_summary = Path(args.out_summary)
    out_detail.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(out_detail, index=False)

    total_races = int(bt["race_id"].nunique())
    out_races = int(len(detail))
    cat_counts = detail["category"].value_counts().to_dict() if out_races else {}
    cat_rates = {k: (v / out_races if out_races else 0.0) for k, v in cat_counts.items()}
    cat_rates_total = {k: (v / total_races if total_races else 0.0) for k, v in cat_counts.items()}

    summary = {
        "config": {"top_n_win": 4, "max_combos": 30},
        "races_total": total_races,
        "candidate_out_races": out_races,
        "candidate_out_rate": float(out_races / total_races) if total_races else None,
        "breakdown_counts": cat_counts,
        "breakdown_rates_within_out": cat_rates,
        "breakdown_rates_overall": cat_rates_total,
        "dominant_pattern": max(cat_counts, key=cat_counts.get) if cat_counts else None,
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_detail}")
    print(f"saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
