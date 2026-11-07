from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date

from src.pipeline.pipeline_utils import append_log, iso_now, log_file_for, parse_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a late-day odds refresh pass.")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument(
        "--wait-minutes",
        type=float,
        default=0.0,
        help="Optional wait before running the refresh. Useful for late afternoon refreshes.",
    )
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument(
        "--pending-only",
        action="store_true",
        default=True,
        help="Only retry races previously classified as pending_unpublished.",
    )
    args = parser.parse_args()

    target_date = parse_date(args.date, default=date.today())
    log_path = log_file_for("odds_refresh_late", target_date)
    append_log(
        log_path,
        f"[run:start] pipeline=odds_refresh_late date={target_date.isoformat()} wait_minutes={args.wait_minutes}",
    )

    if args.wait_minutes > 0:
        print(f"[wait] sleeping for {args.wait_minutes:.1f} minutes before refresh...")
        append_log(log_path, f"[wait] start={iso_now()} minutes={args.wait_minutes}")
        time.sleep(args.wait_minutes * 60.0)
        append_log(log_path, f"[wait] end={iso_now()}")

    cmd = [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_odds_refresh",
        "--date",
        target_date.isoformat(),
        "--phase",
        "late",
        "--refresh",
        "--delay",
        str(args.delay),
    ]
    if args.pending_only:
        cmd.extend(["--pending-only"])
    print("[run] " + " ".join(cmd))
    append_log(log_path, f"[run] cmd={' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    append_log(
        log_path,
        f"[run:end] pipeline=odds_refresh_late status={'ok' if result.returncode == 0 else 'failed'} returncode={result.returncode}",
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
