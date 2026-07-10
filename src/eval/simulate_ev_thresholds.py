import argparse
from pathlib import Path

import pandas as pd


def parse_thresholds(raw: str) -> list[float]:
    values = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        values.append(float(token))
    if not values:
        raise ValueError("No thresholds provided")
    return sorted(set(values))


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def simulate(df: pd.DataFrame, threshold: float, exclude_risk: bool) -> dict:
    work = df.copy()
    work["result_available"] = to_bool_series(work["result_available"]) if "result_available" in work.columns else True
    work["hit"] = to_bool_series(work["hit"]) if "hit" in work.columns else False
    work["ev"] = pd.to_numeric(work["ev"], errors="coerce")
    work["settled_odds"] = pd.to_numeric(work["settled_odds"], errors="coerce")
    if "risk_flag" in work.columns:
        work["risk_flag"] = to_bool_series(work["risk_flag"])
    else:
        work["risk_flag"] = False

    work = work[work["result_available"]].copy()
    buy = work[work["ev"] >= threshold].copy()
    if exclude_risk:
        buy = buy[~buy["risk_flag"]].copy()

    buy_count = int(len(buy))
    hit_count = int(buy["hit"].sum())
    hit_rate = (hit_count / buy_count) if buy_count > 0 else None
    avg_odds = buy["settled_odds"].mean()
    avg_odds = float(avg_odds) if pd.notna(avg_odds) else None
    payout_sum = (buy["hit"].astype(int) * buy["settled_odds"].fillna(0.0)).sum()
    roi = (float(payout_sum) / buy_count) if buy_count > 0 else None

    return {
        "ev_threshold": threshold,
        "exclude_risk_flag": exclude_risk,
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": hit_rate,
        "roi": roi,
        "avg_odds": avg_odds,
    }


def main():
    parser = argparse.ArgumentParser(description="Simulate BUY metrics for multiple EV thresholds")
    parser.add_argument("--input", default="reports/backtest_race_results.csv", help="Input race-level backtest CSV")
    parser.add_argument("--thresholds", default="0.8,1.0,1.1,1.2,1.5,2.0,3.0,5.0", help="Comma separated EV thresholds")
    parser.add_argument("--include-risk", action="store_true", help="Also output a variant that keeps risk_flag=True rows")
    parser.add_argument("--output", default="reports/ev_threshold_comparison.csv", help="Output comparison CSV")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    df = pd.read_csv(input_path)
    thresholds = parse_thresholds(args.thresholds)

    rows = []
    for th in thresholds:
        rows.append(simulate(df, th, exclude_risk=True))
        if args.include_risk:
            rows.append(simulate(df, th, exclude_risk=False))

    out = pd.DataFrame(rows).sort_values(["ev_threshold", "exclude_risk_flag"]).reset_index(drop=True)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Threshold comparison saved: {out_path}")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
