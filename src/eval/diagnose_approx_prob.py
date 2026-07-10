import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_BINS = "0,0.02,0.04,0.06,0.08,0.10,0.15,1.01"


def parse_bins(raw: str) -> list[float]:
    vals = [float(x.strip()) for x in str(raw).split(",") if x.strip()]
    if len(vals) < 3:
        raise ValueError("bins must contain at least 3 numeric edges")
    vals = sorted(set(vals))
    if vals[0] > 0:
        vals = [0.0] + vals
    return vals


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def build_race_truth(backtest_df: pd.DataFrame) -> pd.DataFrame:
    cols = ["race_id", "actual_trifecta", "official_odds", "result_available"]
    missing = [c for c in cols if c not in backtest_df.columns]
    if missing:
        raise ValueError(f"backtest_race_results.csv missing columns: {missing}")

    truth = (
        backtest_df[cols]
        .drop_duplicates(subset=["race_id"])
        .copy()
    )
    truth["official_odds"] = pd.to_numeric(truth["official_odds"], errors="coerce")
    truth["result_available"] = to_bool_series(truth["result_available"])
    return truth


def summarize_by_bins(df: pd.DataFrame, bins: list[float]) -> pd.DataFrame:
    work = df.copy()
    work["approx_prob"] = pd.to_numeric(work["approx_prob"], errors="coerce")
    work["ev"] = pd.to_numeric(work["ev"], errors="coerce")
    work["official_odds"] = pd.to_numeric(work["official_odds"], errors="coerce")
    work["hit"] = to_bool_series(work["hit"])
    work = work.dropna(subset=["approx_prob"]).copy()

    labels = [f"[{bins[i]:.3f},{bins[i+1]:.3f})" for i in range(len(bins) - 1)]
    work["approx_prob_bin"] = pd.cut(
        work["approx_prob"],
        bins=bins,
        labels=labels,
        right=False,
        include_lowest=True,
    )

    grouped = work.groupby("approx_prob_bin", dropna=False).agg(
        count=("hit", "size"),
        hit_count=("hit", "sum"),
        avg_approx_prob=("approx_prob", "mean"),
        avg_ev=("ev", "mean"),
        official_odds_missing_count=("official_odds", lambda s: int(s.isna().sum())),
        avg_official_odds=("official_odds", "mean"),
    ).reset_index()

    grouped["hit_count"] = grouped["hit_count"].astype(int)
    grouped["hit_rate"] = grouped["hit_count"] / grouped["count"]
    grouped["official_odds_missing_rate"] = grouped["official_odds_missing_count"] / grouped["count"]
    return grouped


def monotonic_check(summary_df: pd.DataFrame) -> dict:
    check_df = summary_df[summary_df["count"] > 0].copy()
    rates = check_df["hit_rate"].tolist()
    non_decreasing = all(rates[i] <= rates[i + 1] for i in range(len(rates) - 1))
    return {
        "bins_with_data": int(len(check_df)),
        "is_non_decreasing_hit_rate": bool(non_decreasing),
        "hit_rate_sequence": rates,
    }


def build_all_candidates(ev_df: pd.DataFrame, truth_df: pd.DataFrame) -> pd.DataFrame:
    merged = ev_df.merge(truth_df, on="race_id", how="left")
    merged["hit"] = merged["result_available"] & merged["trifecta"].astype(str).eq(merged["actual_trifecta"].astype(str))
    return merged


def build_buy_candidates(ev_df: pd.DataFrame, backtest_df: pd.DataFrame, truth_df: pd.DataFrame) -> pd.DataFrame:
    buy = backtest_df[backtest_df["decision"].astype(str).str.upper() == "BUY"].copy()
    pick = buy[["race_id", "predicted_trifecta"]].rename(columns={"predicted_trifecta": "trifecta"})
    merged = pick.merge(ev_df, on=["race_id", "trifecta"], how="left")
    merged = merged.merge(truth_df, on="race_id", how="left")
    merged["hit"] = merged["result_available"] & merged["trifecta"].astype(str).eq(merged["actual_trifecta"].astype(str))
    return merged


def main():
    parser = argparse.ArgumentParser(description="Diagnose approx_prob calibration by bins")
    parser.add_argument("--ev-analysis", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--backtest-races", default="reports/backtest_race_results.csv")
    parser.add_argument("--bins", default=DEFAULT_BINS, help="comma-separated bin edges for approx_prob")
    parser.add_argument("--out-all", default="reports/approx_prob_diagnostic_all_candidates.csv")
    parser.add_argument("--out-buy", default="reports/approx_prob_diagnostic_buy_candidates.csv")
    parser.add_argument("--out-summary", default="reports/approx_prob_diagnostic_summary.json")
    args = parser.parse_args()

    ev_path = Path(args.ev_analysis)
    race_path = Path(args.backtest_races)
    if not ev_path.exists():
        raise FileNotFoundError(f"ev analysis not found: {ev_path}")
    if not race_path.exists():
        raise FileNotFoundError(f"backtest race results not found: {race_path}")

    bins = parse_bins(args.bins)
    ev_df = pd.read_csv(ev_path)
    race_df = pd.read_csv(race_path)
    truth_df = build_race_truth(race_df)

    all_candidates = build_all_candidates(ev_df, truth_df)
    buy_candidates = build_buy_candidates(ev_df, race_df, truth_df)

    all_summary = summarize_by_bins(all_candidates, bins)
    buy_summary = summarize_by_bins(buy_candidates, bins)

    out_all = Path(args.out_all)
    out_buy = Path(args.out_buy)
    out_summary = Path(args.out_summary)
    out_all.parent.mkdir(parents=True, exist_ok=True)
    out_buy.parent.mkdir(parents=True, exist_ok=True)
    out_summary.parent.mkdir(parents=True, exist_ok=True)

    all_summary.to_csv(out_all, index=False)
    buy_summary.to_csv(out_buy, index=False)

    summary = {
        "inputs": {
            "ev_analysis": str(ev_path),
            "backtest_race_results": str(race_path),
        },
        "all_candidates": {
            "rows": int(len(all_candidates)),
            "result_available_rows": int(to_bool_series(all_candidates["result_available"]).sum()),
            "hit_count": int(to_bool_series(all_candidates["hit"]).sum()),
            "monotonicity": monotonic_check(all_summary),
        },
        "buy_candidates": {
            "rows": int(len(buy_candidates)),
            "result_available_rows": int(to_bool_series(buy_candidates["result_available"]).sum()),
            "hit_count": int(to_bool_series(buy_candidates["hit"]).sum()),
            "monotonicity": monotonic_check(buy_summary),
        },
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"all-candidate diagnostics saved: {out_all}")
    print(f"buy-candidate diagnostics saved: {out_buy}")
    print(f"diagnostic summary saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
