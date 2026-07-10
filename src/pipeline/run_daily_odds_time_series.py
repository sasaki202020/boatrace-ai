from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

from src.pipeline.odds_time_series_ops import (
    ROOT,
    append_phase_rows,
    build_phase_row,
    build_summary_tables,
    build_venue_snapshot,
    phase_command,
    rebuild_global_series,
)
from src.pipeline.pipeline_utils import append_log, iso_now, log_file_for, parse_date


def _report_dir(target_date: date) -> Path:
    return ROOT / "reports" / "daily" / target_date.isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and record odds time-series observations.")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--phase", choices=["morning", "late_refresh", "final_refresh"], required=True)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--wait-minutes", type=float, default=0.0)
    parser.add_argument("--record-only", action="store_true", help="Only record current artifacts without running a pipeline.")
    args = parser.parse_args()

    target_date = parse_date(args.date, default=date.today())
    measured_at = datetime.now().isoformat(timespec="seconds")
    log_path = log_file_for("odds_time_series", target_date)
    append_log(
        log_path,
        f"[run:start] pipeline=odds_time_series date={target_date.isoformat()} phase={args.phase} record_only={args.record_only}",
    )

    cmd: list[str] = []
    run_status = "ok"
    returncode = 0
    pipeline_report_path = ""
    if not args.record_only:
        cmd = phase_command(args.phase, target_date, args.delay, wait_minutes=args.wait_minutes)
        append_log(log_path, f"[run] cmd={' '.join(cmd)}")
        print("[run] " + " ".join(cmd))
        result = subprocess.run(cmd, cwd=str(ROOT), check=False)
        returncode = int(result.returncode)
        run_status = "ok" if returncode == 0 else "failed"
    else:
        append_log(log_path, "[run] record-only mode")

    report_dir = _report_dir(target_date)
    if args.phase == "morning":
        pipeline_report_path = str(report_dir / "pre_race_run.json")
    else:
        pipeline_report_path = str(report_dir / "odds_refresh_run.json")

    skip_path = report_dir / "skip_decisions.csv"

    phase_row = build_phase_row(
        target_date=target_date,
        phase=args.phase,
        measured_at=measured_at,
        skip_path=skip_path,
        run_status=run_status,
        pipeline_report_path=Path(pipeline_report_path) if pipeline_report_path else None,
        command=cmd,
    )
    venue_rows = build_venue_snapshot(skip_path, args.phase, measured_at, target_date)
    phase_path, venue_path, snapshot_path = append_phase_rows(
        target_date=target_date,
        phase_row=phase_row,
        venue_rows=venue_rows,
    )
    root_phase_path, root_venue_path = rebuild_global_series()
    summary = build_summary_tables()
    summary_dir = ROOT / "reports" / "yearly_backtest" / "odds_time_series"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    append_log(
        log_path,
        f"[run:end] pipeline=odds_time_series phase={args.phase} status={run_status} returncode={returncode}",
    )

    output = {
        "status": run_status,
        "returncode": returncode,
        "target_date": target_date.isoformat(),
        "phase": args.phase,
        "measured_at": measured_at,
        "phase_row": phase_row,
        "phase_path": str(phase_path),
        "venue_path": str(venue_path),
        "snapshot_path": str(snapshot_path),
        "root_phase_path": str(root_phase_path),
        "root_venue_path": str(root_venue_path),
        "summary_path": str(summary_dir / "summary.json"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()
