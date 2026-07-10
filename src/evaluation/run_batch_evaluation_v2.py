from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pandas as pd

from src.evaluation.run_day_evaluation_v2 import evaluate_shadow_day_v2
from src.ingest import load_comparison_target_days, load_comparison_target_status, load_v2_sources
from src.storage.duckdb import DuckDBStore, duckdb_available


DEFAULT_COMPARISON_TARGETS = Path("data/v2/comparison_target_days.csv")
DEFAULT_DB_PATH = Path("data/v2/batch_v2.duckdb")
DEFAULT_OUTPUT_DIR = Path("reports/v2")

FAILURE_REASON_MESSAGES = {
    "MISSING_RESULT": "result data is missing or incomplete",
    "MISSING_ODDS": "odds coverage is missing",
    "RAW_INCOMPLETE": "upstream raw comparison is incomplete",
    "INVALID_STATUS": "status is not eligible for batch execution",
    "EVAL_ERROR": "batch evaluation failed",
}


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


@dataclass
class BatchDayRecord:
    date: str
    compare_status: str
    status: str
    reference_only: bool
    compare_ok: bool
    v1_compareable: bool
    v2_compareable: bool
    target_races: int
    results_ready_count: int
    odds_coverage: float
    raw_buy: int
    cal_buy: int
    raw_hit: int
    cal_hit: int
    raw_roi: float
    cal_roi: float
    failure_reason: str
    note: str = ""


@dataclass
class BatchFailureRecord:
    date: str
    failure_type: str
    step: str
    message: str
    retryable: bool


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _load_existing_result_dates(result_csv: Path) -> set[str]:
    if not result_csv.exists():
        return set()
    df = pd.read_csv(result_csv)
    if df.empty or "date" not in df.columns:
        return set()
    return {normalize_date_str(v) for v in df["date"].tolist() if normalize_date_str(v)}


