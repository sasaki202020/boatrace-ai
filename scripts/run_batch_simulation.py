#!/usr/bin/env python3
from __future__ import annotations

"""
run_batch_simulation.py

TARGET 日をまとめて回すための最小バッチ実装。
目的は、比較対象日だけを拾って raw / calibrated の比較を同条件で回し、
CSV / JSON / failure 一覧を残すこと。
"""

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_MODES = {"target-only", "include-hold", "explicit-dates"}
VALID_COMPARE_STATUS = {"TARGET", "HOLD", "EXCLUDE"}

DEFAULT_SOURCE_CSV = Path("reports/simulation/comparison_target_days.csv")
DEFAULT_SOURCE_MD = Path("COMPARISON_TARGET_DAYS.md")
DEFAULT_OUTPUT_DIR = Path("reports/simulation")
DEFAULT_COMPARE_INPUT_PATH = Path("data/strategy_outputs/skip_decisions_with_calibrated_prob.csv")


@dataclass
class DayRecord:
    date: str
    compare_status: str
    result_txt_ready: bool = False
    raw_incomplete: bool = False
    simulator_ok: bool = False
    note: str = ""


@dataclass
class DayResult:
    date: str
    status: str
    reference_only: bool
    raw_buy: Optional[int] = None
    cal_buy: Optional[int] = None
    raw_hit: Optional[int] = None
    cal_hit: Optional[int] = None
    raw_roi: Optional[float] = None
    cal_roi: Optional[float] = None
    rank_swap_count: Optional[int] = None
    simulator_ok: bool = False
    compare_ok: bool = False
    note: str = ""


@dataclass
class FailureRecord:
    date: str
    failure_type: str
    step: str
    message: str
    retryable: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run batch simulation for target days.")
    parser.add_argument("--mode", choices=sorted(VALID_MODES), default="target-only")
    parser.add_argument("--dates", default="", help="Comma-separated dates for explicit-dates mode.")
    parser.add_argument("--target-dates", default="", help="Alias for --dates.")
    parser.add_argument("--limit", type=int, default=0, help="Run only the most recent N eligible days.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip dates already present in batch_results.csv.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop immediately on first failure.")
    parser.add_argument("--dry-run", action="store_true", help="Show selected dates only.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-csv", type=Path, default=DEFAULT_SOURCE_CSV)
    parser.add_argument("--source-md", type=Path, default=DEFAULT_SOURCE_MD)
    parser.add_argument("--compare-input-path", type=Path, default=DEFAULT_COMPARE_INPUT_PATH)
    parser.add_argument("--stake", type=int, default=100)
    parser.add_argument("--buy-min-ev", type=float, default=0.1)
    parser.add_argument("--buy-min-prob", type=float, default=0.0)
    parser.add_argument("--max-buy-count", type=int, default=3)
    return parser


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_target_days_from_csv(source_csv: Path) -> list[DayRecord]:
    rows: list[DayRecord] = []
    with source_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            compare_status = (row.get("status") or row.get("compare_status") or "").strip().upper()
            if compare_status not in VALID_COMPARE_STATUS:
                raise ValueError(f"invalid compare status for date={row.get('date')}: {compare_status}")

            date = (row.get("date") or "").strip()
            if not date:
                raise ValueError("date is empty in source csv")

            rows.append(
                DayRecord(
                    date=normalize_date_str(date),
                    compare_status=compare_status,
                    result_txt_ready=parse_bool(row.get("result_txt_ready", "")),
                    raw_incomplete=parse_bool(row.get("raw_incomplete", "")),
                    simulator_ok=parse_bool(row.get("simulator_ok", "")),
                    note=(row.get("note") or row.get("reason") or "").strip(),
                )
            )
    return rows


def load_target_days_from_markdown(source_md: Path) -> list[DayRecord]:
    if not source_md.exists():
        raise FileNotFoundError(f"source file not found: {source_md}")

    lines = source_md.read_text(encoding="utf-8").splitlines()
    header: list[str] | None = None
    rows: list[DayRecord] = []

    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        lowered = [cell.lower() for cell in cells]
        if header is None and "date" in lowered and "status" in lowered:
            header = lowered
            continue
        if header is None:
            continue
        if all(c.startswith("---") or c == "" for c in cells):
            continue

        if len(cells) < len(header):
            continue
        row = {header[i]: cells[i] for i in range(len(header))}
        date_value = row.get("date", "").strip()
        if not date_value or date_value.startswith("yyyy"):
            continue

        compare_status = row.get("status", "").strip().upper()
        if compare_status not in VALID_COMPARE_STATUS:
            continue

        rows.append(
            DayRecord(
                date=normalize_date_str(date_value),
                compare_status=compare_status,
                result_txt_ready=parse_bool(row.get("result_txt_ready", "")),
                raw_incomplete=parse_bool(row.get("raw_incomplete", "")),
                simulator_ok=parse_bool(row.get("simulator_ok", "")),
                note=(row.get("reason", "") or row.get("action", "")).strip(),
            )
        )

    return rows


