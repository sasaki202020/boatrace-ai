from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.pipeline.odds_time_series_ops import DAILY_SERIES_DIR, YEARLY_SERIES_DIR, build_summary_tables


def _write_markdown(summary: dict, output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Odds Time Series Summary")
    lines.append("")
    lines.append(f"- generated_at: {summary.get('generated_at', '')}")
    lines.append(f"- phase_rows: {summary.get('phase_rows', 0)}")
    lines.append(f"- venue_rows: {summary.get('venue_rows', 0)}")
    lines.append("")

    daily = summary.get("daily_comparison", [])
    phase_table = summary.get("phase_table", [])
    if phase_table:
        lines.append("## Phase Table")
        lines.append("")
        lines.append("| date | phase | real_odds_available | pending_unpublished | real_odds_missing_fetch_failed | real_odds_missing_never_fetched | buy_count | measured_at | run_status |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for row in phase_table:
            lines.append(
                f"| {row.get('date', '')} | {row.get('phase', '')} | {row.get('real_odds_available', 0)} | "
                f"{row.get('pending_unpublished', 0)} | {row.get('real_odds_missing_fetch_failed', 0)} | "
                f"{row.get('real_odds_missing_never_fetched', 0)} | {row.get('buy_count', 0)} | "
                f"{row.get('measured_at', '')} | {row.get('run_status', '')} |"
            )
        lines.append("")

    if daily:
        lines.append("## Daily Comparison")
        lines.append("")
        lines.append("| date | window | real_odds_available_delta | pending_unpublished_delta | buy_count_delta |")
        lines.append("| --- | --- | ---: | ---: | ---: |")
        for row in daily:
            lines.append(
                f"| {row.get('date', '')} | {row.get('window', '')} | {row.get('real_odds_available_delta', 0)} | "
                f"{row.get('pending_unpublished_delta', 0)} | {row.get('buy_count_delta', 0)} |"
            )
        lines.append("")

    pair_summary = summary.get("pair_summary", {})
    if pair_summary:
        lines.append("## Pair Summary")
        lines.append("")
        lines.append("| window | days | avg_real_odds_available_delta | avg_pending_unpublished_delta | days_with_real_odds_available_increase | days_with_pending_unpublished_decrease |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for window, row in pair_summary.items():
            lines.append(
                f"| {window} | {row.get('days', 0)} | {row.get('avg_real_odds_available_delta', 0)} | "
                f"{row.get('avg_pending_unpublished_delta', 0)} | {row.get('days_with_real_odds_available_increase', 0)} | "
                f"{row.get('days_with_pending_unpublished_decrease', 0)} |"
            )
        lines.append("")

    phase_aggregate = summary.get("phase_aggregate", [])
    if phase_aggregate:
        lines.append("## Phase Aggregate")
        lines.append("")
        lines.append("| phase | days | avg_real_odds_available | avg_pending_unpublished | avg_buy_count |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in phase_aggregate:
            lines.append(
                f"| {row.get('phase', '')} | {row.get('days', 0)} | {row.get('avg_real_odds_available', 0)} | "
                f"{row.get('avg_pending_unpublished', 0)} | {row.get('avg_buy_count', 0)} |"
            )
        lines.append("")

    venue_summary = summary.get("venue_summary", [])
    if venue_summary:
        lines.append("## Venue Summary")
        lines.append("")
        lines.append("| jcd | stadium | avg_real_odds_available | avg_pending_unpublished | avg_buy_count |")
        lines.append("| --- | --- | ---: | ---: | ---: |")
        for row in venue_summary:
            lines.append(
                f"| {row.get('jcd', '')} | {row.get('stadium', '')} | {row.get('avg_real_odds_available', 0)} | "
                f"{row.get('avg_pending_unpublished', 0)} | {row.get('avg_buy_count', 0)} |"
            )
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize odds time-series observations.")
    parser.add_argument("--input", default=str(DAILY_SERIES_DIR / "odds_time_series.csv"))
    parser.add_argument("--output-dir", default=str(YEARLY_SERIES_DIR))
    args = parser.parse_args()

    summary = build_summary_tables()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    phase_path = Path(args.input)
    phase_df = pd.read_csv(phase_path, low_memory=False) if phase_path.exists() else pd.DataFrame()
    if not phase_df.empty:
        phase_df.to_csv(output_dir / "summary.csv", index=False)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(summary, output_dir / "summary.md")

    venue_path = DAILY_SERIES_DIR / "odds_time_series_venue.csv"
    venue_df = pd.read_csv(venue_path, low_memory=False) if venue_path.exists() else pd.DataFrame()
    if not venue_df.empty:
        venue_df.to_csv(output_dir / "venue_summary.csv", index=False)

    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
