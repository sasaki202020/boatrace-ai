import argparse
import itertools
import json
from pathlib import Path

import pandas as pd
from sklearn.isotonic import IsotonicRegression


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def build_truth(backtest_df: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "actual_trifecta", "official_odds", "result_available"}
    missing = required - set(backtest_df.columns)
    if missing:
        raise ValueError(f"backtest_race_results missing columns: {sorted(missing)}")

    truth = (
        backtest_df[["race_id", "actual_trifecta", "official_odds", "result_available"]]
        .drop_duplicates(subset=["race_id"])
        .copy()
    )
    truth["official_odds"] = pd.to_numeric(truth["official_odds"], errors="coerce")
    truth["result_available"] = to_bool_series(truth["result_available"])
    return truth


def build_top1_label_df(win_df: pd.DataFrame, truth_df: pd.DataFrame) -> pd.DataFrame:
    top1 = (
        win_df.sort_values(["race_id", "win_proba_norm"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()
        .rename(columns={"lane": "pred_top1_lane", "win_proba_norm": "first_win_proba"})
    )
    top1["pred_top1_lane"] = pd.to_numeric(top1["pred_top1_lane"], errors="coerce")

    truth_lane = truth_df.copy()
    truth_lane["actual_first_lane"] = pd.to_numeric(
        truth_lane["actual_trifecta"].astype(str).str.split("-").str[0], errors="coerce"
    )
    merged = top1.merge(truth_lane[["race_id", "result_available", "actual_first_lane"]], on="race_id", how="inner")
    merged = merged[merged["result_available"]].dropna(subset=["pred_top1_lane", "actual_first_lane"]).copy()
    merged["top1_hit"] = (merged["pred_top1_lane"].astype(int) == merged["actual_first_lane"].astype(int)).astype(int)
    return merged[["race_id", "first_win_proba", "top1_hit"]]


def apply_isotonic_to_lane_probs(win_df: pd.DataFrame, iso: IsotonicRegression) -> pd.DataFrame:
    out = win_df.copy()
    out["win_proba_iso_raw"] = iso.predict(out["win_proba_norm"].to_numpy())
    sum_iso = out.groupby("race_id")["win_proba_iso_raw"].transform("sum")
    out["win_proba_iso"] = out["win_proba_iso_raw"] / sum_iso
    out["win_proba_iso"] = out["win_proba_iso"].fillna(0.0)
    return out


def generate_candidates(win_df: pd.DataFrame, prob_col: str, top_n_win: int, max_combos: int) -> pd.DataFrame:
    rows = []
    for race_id, group in win_df.groupby("race_id"):
        work = group.copy()
        work[prob_col] = pd.to_numeric(work[prob_col], errors="coerce").fillna(0.0)
        total_prob = work[prob_col].sum()
        if total_prob <= 0:
            continue
        work[prob_col] = work[prob_col] / total_prob

        sorted_boats = work.sort_values(prob_col, ascending=False)
        top_boats = sorted_boats.head(top_n_win)["lane"].tolist()
        lanes = work["lane"].tolist()
        probs = work.set_index("lane")[prob_col].to_dict()

        race_rows = []
        for c in itertools.permutations(lanes, 3):
            if c[0] not in top_boats:
                continue
            eps = 1e-10
            p1 = probs[c[0]]
            remain_after_first = sum(probs[lane] for lane in lanes if lane != c[0])
            remain_after_second = sum(probs[lane] for lane in lanes if lane not in (c[0], c[1]))
            if p1 <= 0 or remain_after_first <= eps or remain_after_second <= eps:
                continue
            p2 = probs[c[1]] / remain_after_first
            p3 = probs[c[2]] / remain_after_second
            approx_prob = min(p1 * p2 * p3, 1.0)
            if approx_prob <= 0:
                continue
            race_rows.append(
                {
                    "race_id": race_id,
                    "trifecta": f"{c[0]}-{c[1]}-{c[2]}",
                    "approx_prob": approx_prob,
                    "first_win_proba": p1,
                }
            )
        race_rows = sorted(race_rows, key=lambda x: x["approx_prob"], reverse=True)[:max_combos]
        rows.extend(race_rows)
    return pd.DataFrame(rows)


def attach_odds_and_ev(cand_df: pd.DataFrame, odds_df: pd.DataFrame | None) -> pd.DataFrame:
    out = cand_df.copy()
    if odds_df is not None and {"race_id", "trifecta", "odds"}.issubset(odds_df.columns):
        merged = out.merge(
            odds_df[["race_id", "trifecta", "odds"]].copy(),
            on=["race_id", "trifecta"],
            how="left",
        )
        merged["odds_source"] = "fallback_fixed"
        merged.loc[merged["odds"].notna(), "odds_source"] = "file"
        merged["odds"] = pd.to_numeric(merged["odds"], errors="coerce").fillna(50.0)
        out = merged
    else:
        out["odds"] = 50.0
        out["odds_source"] = "fallback_fixed"
    out["ev"] = out["approx_prob"] * out["odds"]
    return out


def pick_ev_top1(ev_df: pd.DataFrame) -> pd.DataFrame:
    return (
        ev_df.sort_values(["race_id", "ev"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()
    )


def evaluate_top1(top_df: pd.DataFrame, truth_df: pd.DataFrame, race_filter: set[str] | None = None) -> dict:
    merged = top_df.merge(truth_df, on="race_id", how="inner")
    merged = merged[merged["result_available"]].copy()
    if race_filter is not None:
        merged = merged[merged["race_id"].astype(str).isin(race_filter)].copy()
    merged["hit"] = merged["trifecta"].astype(str) == merged["actual_trifecta"].astype(str)
    merged["settled_odds"] = pd.to_numeric(merged["official_odds"], errors="coerce").fillna(merged["odds"])
    count = int(len(merged))
    hit_count = int(merged["hit"].sum())
    return {
        "count": count,
        "hit_count": hit_count,
        "hit_rate": float(hit_count / count) if count else None,
        "roi": float((merged["hit"].astype(int) * merged["settled_odds"]).sum() / count) if count else None,
        "avg_odds": float(merged["settled_odds"].mean()) if count else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Offline comparison: old EV top1 vs calibrated-first-win-proba EV top1"
    )
    parser.add_argument("--today-win-proba", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest-races", default="reports/backtest_race_results.csv")
    parser.add_argument("--odds", default="data/odds/today_trifecta_odds.csv")
    parser.add_argument("--config", default="config/strategy_config.json")
    parser.add_argument("--out-comparison", default="reports/calibrated_first_ev_comparison.csv")
    parser.add_argument("--out-race-diff", default="reports/calibrated_first_ev_topdiff.csv")
    parser.add_argument("--out-summary", default="reports/calibrated_first_ev_summary.json")
    args = parser.parse_args()

    win_df = pd.read_csv(args.today_win_proba)
    bt_df = pd.read_csv(args.backtest_races)
    truth_df = build_truth(bt_df)

    win_df["lane"] = pd.to_numeric(win_df["lane"], errors="coerce")
    win_df["win_proba_norm"] = pd.to_numeric(win_df["win_proba_norm"], errors="coerce")
    win_df = win_df.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    label_df = build_top1_label_df(win_df, truth_df)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(label_df["first_win_proba"].to_numpy(), label_df["top1_hit"].to_numpy())
    cal_win_df = apply_isotonic_to_lane_probs(win_df, iso)

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    top_n_win = int(cfg["candidate_generation"]["top_n_win"])
    max_combos = int(cfg["candidate_generation"]["max_trifecta_combinations"])

    old_cand = generate_candidates(win_df, "win_proba_norm", top_n_win=top_n_win, max_combos=max_combos)
    cal_cand = generate_candidates(cal_win_df, "win_proba_iso", top_n_win=top_n_win, max_combos=max_combos)

    odds_df = None
    odds_path = Path(args.odds)
    if odds_path.exists():
        odds_df = pd.read_csv(odds_path)

    old_ev = attach_odds_and_ev(old_cand, odds_df)
    cal_ev = attach_odds_and_ev(cal_cand, odds_df)
    old_top = pick_ev_top1(old_ev)
    cal_top = pick_ev_top1(cal_ev)

    old_races = set(old_top["race_id"].astype(str))
    cal_races = set(cal_top["race_id"].astype(str))
    common_races = old_races & cal_races

    old_metrics = evaluate_top1(old_top, truth_df)
    cal_metrics = evaluate_top1(cal_top, truth_df)
    old_common_metrics = evaluate_top1(old_top, truth_df, race_filter=common_races)
    cal_common_metrics = evaluate_top1(cal_top, truth_df, race_filter=common_races)

    diff = old_top[["race_id", "trifecta", "ev", "approx_prob", "first_win_proba"]].rename(
        columns={
            "trifecta": "old_top_trifecta",
            "ev": "old_ev",
            "approx_prob": "old_approx_prob",
            "first_win_proba": "old_first_win_proba",
        }
    ).merge(
        cal_top[["race_id", "trifecta", "ev", "approx_prob", "first_win_proba"]].rename(
            columns={
                "trifecta": "cal_top_trifecta",
                "ev": "cal_ev",
                "approx_prob": "cal_approx_prob",
                "first_win_proba": "cal_first_win_proba",
            }
        ),
        on="race_id",
        how="inner",
    )
    diff["top_changed"] = diff["old_top_trifecta"].astype(str) != diff["cal_top_trifecta"].astype(str)

    out_comp = Path(args.out_comparison)
    out_diff = Path(args.out_race_diff)
    out_summary = Path(args.out_summary)
    for p in [out_comp, out_diff, out_summary]:
        p.parent.mkdir(parents=True, exist_ok=True)

    comp_df = pd.DataFrame(
        [
            {"method": "old_ev_top1", **old_metrics},
            {"method": "calibrated_first_ev_top1", **cal_metrics},
            {"method": "old_ev_top1_common_races", **old_common_metrics},
            {"method": "calibrated_first_ev_top1_common_races", **cal_common_metrics},
        ]
    )
    comp_df.to_csv(out_comp, index=False)
    diff.to_csv(out_diff, index=False)

    summary = {
        "input_races": int(win_df["race_id"].nunique()),
        "result_available_races": int(truth_df["result_available"].sum()),
        "old_top_races": int(len(old_races)),
        "calibrated_top_races": int(len(cal_races)),
        "common_top_races": int(len(common_races)),
        "top_candidate_changed_races": int(diff["top_changed"].sum()),
        "top_candidate_changed_rate": float(diff["top_changed"].mean()) if len(diff) else None,
        "comparison": [
            {"method": "old_ev_top1", **old_metrics},
            {"method": "calibrated_first_ev_top1", **cal_metrics},
            {"method": "old_ev_top1_common_races", **old_common_metrics},
            {"method": "calibrated_first_ev_top1_common_races", **cal_common_metrics},
        ],
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"comparison saved: {out_comp}")
    print(f"race-diff saved: {out_diff}")
    print(f"summary saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
