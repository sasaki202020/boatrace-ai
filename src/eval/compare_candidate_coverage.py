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
        return pd.DataFrame(columns=["race_id", "trifecta", "first_lane", "approx_prob"])

    work["win_proba_norm"] = work["win_proba_norm"] / total_prob
    sorted_boats = work.sort_values("win_proba_norm", ascending=False)
    top_boats = sorted_boats.head(top_n_win)["lane"].tolist()
    lanes = work["lane"].tolist()
    probs = work.set_index("lane")["win_proba_norm"].to_dict()

    rows = []
    eps = 1e-10
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
        rows.append(
            {
                "race_id": work["race_id"].iloc[0],
                "trifecta": f"{c[0]}-{c[1]}-{c[2]}",
                "first_lane": int(c[0]),
                "approx_prob": approx_prob,
            }
        )

    rows = sorted(rows, key=lambda x: x["approx_prob"], reverse=True)[:max_combos]
    return pd.DataFrame(rows)


def simulate_candidates(win_df: pd.DataFrame, top_n_win: int, max_combos: int) -> pd.DataFrame:
    out = []
    for _, group in win_df.groupby("race_id"):
        cand = generate_candidates_for_race(group, top_n_win=top_n_win, max_combos=max_combos)
        if not cand.empty:
            out.append(cand)
    if not out:
        return pd.DataFrame(columns=["race_id", "trifecta", "first_lane", "approx_prob"])
    return pd.concat(out, ignore_index=True)


def evaluate_coverage(candidates: pd.DataFrame, truth: pd.DataFrame) -> dict:
    race_count = int(truth["race_id"].nunique())
    cand_counts = candidates.groupby("race_id").size().rename("candidate_count").reset_index()
    race_df = truth.merge(cand_counts, on="race_id", how="left")
    race_df["candidate_count"] = race_df["candidate_count"].fillna(0).astype(int)

    hit_df = candidates.merge(truth[["race_id", "actual_trifecta"]], on="race_id", how="inner")
    hit_df["trifecta_hit"] = hit_df["trifecta"].astype(str) == hit_df["actual_trifecta"].astype(str)
    trifecta_hit_by_race = (
        hit_df.groupby("race_id")["trifecta_hit"].max().rename("actual_in_candidates").reset_index()
    )

    first_df = candidates.merge(truth[["race_id", "actual_first"]], on="race_id", how="inner")
    first_df["first_in_candidates"] = (
        pd.to_numeric(first_df["first_lane"], errors="coerce").astype("Int64")
        == pd.to_numeric(first_df["actual_first"], errors="coerce").astype("Int64")
    )
    first_hit_by_race = (
        first_df.groupby("race_id")["first_in_candidates"].max().rename("actual_first_in_candidates").reset_index()
    )

    race_df = (
        race_df.merge(trifecta_hit_by_race, on="race_id", how="left")
        .merge(first_hit_by_race, on="race_id", how="left")
        .fillna({"actual_in_candidates": False, "actual_first_in_candidates": False})
    )

    return {
        "races": race_count,
        "actual_in_candidates_races": int(race_df["actual_in_candidates"].sum()),
        "actual_in_candidates_rate": float(race_df["actual_in_candidates"].mean()) if race_count else None,
        "actual_first_in_candidates_races": int(race_df["actual_first_in_candidates"].sum()),
        "actual_first_in_candidates_rate": float(race_df["actual_first_in_candidates"].mean()) if race_count else None,
        "candidate_count_avg": float(race_df["candidate_count"].mean()) if race_count else None,
        "candidate_count_max": int(race_df["candidate_count"].max()) if race_count else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Candidate coverage sensitivity: (top_n, max_combos)")
    parser.add_argument("--today-win", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-csv", default="reports/candidate_coverage_comparison.csv")
    parser.add_argument("--out-json", default="reports/candidate_coverage_comparison_summary.json")
    args = parser.parse_args()

    win = pd.read_csv(args.today_win)
    bt = pd.read_csv(args.backtest)

    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    parts = bt["actual_trifecta"].astype(str).str.split("-", expand=True)
    bt["actual_first"] = pd.to_numeric(parts[0], errors="coerce")

    win = win[win["race_id"].astype(str).isin(set(bt["race_id"].astype(str)))].copy()
    win["lane"] = pd.to_numeric(win["lane"], errors="coerce")
    win["win_proba_norm"] = pd.to_numeric(win["win_proba_norm"], errors="coerce")
    win = win.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    scenarios = [
        {"name": "baseline_top3_max20", "top_n_win": 3, "max_combos": 20},
        {"name": "candidate_top4_max30", "top_n_win": 4, "max_combos": 30},
    ]

    rows = []
    for s in scenarios:
        cand = simulate_candidates(win, top_n_win=s["top_n_win"], max_combos=s["max_combos"])
        metrics = evaluate_coverage(cand, bt)
        rows.append({"scenario": s["name"], "top_n_win": s["top_n_win"], "max_combos": s["max_combos"], **metrics})

    comp = pd.DataFrame(rows)
    base = comp[comp["scenario"] == "baseline_top3_max20"].iloc[0]
    cmp = comp[comp["scenario"] == "candidate_top4_max30"].iloc[0]
    delta = {
        "actual_in_candidates_races_delta": int(cmp["actual_in_candidates_races"] - base["actual_in_candidates_races"]),
        "actual_in_candidates_rate_delta": float(cmp["actual_in_candidates_rate"] - base["actual_in_candidates_rate"]),
        "actual_first_in_candidates_races_delta": int(
            cmp["actual_first_in_candidates_races"] - base["actual_first_in_candidates_races"]
        ),
        "actual_first_in_candidates_rate_delta": float(
            cmp["actual_first_in_candidates_rate"] - base["actual_first_in_candidates_rate"]
        ),
        "candidate_count_avg_delta": float(cmp["candidate_count_avg"] - base["candidate_count_avg"]),
        "candidate_count_max_delta": int(cmp["candidate_count_max"] - base["candidate_count_max"]),
    }

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    comp.to_csv(out_csv, index=False)
    out_json.write_text(
        json.dumps({"comparison": rows, "delta_top4_max30_vs_top3_max20": delta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"saved: {out_csv}")
    print(f"saved: {out_json}")
    print(comp.to_string(index=False))
    print(json.dumps(delta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