def load_target_days(source_csv: Path, source_md: Path) -> list[DayRecord]:
    if source_csv.exists():
        return load_target_days_from_csv(source_csv)
    return load_target_days_from_markdown(source_md)


def filter_days(
    records: list[DayRecord],
    mode: str,
    explicit_dates: list[str],
    limit: int,
) -> list[DayRecord]:
    if mode == "explicit-dates":
        wanted = set(explicit_dates)
        filtered = [r for r in records if r.date in wanted]
    elif mode == "target-only":
        filtered = [r for r in records if r.compare_status == "TARGET"]
    elif mode == "include-hold":
        filtered = [r for r in records if r.compare_status in {"TARGET", "HOLD"}]
    else:
        raise ValueError(f"unsupported mode: {mode}")

    filtered.sort(key=lambda x: x.date)
    if limit > 0:
        filtered = filtered[-limit:]
    return filtered


def load_existing_result_dates(result_csv: Path) -> set[str]:
    if not result_csv.exists():
        return set()
    with result_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return {str(row.get("date", "")).strip() for row in reader if row.get("date")}


def run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


def run_compare_for_date(
    date: str,
    compare_input_path: Path,
    stake: int,
    buy_min_ev: float,
    buy_min_prob: float,
    max_buy_count: int,
) -> tuple[bool, str, Path]:
    cmd = [
        sys.executable,
        "scripts/compare_raw_vs_calibrated.py",
        "--date",
        date,
        "--input-path",
        str(compare_input_path),
        "--stake",
        str(stake),
        "--buy-min-ev",
        str(buy_min_ev),
        "--buy-min-prob",
        str(buy_min_prob),
        "--max-buy-count",
        str(max_buy_count),
    ]
    proc = run_subprocess(cmd)
    json_path = REPO_ROOT / "reports" / "comparison" / f"raw_vs_calibrated_{date}.json"
    message = (proc.stdout if proc.returncode == 0 else proc.stderr).strip()
    return proc.returncode == 0, message[-1000:], json_path


def extract_metrics_from_compare_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "raw_buy": None,
            "cal_buy": None,
            "raw_hit": None,
            "cal_hit": None,
            "raw_roi": None,
            "cal_roi": None,
            "rank_swap_count": None,
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "raw_buy": None,
            "cal_buy": None,
            "raw_hit": None,
            "cal_hit": None,
            "raw_roi": None,
            "cal_roi": None,
            "rank_swap_count": None,
        }

    raw = payload.get("raw_summary", {}) or {}
    cal = payload.get("calibrated_summary", {}) or {}
    rows = payload.get("comparison_rows", []) or []
    rank_swap_count = None
    for row in rows:
        if str(row.get("metric", "")) == "hit_rate":
            rank_swap_count = None
            break
    return {
        "raw_buy": _as_int(raw.get("buy_count")),
        "cal_buy": _as_int(cal.get("buy_count")),
        "raw_hit": _as_int(raw.get("hit_count")),
        "cal_hit": _as_int(cal.get("hit_count")),
        "raw_roi": _as_float(raw.get("roi")),
        "cal_roi": _as_float(cal.get("roi")),
        "rank_swap_count": rank_swap_count,
    }


def validate_day_inputs(record: DayRecord) -> Optional[FailureRecord]:
    if record.compare_status == "EXCLUDE":
        return FailureRecord(
            date=record.date,
            failure_type="INVALID_STATUS",
            step="precheck",
            message="EXCLUDE day is not eligible",
            retryable=False,
        )
    if record.compare_status == "HOLD":
        return FailureRecord(
            date=record.date,
            failure_type="HOLD_REFERENCE",
            step="precheck",
            message="HOLD day kept as reference only",
            retryable=False,
        )
    if not record.result_txt_ready:
        return FailureRecord(
            date=record.date,
            failure_type="MISSING_RESULT_TXT",
            step="precheck",
            message="result txt not ready",
            retryable=True,
        )
    if record.raw_incomplete:
        return FailureRecord(
            date=record.date,
            failure_type="RAW_INCOMPLETE",
            step="precheck",
            message="raw_incomplete=yes",
            retryable=True,
        )
    return None


