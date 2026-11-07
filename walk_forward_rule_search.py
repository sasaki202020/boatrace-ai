from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y", "t"})


def safe_read(path: Path) -> pd.DataFrame:
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.read_csv(path)


def parse_thresholds(raw: str) -> list[float]:
    values = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        values.append(float(token))
    if not values:
        raise ValueError("no thresholds supplied")
    return sorted(set(values))


def evaluate(df: pd.DataFrame, threshold: float, exclude_risk: bool = True) -> dict[str, float | int | None]:
    work = df.copy()
    work["ev"] = pd.to_numeric(work["ev"], errors="coerce")
    work["odds"] = pd.to_numeric(work.get("odds", pd.Series(dtype=float)), errors="coerce")
    work["hit"] = to_bool(work["hit"]) if "hit" in work.columns else False
    work["result_available"] = to_bool(work["result_available"]) if "result_available" in work.columns else True
    work["risk_flag"] = to_bool(work["risk_flag"]) if "risk_flag" in work.columns else False

    work = work[work["result_available"]].copy()
    buy = work[work["ev"] >= threshold].copy()
    if exclude_risk:
        buy = buy[~buy["risk_flag"]].copy()

    buy_count = int(len(buy))
    hit_count = int(buy["hit"].sum()) if buy_count else 0
    payout_total = float((buy["hit"].astype(int) * buy["odds"].fillna(0.0)).sum()) if buy_count else 0.0
    roi = (payout_total / buy_count) if buy_count else None
    hit_rate = (hit_count / buy_count) if buy_count else None
    avg_odds = float(buy["odds"].mean()) if buy_count and buy["odds"].notna().any() else None
    return {
        "ev_threshold": threshold,
        "exclude_risk_flag": exclude_risk,
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": hit_rate,
        "roi": roi,
        "avg_odds": avg_odds,
    }


def max_drawdown(curve: list[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return max_dd


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward rule search for EV thresholding")
    parser.add_argument("--input", required=True, help="EV table CSV path")
    parser.add_argument("--thresholds", default="0.8,1.0,1.2,1.5,2.0,3.0,5.0", help="Comma separated EV thresholds")
    parser.add_argument("--min-train-months", type=int, default=3, help="Minimum initial train months")
    parser.add_argument("--output-dir", default="reports/walk_forward", help="Output directory")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = safe_read(input_path)
    if "date" not in df.columns:
        raise ValueError("input must contain date column")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()
    df = df.sort_values(["date", "race_id"] if "race_id" in df.columns else ["date"]).reset_index(drop=True)

    thresholds = parse_thresholds(args.thresholds)
    df["month"] = df["date"].dt.to_period("M").astype(str)
    months = sorted(df["month"].dropna().unique())
    use_months = len(months) >= max(2, args.min_train_months + 1)
    periods = months if use_months else sorted(df["date"].dt.strftime("%Y-%m-%d").dropna().unique())

    monthly_rows: list[dict[str, object]] = []
    threshold_rows: list[dict[str, object]] = []

    if len(periods) < 2:
        overall_metrics = [evaluate(df, th, exclude_risk=True) for th in thresholds]
        for m in overall_metrics:
            m["month"] = periods[0] if periods else "all"
            m["split"] = "all"
            threshold_rows.append(m)
        best = max(
            overall_metrics,
            key=lambda row: (row["roi"] if row["roi"] is not None else float("-inf"), row["buy_count"]),
        )
        best_row = evaluate(df, float(best["ev_threshold"]), exclude_risk=True)
        best_row["month"] = periods[0] if periods else "all"
        best_row["split"] = "all"
        best_row["chosen_threshold"] = float(best["ev_threshold"])
        monthly_rows.append(best_row)
    else:
        start_idx = max(1, args.min_train_months)
        if start_idx >= len(periods):
            start_idx = len(periods) - 1
        for idx in range(start_idx, len(periods)):
            train_periods = periods[:idx]
            test_period = periods[idx]
            if use_months:
                train = df[df["month"].isin(train_periods)].copy()
                test = df[df["month"] == test_period].copy()
            else:
                train = df[df["date"].dt.strftime("%Y-%m-%d").isin(train_periods)].copy()
                test = df[df["date"].dt.strftime("%Y-%m-%d") == test_period].copy()
            if train.empty or test.empty:
                continue

            train_metrics = [evaluate(train, th, exclude_risk=True) for th in thresholds]
            for m in train_metrics:
                m["month"] = test_period
                m["split"] = "train"
                threshold_rows.append(m)

            best = max(
                train_metrics,
                key=lambda row: (row["roi"] if row["roi"] is not None else float("-inf"), row["buy_count"]),
            )
            test_metrics = evaluate(test, float(best["ev_threshold"]), exclude_risk=True)
            test_metrics["month"] = test_period
            test_metrics["split"] = "test"
            test_metrics["chosen_threshold"] = float(best["ev_threshold"])
            monthly_rows.append(test_metrics)

    monthly_df = pd.DataFrame(monthly_rows)
    threshold_df = pd.DataFrame(threshold_rows)

    monthly_path = output_dir / "monthly_performance.csv"
    threshold_path = output_dir / "threshold_search.csv"
    summary_path = output_dir / "walk_forward_summary.json"
    monthly_df.to_csv(monthly_path, index=False)
    threshold_df.to_csv(threshold_path, index=False)

    summary = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "input": str(input_path),
        "months": months,
        "period_mode": "month" if use_months else "date",
        "min_train_months": args.min_train_months,
        "thresholds": thresholds,
        "test_months": int(len(monthly_df)),
        "outputs": {
            "monthly_performance": str(monthly_path),
            "threshold_search": str(threshold_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
