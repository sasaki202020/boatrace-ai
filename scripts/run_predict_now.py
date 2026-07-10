from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> int:
    print(f"RUN: {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    return int(completed.returncode)


def _today_iso() -> str:
    return date.today().isoformat()


def _entry_path(target_date: str) -> Path:
    y, m, d = target_date.split("-")
    return ROOT / "data" / "raw" / "official" / "entries" / f"B{y[2:]}{m}{d}.TXT"


def _summary_path(target_date: str) -> Path:
    return ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"


def _print_prediction_summary(path: Path) -> None:
    if not path.exists():
        print(f"Prediction file not found: {path}")
        return

    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        print("No prediction rows available.")
        return

    counts = Counter(str(row.get("decision", "")).upper() for row in rows)
    print("Prediction summary")
    print(f"- BUY: {counts.get('BUY', 0)}")
    print(f"- PENDING: {counts.get('PENDING', 0)}")
    print(f"- SKIP: {counts.get('SKIP', 0)}")

    buy_rows = [row for row in rows if str(row.get("decision", "")).upper() == "BUY"]
    buy_rows.sort(key=lambda row: float(row.get("buy_final_score") or 0.0), reverse=True)
    print()
    print("Top BUY rows")
    if not buy_rows:
        print("- none")
        return
    for row in buy_rows[:10]:
        print(
            f"- {row.get('race_id', '-')} / {row.get('recommended_trifecta', '-')} / "
            f"odds={row.get('odds', '-')} / ev={row.get('ev', '-')} / reason={row.get('reason', '-')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pre-race prediction as soon as entry data is ready.")
    parser.add_argument("--date", default="", help="Target date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    target_date = args.date.strip() or _today_iso()
    entry_path = _entry_path(target_date)
    if not entry_path.exists():
        print(f"Entry file not ready: {entry_path}")
        return 0

    pre_race_cmd = [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_pre_race",
        "--date",
        target_date,
    ]
    returncode = _run(pre_race_cmd)
    if returncode != 0:
        return returncode

    _print_prediction_summary(_summary_path(target_date))
    dashboard_cmd = [sys.executable, str(ROOT / "scripts" / "show_daily_ops_dashboard.py")]
    _run(dashboard_cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
