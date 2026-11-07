import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def bin_numeric(series: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return pd.cut(s, bins=bins, labels=labels, include_lowest=True)


def main():
    parser = argparse.ArgumentParser(description="Condition-wise analysis for outlier27 concentration.")
    parser.add_argument("--outlier-summary", default="reports/outlier27_summary.csv")
    parser.add_argument("--features", default="data/features/today_features.csv")
    parser.add_argument("--today-races", default="data/processed/today_races.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-agg", default="reports/outlier27_condition_aggregates.csv")
    parser.add_argument("--out-json", default="reports/outlier27_condition_summary.json")
    parser.add_argument("--out-rows", default="reports/outlier27_condition_rows.csv")
    args = parser.parse_args()

    outlier = pd.read_csv(args.outlier_summary)
    feat = pd.read_csv(args.features)
    races = pd.read_csv(args.today_races)
    bt = pd.read_csv(args.backtest)

    # Winner rows for outlier27
    out = outlier[["race_id", "actual_winner_lane", "winner_predicted_rank", "prob_gap_top1_minus_winner"]].copy()
    out["lane"] = pd.to_numeric(out["actual_winner_lane"], errors="coerce")

    # Winner rows for full baseline (599)
    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    bt["lane"] = pd.to_numeric(bt["actual_trifecta"].astype(str).str.split("-").str[0], errors="coerce")

    # Prepare join sources
    for df in (feat, races):
        df["lane"] = pd.to_numeric(df["lane"], errors="coerce")

    feature_cols = [
        "jcd", "weather_num", "motor_2ren_rate", "boat_2ren_rate",
        "national_2ren_rate", "local_2ren_rate", "avg_st", "st_diff_to_min",
        "win_rate_diff_to_avg",
    ]
    race_cols = ["race_id", "lane", "weather", "wind_speed", "wave_height", "prev_race_course", "start_display_st"]
    feat_keep = [c for c in ["race_id", "lane"] + feature_cols if c in feat.columns]
    race_keep = [c for c in race_cols if c in races.columns]
    source = feat[feat_keep].merge(races[race_keep], on=["race_id", "lane"], how="left")

    out_rows = out.merge(source, on=["race_id", "lane"], how="left")
    base_rows = bt[["race_id", "lane"]].merge(source, on=["race_id", "lane"], how="left")

    # Derived bins
    for df in (out_rows, base_rows):
        if "wind_speed" in df.columns:
            df["wind_speed_bin"] = bin_numeric(
                df["wind_speed"], bins=[-1e9, 1, 3, 5, 1e9], labels=["<=1", "1-3", "3-5", ">=5"]
            ).astype(str)
        if "wave_height" in df.columns:
            df["wave_height_bin"] = bin_numeric(
                df["wave_height"], bins=[-1e9, 1, 3, 5, 1e9], labels=["<=1", "1-3", "3-5", ">=5"]
            ).astype(str)
        if "motor_2ren_rate" in df.columns:
            df["motor_2ren_bin"] = bin_numeric(
                df["motor_2ren_rate"], bins=[-1e9, 25, 35, 45, 1e9], labels=["<=25", "25-35", "35-45", ">=45"]
            ).astype(str)
        if "boat_2ren_rate" in df.columns:
            df["boat_2ren_bin"] = bin_numeric(
                df["boat_2ren_rate"], bins=[-1e9, 25, 35, 45, 1e9], labels=["<=25", "25-35", "35-45", ">=45"]
            ).astype(str)

    cond_cols = [
        c for c in [
            "jcd", "weather_num", "weather", "wind_speed_bin", "wave_height_bin",
            "motor_2ren_bin", "boat_2ren_bin", "prev_race_course", "start_display_st"
        ]
        if c in out_rows.columns
    ]

    out_total = len(out_rows)
    base_total = len(base_rows)
    agg_rows = []
    for col in cond_cols:
        o = out_rows[[col, "winner_predicted_rank", "prob_gap_top1_minus_winner"]].copy()
        b = base_rows[[col]].copy()
        o[col] = o[col].astype(str).fillna("NA")
        b[col] = b[col].astype(str).fillna("NA")

        o_group = o.groupby(col).agg(
            outlier_count=("winner_predicted_rank", "size"),
            winner_rank_mean=("winner_predicted_rank", "mean"),
            winner_rank_median=("winner_predicted_rank", "median"),
            prob_gap_mean=("prob_gap_top1_minus_winner", "mean"),
            prob_gap_median=("prob_gap_top1_minus_winner", "median"),
        ).reset_index()
        b_group = b.groupby(col).size().rename("base_count").reset_index()
        g = o_group.merge(b_group, on=col, how="left")
        g["condition"] = col
        g["outlier_rate_within_27"] = g["outlier_count"] / out_total if out_total else 0.0
        g["base_rate_within_599"] = g["base_count"] / base_total if base_total else 0.0
        g["lift_vs_base"] = g["outlier_rate_within_27"] / g["base_rate_within_599"].replace(0, pd.NA)
        g = g.rename(columns={col: "condition_value"})
        agg_rows.append(g)

    agg = pd.concat(agg_rows, ignore_index=True) if agg_rows else pd.DataFrame()
    if not agg.empty:
        agg = agg.sort_values(["lift_vs_base", "outlier_count"], ascending=[False, False])

    out_agg = Path(args.out_agg)
    out_json = Path(args.out_json)
    out_rows_csv = Path(args.out_rows)
    out_agg.parent.mkdir(parents=True, exist_ok=True)
    agg.to_csv(out_agg, index=False)
    out_rows.to_csv(out_rows_csv, index=False)

    top_conc = (
        agg[(agg["outlier_count"] >= 3) & (agg["base_count"] >= 10)]
        .head(10)[["condition", "condition_value", "outlier_count", "base_count", "lift_vs_base"]]
        .to_dict(orient="records")
        if not agg.empty else []
    )

    summary = {
        "outlier_count": int(out_total),
        "baseline_winner_count": int(base_total),
        "conditions_analyzed": cond_cols,
        "top_concentrated_conditions": top_conc,
        "avg_winner_rank": round(float(out_rows["winner_predicted_rank"].mean()), 4) if out_total else None,
        "avg_prob_gap": round(float(out_rows["prob_gap_top1_minus_winner"].mean()), 4) if out_total else None,
        "conditional_correction_candidate": bool(len(top_conc) > 0),
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {out_agg}")
    print(f"[saved] {out_rows_csv}")
    print(f"[saved] {out_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
