from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

from src.pipeline.pipeline_utils import parse_date


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily operation checklist as a single command.")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument(
        "--phase",
        choices=["morning", "late", "night", "full"],
        default="full",
        help="Which part of the checklist to run.",
    )
    parser.add_argument(
        "--wait-minutes",
        type=float,
        default=0.0,
        help="Optional wait before late refresh. Used only when phase includes late refresh.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay forwarded to the underlying odds refresh and pre-race pipelines.",
    )
    return parser.parse_args()


def run_cmd(label: str, cmd: list[str]) -> int:
    print(f"\n[{label}] RUN {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, cwd=str(ROOT))
    print(f"[{label}] EXIT {result.returncode}")
    return int(result.returncode)


def build_pre_race_cmd(target_date: str, delay: float) -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_pre_race",
        "--date",
        target_date,
        "--delay",
        str(delay),
    ]


def build_late_refresh_cmd(target_date: str, wait_minutes: float, delay: float) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_odds_refresh_late",
        "--date",
        target_date,
        "--delay",
        str(delay),
    ]
    if wait_minutes > 0:
        cmd.extend(["--wait-minutes", str(wait_minutes)])
    return cmd


def build_post_race_cmd(target_date: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_post_race",
        "--date",
        target_date,
    ]


def main() -> None:
    args = parse_args()
    target_date = parse_date(args.date, default=date.today()).isoformat()

    steps: list[tuple[str, list[str]]] = []
    if args.phase in {"morning", "full"}:
        steps.append(("pre_race", build_pre_race_cmd(target_date, args.delay)))
    if args.phase in {"late", "full"}:
        steps.append(("late_refresh", build_late_refresh_cmd(target_date, args.wait_minutes, args.delay)))
    if args.phase in {"night", "full"}:
        steps.append(("post_race", build_post_race_cmd(target_date)))

    if not steps:
        print("No steps to run.")
        raise SystemExit(0)

    overall = 0
    for label, cmd in steps:
        code = run_cmd(label, cmd)
        if code != 0:
            overall = code
            break

    raise SystemExit(overall)


if __name__ == "__main__":
    main()
