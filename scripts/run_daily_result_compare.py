from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> int:
    print(f"RUN: {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    return int(completed.returncode)


def _today_iso() -> str:
    return date.today().isoformat()


def _results_path(target_date: str) -> Path:
    # YYYY-MM-DD -> KYYMMDD.TXT
    year, month, day = target_date.split("-")
    return ROOT / "data" / "raw" / "official" / "results" / f"K{year[2:]}{month}{day}.TXT"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run post-race evaluation automatically when today's official results are available."
    )
    parser.add_argument("--date", default="", help="Target date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    target_date = args.date.strip() or _today_iso()
    result_path = _results_path(target_date)
    if not result_path.exists():
        print(f"Result file not ready: {result_path}")
        return 0

    post_race_cmd = [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_post_race",
        "--date",
        target_date,
    ]
    returncode = _run(post_race_cmd)
    if returncode != 0:
        return returncode

    dashboard_cmd = [sys.executable, str(ROOT / "scripts" / "show_daily_ops_dashboard.py")]
    returncode = _run(dashboard_cmd)
    if returncode != 0:
        return returncode

    leaderboard_cmd = [sys.executable, str(ROOT / "scripts" / "show_phase_leaderboard.py")]
    returncode = _run(leaderboard_cmd)
    if returncode != 0:
        return returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