def summarize_day_result(
    record: DayRecord,
    compare_input_path: Path,
    stake: int,
    buy_min_ev: float,
    buy_min_prob: float,
    max_buy_count: int,
) -> tuple[DayResult, list[FailureRecord]]:
    failures: list[FailureRecord] = []

    precheck_failure = validate_day_inputs(record)
    if precheck_failure:
        status = "HOLD" if record.compare_status == "HOLD" else "FAIL"
        result = DayResult(
            date=record.date,
            status=status,
            reference_only=(record.compare_status == "HOLD"),
            simulator_ok=False,
            compare_ok=False,
            note=precheck_failure.message,
        )
        if record.compare_status != "HOLD":
            failures.append(precheck_failure)
        return result, failures

    compare_ok, compare_msg, json_path = run_compare_for_date(
        date=record.date,
        compare_input_path=compare_input_path,
        stake=stake,
        buy_min_ev=buy_min_ev,
        buy_min_prob=buy_min_prob,
        max_buy_count=max_buy_count,
    )
    if not compare_ok:
        failures.append(
            FailureRecord(
                date=record.date,
                failure_type="COMPARE_ERROR",
                step="compare",
                message=compare_msg or "comparison failed",
                retryable=True,
            )
        )

    metrics = extract_metrics_from_compare_json(json_path)
    result = DayResult(
        date=record.date,
        status="SUCCESS" if compare_ok else "FAIL",
        reference_only=False,
        raw_buy=metrics["raw_buy"],
        cal_buy=metrics["cal_buy"],
        raw_hit=metrics["raw_hit"],
        cal_hit=metrics["cal_hit"],
        raw_roi=metrics["raw_roi"],
        cal_roi=metrics["cal_roi"],
        rank_swap_count=metrics["rank_swap_count"],
        simulator_ok=True,
        compare_ok=compare_ok,
        note=compare_msg,
    )
    return result, failures


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_batch_results(path: Path, results: list[DayResult]) -> None:
    fieldnames = [
        "date",
        "status",
        "reference_only",
        "raw_buy",
        "cal_buy",
        "raw_hit",
        "cal_hit",
        "raw_roi",
        "cal_roi",
        "rank_swap_count",
        "simulator_ok",
        "compare_ok",
        "note",
    ]
    write_csv(path, [asdict(r) for r in results], fieldnames)


def write_batch_failures(path: Path, failures: list[FailureRecord]) -> None:
    fieldnames = ["date", "failure_type", "step", "message", "retryable"]
    write_csv(path, [asdict(f) for f in failures], fieldnames)


def safe_sum(values: Iterable[Optional[float]]) -> float:
    return float(sum(v for v in values if v is not None))