def _normalize_dates(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        date8 = normalize_date_str(value)
        if date8:
            out.append(date8)
    return list(dict.fromkeys(out))


def _coerce_bool(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def select_batch_dates(
    *,
    mode: str,
    explicit_dates: list[str],
    limit: int,
    skip_existing: bool,
    comparison_targets_path: Path,
    existing_result_csv: Path,
) -> pd.DataFrame:
    target_days = load_comparison_target_days(comparison_targets_path)
    if not target_days.empty:
        target_days = target_days.copy()
        target_days["date"] = target_days["date"].astype(str).map(normalize_date_str)
        target_days = target_days[target_days["date"] != ""].copy()

    if mode == "target-only":
        selected = target_days[target_days["status"] == "TARGET"].copy()
    elif mode == "include-hold":
        selected = target_days[target_days["status"].isin(["TARGET", "HOLD"])].copy()
    elif mode == "explicit-dates":
        wanted = _normalize_dates(explicit_dates)
        rows: list[dict[str, Any]] = []
        for date in wanted:
            match = target_days[target_days["date"] == date]
            if match.empty:
                rows.append(
                    {
                        "date": date,
                        "result_txt_ready": pd.NA,
                        "raw_incomplete": pd.NA,
                        "real_odds_available": "",
                        "pending_unpublished": "",
                        "missing_fetch": "",
                        "simulator_ok": pd.NA,
                        "status": "HOLD",
                        "reason": "not present in structured target CSV",
                        "action": "review target CSV",
                    }
                )
            else:
                rows.extend(match.to_dict(orient="records"))
        selected = pd.DataFrame(rows)
    else:
        raise ValueError(f"unsupported mode: {mode}")

    if selected.empty:
        return selected

    selected = selected.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    if limit > 0:
        selected = selected.tail(limit).reset_index(drop=True)

    if skip_existing:
        existing_dates = _load_existing_result_dates(existing_result_csv)
        if existing_dates:
            selected = selected[~selected["date"].astype(str).isin(existing_dates)].reset_index(drop=True)

    return selected


def _make_failure_records(date: str, reasons: list[str], *, step: str = "batch") -> list[BatchFailureRecord]:
    records: list[BatchFailureRecord] = []
    for reason in reasons:
        records.append(
            BatchFailureRecord(
                date=date,
                failure_type=reason,
                step=step,
                message=FAILURE_REASON_MESSAGES.get(reason, reason),
                retryable=reason in {"MISSING_RESULT", "MISSING_ODDS", "RAW_INCOMPLETE", "EVAL_ERROR"},
            )
        )
    return records


def run_batch_evaluation_v2(
    *,
    selected_days: pd.DataFrame,
    db_path: Path,
    historical_path: Path,
    odds_root: Path,
    raw_candidates_path: Path,
    cal_candidates_path: Path,
    v1_compare_dir: Path,
    dry_run: bool = False,
) -> tuple[list[BatchDayRecord], list[BatchFailureRecord], dict[str, Any]]:
    sources, warnings = load_v2_sources(historical_path=historical_path, odds_root=odds_root)
    raw_candidates = pd.read_csv(raw_candidates_path) if raw_candidates_path.exists() else pd.DataFrame()
    cal_candidates = pd.read_csv(cal_candidates_path) if cal_candidates_path.exists() else pd.DataFrame()

    results: list[BatchDayRecord] = []
    failures: list[BatchFailureRecord] = []

    if selected_days.empty:
        summary = {
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "run_count": 0,
            "success_count": 0,
            "hold_count": 0,
            "fail_count": 0,
            "aggregate_count": 0,
            "raw": {"buy": 0, "hit": 0, "roi": 0.0},
            "calibrated": {"buy": 0, "hit": 0, "roi": 0.0},
            "delta": {"buy": 0, "hit": 0, "roi": 0.0},
            "selected_dates": [],
            "warnings": warnings,
        }
        return results, failures, summary

    store = None
    conn = None
    if not dry_run and duckdb_available():
        store = DuckDBStore(db_path=db_path)
        conn = store.connect()
        store.initialize_schema(conn)

    try:
        for row in selected_days.to_dict(orient="records"):
            date_str = normalize_date_str(row.get("date"))
            compare_status = str(row.get("status", "HOLD")).upper()
            note = str(row.get("reason", "")).strip()
            reference_only = compare_status == "HOLD"
            result_txt_ready = _coerce_bool(row.get("result_txt_ready"))
            raw_incomplete = _coerce_bool(row.get("raw_incomplete"))
            simulator_ok = _coerce_bool(row.get("simulator_ok"))

            if compare_status == "EXCLUDE":
                failures.extend(_make_failure_records(date_str, ["INVALID_STATUS"]))
                results.append(
                    BatchDayRecord(
                        date=date_str,
                        compare_status=compare_status,
                        status="FAIL",
                        reference_only=False,
                        compare_ok=False,
                        v1_compareable=False,
                        v2_compareable=False,
                        target_races=0,
                        results_ready_count=0,
                        odds_coverage=0.0,
                        raw_buy=0,
                        cal_buy=0,
                        raw_hit=0,
                        cal_hit=0,
                        raw_roi=0.0,
                        cal_roi=0.0,
                        failure_reason="INVALID_STATUS",
                        note=note,
                    )
                )
                continue

            if compare_status == "TARGET":
                precheck_failures: list[str] = []
                if result_txt_ready is False:
                    precheck_failures.append("MISSING_RESULT")
                if raw_incomplete is True:
                    precheck_failures.append("RAW_INCOMPLETE")
                if simulator_ok is False:
                    precheck_failures.append("EVAL_ERROR")

                if precheck_failures:
                    failures.extend(_make_failure_records(date_str, precheck_failures))
                    results.append(
                        BatchDayRecord(
                            date=date_str,
                            compare_status=compare_status,
                            status="FAIL",
                            reference_only=False,
                            compare_ok=False,
                            v1_compareable=False,
                            v2_compareable=False,
                            target_races=0,
                            results_ready_count=0,
                            odds_coverage=0.0,
                            raw_buy=0,
                            cal_buy=0,
                            raw_hit=0,
                            cal_hit=0,
                            raw_roi=0.0,
                            cal_roi=0.0,
                            failure_reason=",".join(precheck_failures),
                            note=note,
                        )
                    )
                    continue

            day_tables = {name: frame[frame["date"].astype(str).map(normalize_date_str) == date_str].copy() if not frame.empty and "date" in frame.columns else frame.iloc[0:0].copy() for name, frame in sources.items()}
            raw_day = raw_candidates[raw_candidates["date"].astype(str).map(normalize_date_str) == date_str].copy() if not raw_candidates.empty and "date" in raw_candidates.columns else pd.DataFrame()
            cal_day = cal_candidates[cal_candidates["date"].astype(str).map(normalize_date_str) == date_str].copy() if not cal_candidates.empty and "date" in cal_candidates.columns else pd.DataFrame()

            if conn is not None:
                for table_name, frame in day_tables.items():
                    store.replace_table(conn, table_name, frame)

            v1_compare_path = v1_compare_dir / f"raw_vs_calibrated_{date_str}.json"
            try:
                summary, diff, db_warnings = evaluate_shadow_day_v2(
                    date_str=date_str,
                    compare_status=compare_status,
                    db_path=db_path,
                    fallback_tables=day_tables,
                    raw_candidates=raw_day,
                    calibrated_candidates=cal_day,
                    v1_compare_path=v1_compare_path,
                )
            except Exception as exc:
                failures.extend(_make_failure_records(date_str, ["EVAL_ERROR"]))
                results.append(
                    BatchDayRecord(
                        date=date_str,
                        compare_status=compare_status,
                        status="FAIL",
                        reference_only=reference_only,
                        compare_ok=False,
                        v1_compareable=False,
                        v2_compareable=False,
                        target_races=0,
                        results_ready_count=0,
                        odds_coverage=0.0,
                        raw_buy=0,
                        cal_buy=0,
                        raw_hit=0,
                        cal_hit=0,
                        raw_roi=0.0,
                        cal_roi=0.0,
                        failure_reason="EVAL_ERROR",
                        note=str(exc),
                    )
                )
                continue

            failure_reasons = list(summary.get("failure_reasons", []))
            compare_ok = bool(summary.get("compare_possible"))
            status = "HOLD" if reference_only else ("SUCCESS" if compare_ok and not failure_reasons else "FAIL")

            if status == "FAIL" and failure_reasons:
                failures.extend(_make_failure_records(date_str, failure_reasons))

            results.append(
                BatchDayRecord(
                    date=date_str,
                    compare_status=compare_status,
                    status=status,
                    reference_only=reference_only,
                    compare_ok=compare_ok,
                    v1_compareable=bool(summary.get("v1_compareable")),
                    v2_compareable=compare_ok,
                    target_races=_as_int(summary.get("target_races")),
                    results_ready_count=_as_int(summary.get("results_ready_count")),
                    odds_coverage=_as_float(summary.get("odds_coverage")),
                    raw_buy=_as_int(summary.get("v1_raw_summary", {}).get("buy_count")),
                    cal_buy=_as_int(summary.get("v1_calibrated_summary", {}).get("buy_count")),
                    raw_hit=_as_int(summary.get("v1_raw_summary", {}).get("hit_count")),
                    cal_hit=_as_int(summary.get("v1_calibrated_summary", {}).get("hit_count")),
                    raw_roi=round(_as_float(summary.get("v1_raw_summary", {}).get("roi")), 4),
                    cal_roi=round(_as_float(summary.get("v1_calibrated_summary", {}).get("roi")), 4),
                    failure_reason=",".join(failure_reasons),
                    note=note,
                )
            )
    finally:
        if conn is not None:
            conn.close()

    success_rows = [row for row in results if row.status == "SUCCESS" and not row.reference_only]
    hold_rows = [row for row in results if row.status == "HOLD"]
    fail_rows = [row for row in results if row.status == "FAIL"]

    raw_buy = sum(row.raw_buy for row in success_rows)
    raw_hit = sum(row.raw_hit for row in success_rows)
    cal_buy = sum(row.cal_buy for row in success_rows)
    cal_hit = sum(row.cal_hit for row in success_rows)

    summary = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "run_count": len(results),
        "success_count": len(success_rows),
        "hold_count": len(hold_rows),
        "fail_count": len(fail_rows),
        "aggregate_count": len(success_rows),
        "raw": {"buy": raw_buy, "hit": raw_hit, "roi": None},
        "calibrated": {"buy": cal_buy, "hit": cal_hit, "roi": None},
        "delta": {"buy": cal_buy - raw_buy, "hit": cal_hit - raw_hit, "roi": None},
        "selected_dates": [row.date for row in results],
        "warnings": warnings,
    }
    if success_rows:
        summary["raw"]["roi"] = round(sum(row.raw_roi for row in success_rows) / len(success_rows), 4)
        summary["calibrated"]["roi"] = round(sum(row.cal_roi for row in success_rows) / len(success_rows), 4)
        summary["delta"]["roi"] = round(summary["calibrated"]["roi"] - summary["raw"]["roi"], 4)
    else:
        summary["raw"]["roi"] = 0.0
        summary["calibrated"]["roi"] = 0.0
        summary["delta"]["roi"] = 0.0

    return results, failures, summary


def results_to_frame(results: list[BatchDayRecord]) -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in results])


def failures_to_frame(failures: list[BatchFailureRecord]) -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in failures])


def write_batch_outputs(
    *,
    output_dir: Path,
    results: list[BatchDayRecord],
    failures: list[BatchFailureRecord],
    summary: dict[str, Any],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    results_csv = output_dir / "batch_results.csv"
    summary_json = output_dir / "batch_summary.json"
    failures_csv = output_dir / "batch_failures.csv"

    results_to_frame(results).to_csv(results_csv, index=False, encoding="utf-8")
    failures_to_frame(failures).to_csv(failures_csv, index=False, encoding="utf-8")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return results_csv, summary_json, failures_csv
