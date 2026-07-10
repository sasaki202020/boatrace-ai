from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "reports" / "daily" / "odds_refresh_summary.csv"
PHASES = ["morning", "late", "final"]


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _normalize_phase(value: Any) -> str:
    text = str(value).strip().lower()
    if text in {"morning", "late", "final"}:
        return text
    return ""


def _summarize_date(rows: list[dict[str, Any]], target_date: str) -> dict[str, Any]:
    filtered = [row for row in rows if str(row.get("date", "")).strip() == target_date]
    phases_by_date: dict[str, list[str]] = defaultdict(list)
    for row in filtered:
        phase = _normalize_phase(row.get("phase", ""))
        if phase:
            phases_by_date[target_date].append(phase)

    phases = phases_by_date.get(target_date, [])
    unique_phases = sorted(set(phases), key=lambda value: PHASES.index(value))
    missing = [phase for phase in PHASES if phase not in unique_phases]
    duplicates = sorted({phase for phase in unique_phases if phases.count(phase) > 1}, key=lambda value: PHASES.index(value))

    return {
        "date": target_date,
        "rows": len(filtered),
        "phases": unique_phases,
        "missing": missing,
        "duplicates": duplicates,
        "status": "ok" if not missing and not duplicates and len(unique_phases) == 3 else "missing",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether a given date has morning/late/final rows in odds_refresh_summary.csv.")
    parser.add_argument("--date", required=True, help="Target date (YYYY-MM-DD).")
    parser.add_argument("--summary-path", default=str(SUMMARY_PATH), help="Path to odds_refresh_summary.csv.")
    args = parser.parse_args()

    summary_path = Path(args.summary_path)
    rows = _load_rows(summary_path)
    if not rows:
        print("No summary data available.")
        return

    result = _summarize_date(rows, str(args.date))
    print("Summary path: reports/daily/odds_refresh_summary.csv")
    print(f"date: {result['date']}")
    print(f"rows: {result['rows']}")
    print(f"phases: {', '.join(result['phases']) if result['phases'] else '-'}")
    print(f"missing: {', '.join(result['missing']) if result['missing'] else '-'}")
    print(f"duplicates: {', '.join(result['duplicates']) if result['duplicates'] else '-'}")
    print(f"status: {result['status']}")


if __name__ == "__main__":
    main()
