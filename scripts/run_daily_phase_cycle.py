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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one odds refresh phase and verify completeness/leaderboard.")
    parser.add_argument("--phase", required=True, choices=["morning", "late", "final"], help="Target refresh phase.")
    parser.add_argument("--date", default="", help="Target date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    target_date = args.date.strip() or _today_iso()
    extra_args = ["--refresh"]
    if args.phase in {"late", "final"}:
        extra_args.append("--pending-only")

    refresh_cmd = [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_odds_refresh",
        "--date",
        target_date,
        "--phase",
        args.phase,
        *extra_args,
    ]
    returncode = _run(refresh_cmd)
    if returncode != 0:
        return returncode

    completeness_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "check_phase_completeness.py"),
        "--date",
        target_date,
    ]
    returncode = _run(completeness_cmd)
    if returncode != 0:
        return returncode

    leaderboard_cmd = [sys.executable, str(ROOT / "scripts" / "show_phase_leaderboard.py")]
    returncode = _run(leaderboard_cmd)
    if returncode != 0:
        return returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
