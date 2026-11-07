import argparse
import json
from pathlib import Path

import pandas as pd
from sklearn.isotonic import IsotonicRegression


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def build_decile_report(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    work = df.copy()
    try:
        work["decile"] = pd.qcut(work[score_col], 10, labels=False, duplicates="drop") + 1
    except ValueError:
        work["decile"] = 1

    rep = (
        work.groupby("decile", as_index=False)
        .agg(
            count=("race_id", "count"),
            mean_pred=(score_col, "mean"),
            actual_first_rate=("top1_hit", "mean"),
        )
        .sort_values("decile")
    )
    rep["prob_minus_actual"] = rep["mean_pred"] - rep["actual_first_rate"]
    return rep


def calc_topk_hits(
    pred_df: pd.DataFrame, truth_df: pd.DataFrame, score_col: str, suffix: str, tie_break_col: str | None = None
) -> pd.DataFrame:
    rows = []
    for rid, g in pred_df.groupby("race_id"):
        g = g.dropna(subset=["lane", score_col]).copy()
        if tie_break_col and tie_break_col in g.columns:
            g = g.sort_values([score_col, tie_break_col], ascending=[False, False])
        else:
            g = g.sort_values(score_col, ascending=False)
        if g.empty:
            continue
        lanes = g["lane"].astype(int).tolist()
        rows.append(
            {
                "race_id": rid,
                f"pred_top1_{suffix}": lanes[0],
                f"pred_top2_{suffix}": lanes[:2],
                f"pred_top3_{suffix}": lanes[:3],
                f"first_win_proba_{suffix}": float(g.iloc[0][score_col]),
            }
        )
    top = pd.DataFrame(rows)
    out = top.merge(truth_df[["race_id", "actual_first_lane"]], on="race_id", how="inner")
    out[f"top1_hit_{suffix}"] = (out[f"pred_top1_{suffix}"] == out["actual_first_lane"]).astype(int)
    out[f"top2_hit_{suffix}"] = [
        int(a in b) for a, b in zip(out["actual_first_lane"], out[f"pred_top2_{suffix}"])
    ]
    out[f"top3_hit_{suffix}"] = [
        int(a in b) for a, b in zip(out["actual_first_lane"], out[f"pred_top3_{suffix}"])
    ]
    return out


def main():
    parser = argparse.ArgumentParser(description="Offline isotonic calibration analysis for first_win_proba")
    parser.add_argument("--pred", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-before", default="reports/first_win_proba_decile_before.csv")
    parser.add_argument("--out-after", default="reports/first_win_proba_decile_after_isotonic.csv")
    parser.add_argument("--out-summary", default="reports/first_win_proba_isotonic_summary.json")
    parser.add_argument("--out-race", default="reports/first_win_proba_isotonic_race_level.csv")
    args = parser.parse_args()

    pred = pd.read_csv(args.pred)
    bt = pd.read_csv(args.backtest)

    pred["lane"] = pd.to_numeric(pred["lane"], errors="coerce")
    pred["win_proba_norm"] = pd.to_numeric(pred["win_proba_norm"], errors="coerce")
    pred = pred.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    truth = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    truth["result_available"] = to_bool(truth["result_available"])
    truth = truth[truth["result_available"]].copy()
    truth["actual_first_lane"] = pd.to_numeric(
        truth["actual_trifecta"].astype(str).str.split("-").str[0], errors="coerce"
    )
    truth = truth.dropna(subset=["actual_first_lane"]).copy()

    before = calc_topk_hits(pred, truth, "win_proba_norm", "before")
    # race-level first win proba + binary target
    train_df = before[["race_id", "first_win_proba_before", "top1_hit_before"]].rename(
        columns={"first_win_proba_before": "first_win_proba", "top1_hit_before": "top1_hit"}
    )

    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(train_df["first_win_proba"], train_df["top1_hit"])

    pred["win_proba_iso"] = iso.predict(pred["win_proba_norm"].to_numpy())
    # Keep original ordering signal when isotonic outputs ties
    after = calc_topk_hits(pred, truth, "win_proba_iso", "after", tie_break_col="win_proba_norm")

    before_rep = build_decile_report(
        before.rename(columns={"first_win_proba_before": "score", "top1_hit_before": "top1_hit"}),
        "score",
    )
    after_rep = build_decile_report(
        after.rename(columns={"first_win_proba_after": "score", "top1_hit_after": "top1_hit"}),
        "score",
    )

    out_before = Path(args.out_before)
    out_after = Path(args.out_after)
    out_summary = Path(args.out_summary)
    out_race = Path(args.out_race)
    for p in [out_before, out_after, out_summary, out_race]:
        p.parent.mkdir(parents=True, exist_ok=True)

    before_rep.to_csv(out_before, index=False)
    after_rep.to_csv(out_after, index=False)

    race = before.merge(after[["race_id", "first_win_proba_after", "top1_hit_after", "top2_hit_after", "top3_hit_after"]], on="race_id", how="inner")
    race.to_csv(out_race, index=False)

    summary = {
        "races_evaluated": int(len(race)),
        "topk_before": {
            "top1_rate": float(race["top1_hit_before"].mean()),
            "top2_rate": float(race["top2_hit_before"].mean()),
            "top3_rate": float(race["top3_hit_before"].mean()),
        },
        "topk_after_isotonic": {
            "top1_rate": float(race["top1_hit_after"].mean()),
            "top2_rate": float(race["top2_hit_after"].mean()),
            "top3_rate": float(race["top3_hit_after"].mean()),
        },
        "decile_before_mean_abs_gap": float(before_rep["prob_minus_actual"].abs().mean()),
        "decile_after_mean_abs_gap": float(after_rep["prob_minus_actual"].abs().mean()),
        "improved_calibration_gap": float(before_rep["prob_minus_actual"].abs().mean() - after_rep["prob_minus_actual"].abs().mean()),
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_before}")
    print(f"saved: {out_after}")
    print(f"saved: {out_race}")
    print(f"saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
