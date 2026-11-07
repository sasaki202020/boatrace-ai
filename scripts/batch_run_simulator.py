from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timedelta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--stake", type=int, default=100)
    return parser.parse_args()


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError("start-date must be <= end-date")
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def run_cmd(cmd: list[str]) -> int:
    print(f"\n[RUN] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    print(f"[EXIT] code={result.returncode}")
    return int(result.returncode)


def main() -> None:
    args = parse_args()
    dates = _date_range(args.start_date, args.end_date)

    summary_rows: list[dict] = []
    for date_str in dates:
        build_cmd = [
            "python",
            "scripts/build_buy_tickets.py",
            "--date",
            date_str,
        ]
        build_exit = run_cmd(build_cmd)

        sim_cmd = [
            "python",
            "scripts/run_simulator_for_date.py",
            "--date",
            date_str,
            "--stake",
            str(args.stake),
        ]
        sim_exit = run_cmd(sim_cmd)

        summary_rows.append(
            {
                "date": date_str,
                "build_buy_tickets_exit": build_exit,
                "run_simulator_exit": sim_exit,
            }
        )

    print("\n=== Batch Summary ===")
    for row in summary_rows:
        print(row)

    agg_cmd = [
        "python",
        "scripts/aggregate_simulation_reports.py",
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
    ]
    run_cmd(agg_cmd)

    weekly_cmd = [
        "python",
        "scripts/build_weekly_report.py",
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
    ]
    run_cmd(weekly_cmd)


if __name__ == "__main__":
    main()
