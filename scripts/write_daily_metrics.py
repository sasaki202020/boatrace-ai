from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_MONITOR_ROOT = Path("reports/monitoring")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--skip-decisions",
        default="data/strategy_outputs/skip_decisions.csv",
        help="Path to skip_decisions.csv",
    )
    parser.add_argument(
        "--output-root",
        default="reports/daily",
        help="Root dir for daily reports",
    )
    parser.add_argument("--real-odds-available", type=int, default=None)
    parser.add_argument("--pending-unpublished", type=int, default=None)
    parser.add_argument("--improvement-report-top1", default=None)
    return parser.parse_args()


def build_monitoring_rows(payload: dict[str, object]) -> pd.DataFrame:
    row = {
        "date": payload["date"],
        "total_count": int(payload.get("total_count", 0) or 0),
        "buy_count": int(payload.get("buy_count", 0) or 0),
        "buy_hit_count": int(payload.get("buy_hit_count", 0) or 0),
        "buy_hit_rate": float(payload.get("buy_hit_rate", 0.0) or 0.0),
        "avg_odds": float(payload.get("avg_odds", 0.0) or 0.0),
        "pending_count": int(payload.get("pending_count", 0) or 0),
        "skip_count": int(payload.get("skip_count", 0) or 0),
        "real_odds_available": int(payload.get("real_odds_available", 0) or 0),
        "pending_unpublished": int(payload.get("pending_unpublished", 0) or 0),
        "improvement_report_top1": payload.get("improvement_report_top1"),
    }
    total = dict(row)
    total["date"] = "TOTAL"
    return pd.DataFrame([row, total])


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows_"
    cols = list(df.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = []
    for _, row in df.iterrows():
        values = ["" if pd.isna(row[c]) else str(row[c]) for c in cols]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *rows])


def main() -> None:
    args = parse_args()
    date_str = str(args.date)

    out_dir = Path(args.output_root) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    skip_path = Path(args.skip_decisions)
    if not skip_path.exists():
        raise FileNotFoundError(f"skip_decisions not found: {skip_path}")

    df = pd.read_csv(skip_path)
    if "date" not in df.columns or "decision" not in df.columns:
        raise ValueError("skip_decisions.csv must contain date and decision columns")

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_df = df[df["date"] == date_str].copy()

    if "hit" in day_df.columns:
        day_df["hit"] = pd.to_numeric(day_df["hit"], errors="coerce").fillna(0)
    else:
        day_df["hit"] = 0

    if "odds" in day_df.columns:
        day_df["odds"] = pd.to_numeric(day_df["odds"], errors="coerce")
    else:
        day_df["odds"] = pd.NA

    buy_df = day_df[day_df["decision"] == "BUY"].copy()

    payload = {
        "date": date_str,
        "total_count": int(len(day_df)),
        "buy_count": int((day_df["decision"] == "BUY").sum()),
        "buy_hit_count": int(buy_df["hit"].sum()) if not buy_df.empty else 0,
        "buy_hit_rate": round(float(buy_df["hit"].mean()), 4) if not buy_df.empty else 0.0,
        "avg_odds": (
            round(float(buy_df["odds"].dropna().mean()), 4)
            if not buy_df["odds"].dropna().empty
            else 0.0
        ),
        "pending_count": int((day_df["decision"] == "PENDING").sum()),
        "skip_count": int((day_df["decision"] == "SKIP").sum()),
        "real_odds_available": args.real_odds_available,
        "pending_unpublished": args.pending_unpublished,
        "improvement_report_top1": args.improvement_report_top1,
    }

    json_path = out_dir / "daily_metrics.json"
    csv_path = out_dir / "daily_metrics.csv"
    monitor_dir = DEFAULT_MONITOR_ROOT
    monitor_dir.mkdir(parents=True, exist_ok=True)
    monitor_csv_path = monitor_dir / "daily_monitoring_summary.csv"
    monitor_json_path = monitor_dir / "daily_monitoring_summary.json"
    monitor_md_path = monitor_dir / "daily_monitoring_summary.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame([payload]).to_csv(csv_path, index=False, encoding="utf-8")
    monitoring_df = build_monitoring_rows(payload)
    monitoring_df.to_csv(monitor_csv_path, index=False, encoding="utf-8")
    monitor_json_path.write_text(
        json.dumps(
            {
                "generatedAt": pd.Timestamp.now().isoformat(),
                "rows": monitoring_df.to_dict(orient="records"),
                "source": str(csv_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monitor_md_path.write_text(dataframe_to_markdown(monitoring_df), encoding="utf-8")

    print(f"[daily_metrics] wrote: {json_path}")
    print(f"[daily_metrics] wrote: {csv_path}")
    print(f"[daily_metrics] wrote: {monitor_csv_path}")


if __name__ == "__main__":
    main()