def _as_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(float(value))
    except Exception:
        return 0


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def write_batch_summary(path: Path, mode: str, results: list[DayResult], failures: list[FailureRecord]) -> None:
    success_results = [r for r in results if r.status == "SUCCESS" and not r.reference_only]
    hold_results = [r for r in results if r.status == "HOLD"]
    fail_results = [r for r in results if r.status == "FAIL"]

    raw_buy = safe_sum(r.raw_buy for r in success_results)
    raw_hit = safe_sum(r.raw_hit for r in success_results)
    cal_buy = safe_sum(r.cal_buy for r in success_results)
    cal_hit = safe_sum(r.cal_hit for r in success_results)
    raw_roi = safe_sum(r.raw_roi for r in success_results)
    cal_roi = safe_sum(r.cal_roi for r in success_results)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "run_count": len(results),
        "success_count": len(success_results),
        "hold_count": len(hold_results),
        "fail_count": len(fail_results),
        "failure_rows": len(failures),
        "raw": {
            "buy": raw_buy,
            "hit": raw_hit,
            "roi_sum": raw_roi,
        },
        "calibrated": {
            "buy": cal_buy,
            "hit": cal_hit,
            "roi_sum": cal_roi,
        },
        "delta": {
            "buy": cal_buy - raw_buy,
            "hit": cal_hit - raw_hit,
        },
        "notes": [
            "ROI aggregation is intentionally simple in the first pass.",
            "reference_only results are excluded from aggregate summary.",
        ],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def write_run_log(path: Path, lines: list[str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    args = build_parser().parse_args()

    args.output_dir = resolve_path(args.output_dir)
    args.source_csv = resolve_path(args.source_csv)
    args.source_md = resolve_path(args.source_md)
    args.compare_input_path = resolve_path(args.compare_input_path)

    ensure_dir(args.output_dir)

    result_csv = args.output_dir / "batch_results.csv"
    failure_csv = args.output_dir / "batch_failures.csv"
    summary_json = args.output_dir / "batch_summary.json"
    run_log_txt = args.output_dir / "batch_run_log.txt"

    explicit_dates_arg = args.dates or args.target_dates
    explicit_dates = [d.strip() for d in explicit_dates_arg.split(",") if d.strip()]
    effective_mode = "explicit-dates" if explicit_dates else args.mode

    log_lines: list[str] = [
        f"[start] {datetime.now().isoformat(timespec='seconds')}",
        f"mode={effective_mode}",
        f"source_csv={args.source_csv}",
        f"source_md={args.source_md}",
        f"compare_input_path={args.compare_input_path}",
    ]

    if effective_mode == "explicit-dates" and not explicit_dates:
        print("--dates is required for explicit-dates mode", file=sys.stderr)
        return 2

    try:
        records = load_target_days(args.source_csv, args.source_md)
    except Exception as e:
        print(f"failed to load target days: {e}", file=sys.stderr)
        return 1

    selected = filter_days(records, effective_mode, [normalize_date_str(d) for d in explicit_dates], args.limit)

    if args.skip_existing:
        existing_dates = load_existing_result_dates(result_csv)
        selected = [r for r in selected if r.date not in existing_dates]
        log_lines.append(f"skip_existing=true skipped_existing_dates={len(existing_dates)}")

    log_lines.append(f"selected_count={len(selected)}")
    log_lines.append("selected_dates=" + ",".join(r.date for r in selected))

    if args.dry_run:
        print("Dry run. Selected dates:")
        for r in selected:
            print(f"- {r.date} [{r.compare_status}]")
        write_run_log(run_log_txt, log_lines + ["[done] dry-run"])
        return 0

    results: list[DayResult] = []
    failures: list[FailureRecord] = []

    for record in selected:
        log_lines.append(f"[day:start] {record.date} status={record.compare_status}")
        try:
            result, day_failures = summarize_day_result(
                record,
                compare_input_path=args.compare_input_path,
                stake=args.stake,
                buy_min_ev=args.buy_min_ev,
                buy_min_prob=args.buy_min_prob,
                max_buy_count=args.max_buy_count,
            )
            results.append(result)
            failures.extend(day_failures)
            log_lines.append(
                f"[day:end] {record.date} result_status={result.status} "
                f"simulator_ok={result.simulator_ok} compare_ok={result.compare_ok}"
            )

            if args.fail_fast and result.status == "FAIL":
                log_lines.append("[abort] fail-fast triggered")
                break
        except Exception as e:
            failures.append(
                FailureRecord(
                    date=record.date,
                    failure_type="UNKNOWN",
                    step="runtime",
                    message=str(e),
                    retryable=True,
                )
            )
            results.append(
                DayResult(
                    date=record.date,
                    status="FAIL",
                    reference_only=(record.compare_status == "HOLD"),
                    simulator_ok=False,
                    compare_ok=False,
                    note=str(e),
                )
            )
            log_lines.append(f"[day:error] {record.date} error={e}")
            if args.fail_fast:
                log_lines.append("[abort] fail-fast triggered")
                break

    write_batch_results(result_csv, results)
    write_batch_failures(failure_csv, failures)
    write_batch_summary(summary_json, effective_mode, results, failures)
    log_lines.append(f"[done] wrote={result_csv},{failure_csv},{summary_json}")
    write_run_log(run_log_txt, log_lines)

    print(f"Saved: {result_csv}")
    print(f"Saved: {failure_csv}")
    print(f"Saved: {summary_json}")
    print(f"Saved: {run_log_txt}")
    print("")
    print("Important TODO:")
    print("1. If a structured source CSV is created later, it will be preferred automatically.")
    print("2. Comparison metrics are sourced from compare_raw_vs_calibrated.py output JSON.")
    print("3. HOLD days are kept out of the aggregate summary by design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
