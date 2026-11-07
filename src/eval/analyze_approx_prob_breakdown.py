import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def qbin(series: pd.Series, bins: int = 10) -> pd.Series:
    try:
        return pd.qcut(series, bins, labels=False, duplicates="drop") + 1
    except ValueError:
        return pd.Series([1] * len(series), index=series.index)


def parse_actual_order(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    parts = out["actual_trifecta"].astype(str).str.split("-", expand=True)
    out["actual_first"] = pd.to_numeric(parts[0], errors="coerce")
    out["actual_second"] = pd.to_numeric(parts[1], errors="coerce")
    out["actual_third"] = pd.to_numeric(parts[2], errors="coerce")
    return out


def load_data(ev_path: Path, bt_path: Path, win_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ev = pd.read_csv(ev_path)
    bt = pd.read_csv(bt_path)
    win = pd.read_csv(win_path)

    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    bt = parse_actual_order(bt)

    for c in ["first_lane", "second_lane", "third_lane", "first_win_proba", "second_win_proba", "third_win_proba", "approx_prob", "ev"]:
        if c in ev.columns:
            ev[c] = pd.to_numeric(ev[c], errors="coerce")

    win["lane"] = pd.to_numeric(win["lane"], errors="coerce")
    win["win_proba_norm"] = pd.to_numeric(win["win_proba_norm"], errors="coerce")
    return ev, bt, win


def add_formula_components(cand: pd.DataFrame) -> pd.DataFrame:
    out = cand.copy()
    eps = 1e-12
    denom2 = (1.0 - out["first_win_proba"]).clip(lower=eps)
    denom3 = (1.0 - out["first_win_proba"] - out["second_win_proba"]).clip(lower=eps)
    out["p2_cond"] = out["second_win_proba"] / denom2
    out["p3_cond"] = out["third_win_proba"] / denom3
    out["approx_reconstructed"] = out["first_win_proba"] * out["p2_cond"] * out["p3_cond"]
    return out


def make_bin_report(df: pd.DataFrame, score_col: str, out_path: Path) -> pd.DataFrame:
    work = df.copy()
    work["bin"] = qbin(work[score_col], 10)
    rep = (
        work.groupby("bin", as_index=False)
        .agg(
            count=("race_id", "count"),
            mean_approx_prob=("approx_prob", "mean"),
            mean_first_win_proba=("first_win_proba", "mean"),
            hit_count=("hit", "sum"),
            hit_rate=("hit", "mean"),
            first_match_rate=("first_match", "mean"),
            mean_ev=("ev", "mean"),
        )
        .sort_values("bin")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(out_path, index=False)
    return rep


def main():
    parser = argparse.ArgumentParser(description="Analyze approx_prob breakdown and miss patterns")
    parser.add_argument("--ev-analysis", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--today-win", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--out-summary", default="reports/approx_prob_breakdown_summary.json")
    parser.add_argument("--out-top1-bins", default="reports/approx_prob_top1_bins.csv")
    parser.add_argument("--out-all-bins", default="reports/approx_prob_all_candidate_bins.csv")
    parser.add_argument("--out-race-breakdown", default="reports/approx_prob_race_breakdown.csv")
    args = parser.parse_args()

    ev, bt, win = load_data(Path(args.ev_analysis), Path(args.backtest), Path(args.today_win))
    cand = ev.merge(bt, on="race_id", how="inner")
    cand = add_formula_components(cand)

    cand["hit"] = cand["trifecta"].astype(str) == cand["actual_trifecta"].astype(str)
    cand["first_match"] = cand["first_lane"].astype("Int64") == cand["actual_first"].astype("Int64")
    cand["second_match"] = cand["second_lane"].astype("Int64") == cand["actual_second"].astype("Int64")
    cand["third_match"] = cand["third_lane"].astype("Int64") == cand["actual_third"].astype("Int64")
    cand = cand.sort_values(["race_id", "approx_prob"], ascending=[True, False]).copy()
    cand["approx_rank"] = cand.groupby("race_id").cumcount() + 1

    top1 = cand[cand["approx_rank"] == 1].copy()
    top1["order_stage"] = "hit"
    top1.loc[~top1["first_match"], "order_stage"] = "first_miss"
    top1.loc[top1["first_match"] & ~top1["second_match"], "order_stage"] = "second_miss"
    top1.loc[top1["first_match"] & top1["second_match"] & ~top1["third_match"], "order_stage"] = "third_miss"

    # Race-level candidate coverage for actual trifecta
    race_cov = (
        cand.groupby("race_id")
        .agg(
            actual_in_candidates=("hit", "max"),
            best_hit_rank=("approx_rank", lambda s: int(s[cand.loc[s.index, "hit"]].min()) if cand.loc[s.index, "hit"].any() else None),
        )
        .reset_index()
    )

    # Is actual first in top3 first_win_proba lanes?
    top3_lanes = (
        win.sort_values(["race_id", "win_proba_norm"], ascending=[True, False])
        .groupby("race_id")
        .head(3)
        .groupby("race_id")["lane"]
        .apply(lambda s: set(pd.to_numeric(s, errors="coerce").dropna().astype(int).tolist()))
        .reset_index(name="top3_lanes")
    )
    bt_first = bt[["race_id", "actual_first"]].copy()
    cov = race_cov.merge(bt_first, on="race_id", how="left").merge(top3_lanes, on="race_id", how="left")
    cov["actual_first_in_top3"] = [
        (int(a) in lanes) if isinstance(lanes, set) and pd.notna(a) else False
        for a, lanes in zip(cov["actual_first"], cov["top3_lanes"])
    ]

    # Reports
    all_bins = make_bin_report(cand, "approx_prob", Path(args.out_all_bins))
    top1_bins = make_bin_report(top1, "approx_prob", Path(args.out_top1_bins))

    race_breakdown = top1[
        [
            "race_id",
            "trifecta",
            "actual_trifecta",
            "hit",
            "first_match",
            "second_match",
            "third_match",
            "order_stage",
            "first_win_proba",
            "second_win_proba",
            "third_win_proba",
            "p2_cond",
            "p3_cond",
            "approx_prob",
            "ev",
        ]
    ].copy()
    Path(args.out_race_breakdown).parent.mkdir(parents=True, exist_ok=True)
    race_breakdown.to_csv(args.out_race_breakdown, index=False)

    total_races = int(top1["race_id"].nunique())
    hit_count = int(top1["hit"].sum())
    first_match_count = int(top1["first_match"].sum())
    second_match_with_first = int((top1["first_match"] & top1["second_match"]).sum())

    cov_total = int(cov["race_id"].nunique())
    cov_hit = int(cov["actual_in_candidates"].sum())
    cov_miss = cov_total - cov_hit
    cov_miss_first_not_top3 = int((~cov["actual_in_candidates"] & ~cov["actual_first_in_top3"]).sum())
    cov_miss_first_in_top3 = int((~cov["actual_in_candidates"] & cov["actual_first_in_top3"]).sum())

    summary = {
        "races_analyzed": total_races,
        "top1_hit_count": hit_count,
        "top1_hit_rate": float(hit_count / total_races) if total_races else None,
        "top1_first_match_count": first_match_count,
        "top1_first_match_rate": float(first_match_count / total_races) if total_races else None,
        "top1_second_match_given_first_count": second_match_with_first,
        "top1_second_match_given_first_rate": float(second_match_with_first / first_match_count) if first_match_count else None,
        "top1_stage_counts": {
            "first_miss": int((top1["order_stage"] == "first_miss").sum()),
            "second_miss": int((top1["order_stage"] == "second_miss").sum()),
            "third_miss": int((top1["order_stage"] == "third_miss").sum()),
            "hit": int((top1["order_stage"] == "hit").sum()),
        },
        "candidate_coverage": {
            "races": cov_total,
            "actual_in_candidates_races": cov_hit,
            "actual_not_in_candidates_races": cov_miss,
            "not_in_candidates_actual_first_not_in_top3": cov_miss_first_not_top3,
            "not_in_candidates_actual_first_in_top3": cov_miss_first_in_top3,
        },
        "top1_miss_with_actual_first_included_common": {
            "count": int((top1["first_match"] & ~top1["hit"]).sum()),
            "mean_first_win_proba": float(top1.loc[top1["first_match"] & ~top1["hit"], "first_win_proba"].mean()),
            "mean_approx_prob": float(top1.loc[top1["first_match"] & ~top1["hit"], "approx_prob"].mean()),
            "mean_p2_cond": float(top1.loc[top1["first_match"] & ~top1["hit"], "p2_cond"].mean()),
            "mean_p3_cond": float(top1.loc[top1["first_match"] & ~top1["hit"], "p3_cond"].mean()),
        },
    }

    out_summary = Path(args.out_summary)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_summary}")
    print(f"saved: {args.out_top1_bins}")
    print(f"saved: {args.out_all_bins}")
    print(f"saved: {args.out_race_breakdown}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nall candidate bins (tail):")
    print(all_bins.tail(3).to_string(index=False))
    print("\ntop1 bins (tail):")
    print(top1_bins.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
