import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def main():
    parser = argparse.ArgumentParser(description="Generate first_win_proba decile calibration report")
    parser.add_argument("--pred", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-csv", default="reports/first_win_proba_decile_report.csv")
    parser.add_argument("--out-summary", default="reports/first_win_proba_decile_summary.json")
    args = parser.parse_args()

    pred = pd.read_csv(args.pred)
    bt = pd.read_csv(args.backtest)

    required_pred = {"race_id", "lane", "win_proba_norm"}
    required_bt = {"race_id", "actual_trifecta", "result_available"}
    if not required_pred.issubset(pred.columns):
        raise ValueError(f"missing pred columns: {sorted(required_pred - set(pred.columns))}")
    if not required_bt.issubset(bt.columns):
        raise ValueError(f"missing backtest columns: {sorted(required_bt - set(bt.columns))}")

    pred["lane"] = pd.to_numeric(pred["lane"], errors="coerce")
    pred["win_proba_norm"] = pd.to_numeric(pred["win_proba_norm"], errors="coerce")
    pred = pred.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    # race top1 predicted lane + probability
    race_top1 = (
        pred.sort_values(["race_id", "win_proba_norm"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()[["race_id", "lane", "win_proba_norm"]]
        .rename(columns={"lane": "pred_top1_lane", "win_proba_norm": "first_win_proba"})
    )

    truth = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    truth["result_available"] = to_bool(truth["result_available"])
    truth = truth[truth["result_available"]].copy()
    truth["actual_first_lane"] = pd.to_numeric(
        truth["actual_trifecta"].astype(str).str.split("-").str[0], errors="coerce"
    )

    df = race_top1.merge(truth[["race_id", "actual_first_lane"]], on="race_id", how="inner")
    df = df.dropna(subset=["actual_first_lane"]).copy()
    df["top1_hit"] = (df["pred_top1_lane"] == df["actual_first_lane"]).astype(int)

    # 10-quantile bins (low -> high)
    try:
        df["decile"] = pd.qcut(df["first_win_proba"], 10, labels=False, duplicates="drop") + 1
    except ValueError:
        # Fallback for degenerate distributions
        df["decile"] = 1

    rep = (
        df.groupby("decile", as_index=False)
        .agg(
            count=("race_id", "count"),
            mean_first_win_proba=("first_win_proba", "mean"),
            actual_first_rate=("top1_hit", "mean"),
            top1_hit_rate=("top1_hit", "mean"),
        )
        .sort_values("decile")
    )
    rep["prob_minus_actual"] = rep["mean_first_win_proba"] - rep["actual_first_rate"]
    rep["actual_minus_prob"] = rep["actual_first_rate"] - rep["mean_first_win_proba"]
    rep["bias"] = rep["prob_minus_actual"].apply(
        lambda x: "overestimated" if x > 0 else ("underestimated" if x < 0 else "well_calibrated")
    )

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(out_csv, index=False)

    over = rep[rep["prob_minus_actual"] > 0].sort_values("prob_minus_actual", ascending=False)
    under = rep[rep["prob_minus_actual"] < 0].sort_values("prob_minus_actual")

    summary = {
        "races_evaluated": int(len(df)),
        "deciles": int(rep["decile"].nunique()),
        "overall_top1_hit_rate": float(df["top1_hit"].mean()) if len(df) else 0.0,
        "most_overestimated_deciles": over.head(3)[["decile", "prob_minus_actual"]].to_dict("records"),
        "most_underestimated_deciles": under.head(3)[["decile", "prob_minus_actual"]].to_dict("records"),
    }

    out_summary = Path(args.out_summary)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_csv}")
    print(f"saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
