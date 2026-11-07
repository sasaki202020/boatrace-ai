from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from src.pipeline.pipeline_utils import REPORTS_ROOT, ROOT, parse_date, read_json


def _safe_value(value):
    if value is None:
        return ""
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Export one daily review row for 7-day operations.")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument(
        "--touched",
        default="",
        help="What was changed that day. Leave empty if no change was made.",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Optional note for the day.",
    )
    args = parser.parse_args()

    target_date = parse_date(args.date, default=date.today())
    day_dir = REPORTS_ROOT / target_date.isoformat()

    daily_summary = read_json(day_dir / "daily_summary.json")
    improvement_report = read_json(day_dir / "improvement_report.json")
    exacta_summary = read_json(day_dir / "exacta_proxy_summary.json")

    if not daily_summary:
        raise SystemExit(f"daily_summary.json not found for {target_date.isoformat()}: {day_dir}")

    top_reasons = daily_summary.get("top_reasons", [])
    top_reason = top_reasons[0]["reason"] if top_reasons else ""

    top_candidates = improvement_report.get("top_candidates", [])
    top_improvement = top_candidates[0]["candidate"] if top_candidates else ""

    exacta_block = exacta_summary.get("exacta", {}) if exacta_summary else daily_summary.get("exacta_recent30", {})

    row = {
        "date": target_date.isoformat(),
        "trifecta_buy_count": _safe_value(daily_summary.get("buy_count")),
        "trifecta_hit_count": _safe_value(daily_summary.get("hit_count")),
        "trifecta_roi": _safe_value(daily_summary.get("roi")),
        "trifecta_exact": _safe_value(daily_summary.get("exact_count")),
        "trifecta_top5": _safe_value(daily_summary.get("top5_count")),
        "trifecta_avg_rank": _safe_value(daily_summary.get("avg_rank")),
        "exacta_buy_count": _safe_value(exacta_block.get("buy_count", "")),
        "exacta_hit_count": _safe_value(exacta_block.get("hit_count")),
        "exacta_roi": _safe_value(exacta_block.get("roi")),
        "main_rejection_reason": top_reason,
        "improvement_top1": top_improvement,
        "touched_change": args.touched,
        "note": args.note,
    }

    out_csv = REPORTS_ROOT / "seven_day_review_log.csv"
    existing = pd.read_csv(out_csv) if out_csv.exists() else pd.DataFrame()
    existing = existing[existing.get("date", pd.Series(dtype=str)).astype(str) != target_date.isoformat()].copy() if not existing.empty else existing
    updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    updated = updated.sort_values("date").reset_index(drop=True)
    updated.to_csv(out_csv, index=False, encoding="utf-8-sig")

    print(f"[saved] {out_csv}")
    print(pd.DataFrame([row]).to_string(index=False))


if __name__ == "__main__":
    main()
