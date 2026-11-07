from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> int:
    print(f"RUN: {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run morning / late / final odds refresh in one shot.")
    parser.add_argument("--date", required=True, help="Target date (YYYY-MM-DD).")
    args = parser.parse_args()

    phases = [
        ("morning", ["--refresh"]),
        ("late", ["--refresh", "--pending-only"]),
        ("final", ["--refresh", "--pending-only"]),
    ]

    for phase, extra_args in phases:
        command = [
            sys.executable,
            "-m",
            "src.pipeline.run_daily_odds_refresh",
            "--date",
            args.date,
            "--phase",
            phase,
            *extra_args,
        ]
        returncode = _run(command)
        if returncode != 0:
            return returncode

    leaderboard_cmd = [sys.executable, str(ROOT / "scripts" / "show_phase_leaderboard.py")]
    returncode = _run(leaderboard_cmd)
    if returncode != 0:
        return returncode

    completeness_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "check_phase_completeness.py"),
        "--date",
        args.date,
    ]
    returncode = _run(completeness_cmd)
    if returncode != 0:
        return returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
