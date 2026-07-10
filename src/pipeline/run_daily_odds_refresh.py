from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from src.pipeline.pipeline_utils import (
    ROOT,
    append_log,
    copy_artifact,
    existing_report_dir_for,
    iso_now,
    log_file_for,
    parse_date,
    report_dir_for,
    run_step,
    write_json,
)
from src.pipeline.odds_refresh_policy import (
    SUMMARY_PATH as ODDS_REFRESH_SUMMARY_PATH,
    calculate_adoption_score,
    OddsRefreshSummary,
    load_policy as load_odds_refresh_policy,
    normalize_phase,
    upsert_daily_summary,
)


def _load_skip_snapshot(path: Path) -> dict:
    if not path.exists():
        return {}
    df = pd.read_csv(path, low_memory=False)
    if df.empty:
        return {"buy_count": 0, "real_odds_missing": 0, "real_odds_pending_before_deadline": 0, "pending": 0, "stop_reason_top": {}}
    stop_counts = df.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict()
    stop_series = df.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str)
    return {
        "buy_count": int(df.get("decision", pd.Series(dtype=object)).astype(str).str.upper().eq("BUY").sum()),
        "real_odds_missing": int(stop_series.str.startswith("real_odds_missing").sum()),
        "real_odds_pending_before_deadline": int(stop_series.eq("real_odds_pending_before_deadline").sum()),
        "real_odds_pending_unpublished": int(stop_series.eq("real_odds_pending_unpublished").sum()),
        "pending": int(df.get("decision", pd.Series(dtype=object)).astype(str).str.upper().eq("PENDING").sum()),
        "odds_status_counts": df.get("odds_status", pd.Series(dtype=object)).fillna("unknown").astype(str).value_counts().to_dict(),
        "stop_reason_top": dict(list(stop_counts.items())[:8]),
    }


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, float) and pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y"}


def _normalize_race_result(row: dict[str, object]) -> dict[str, object]:
    fetch_status = str(row.get("fetch_status", "")).strip().lower()
    used_cache = _coerce_bool(row.get("used_cache", False))
    state = "skipped"
    available = False
    pending_unpublished = False
    missing_fetch = False
    skipped = False

    if fetch_status in {"success", "partial_missing", "cached"}:
        state = "available"
        available = True
        skipped = fetch_status == "cached" or used_cache
    elif fetch_status == "pending_unpublished":
        state = "pending_unpublished"
        pending_unpublished = True
    elif fetch_status == "failed":
        state = "missing_fetch"
        missing_fetch = True
    else:
        skipped = True

    return {
        "race_id": str(row.get("race_id", "")),
        "date": str(row.get("date", "")),
        "jcd": str(row.get("jcd", "")),
        "stadium": str(row.get("stadium", "")),
        "race_no": _coerce_int(row.get("race_no", 0)),
        "fetch_status": fetch_status,
        "used_cache": used_cache,
        "state": state,
        "available": available,
        "pending_unpublished": pending_unpublished,
        "missing_fetch": missing_fetch,
        "skipped": skipped,
        "missing_odds_cells": _coerce_int(row.get("missing_odds_cells", 0)),
        "failed_reason": str(row.get("failed_reason", "")),
        "fetched_at": str(row.get("fetched_at", "")),
        "source_url": str(row.get("source_url", "")),
    }


def _load_race_results(report_dir: Path, fetch_report: dict | None = None) -> list[dict[str, object]]:
    race_status_path = None
    if isinstance(fetch_report, dict):
        output = fetch_report.get("output", {})
        if isinstance(output, dict):
            race_status_path = output.get("race_status_csv")
    if not race_status_path:
        race_status_path = report_dir / "race_status.csv"

    race_status_file = Path(str(race_status_path))
    if not race_status_file.exists():
        return []

    try:
        df = pd.read_csv(race_status_file, low_memory=False)
    except Exception:
        return []
    if df.empty:
        return []

    results: list[dict[str, object]] = []
    for _, row in df.iterrows():
        results.append(_normalize_race_result(row.to_dict()))
    return results


def build_refresh_summary(date_str: str, phase: str, race_results: list[dict[str, object]]) -> dict[str, object]:
    normalized_phase = normalize_phase(phase, default="final")
    summary = OddsRefreshSummary(
        date=str(date_str),
        phase=normalized_phase,
        total_races=int(len(race_results)),
        real_odds_available=int(sum(1 for row in race_results if bool(row.get("available")))),
        pending_unpublished=int(sum(1 for row in race_results if bool(row.get("pending_unpublished")))),
        real_odds_missing_fetch=int(sum(1 for row in race_results if bool(row.get("missing_fetch")))),
        fetch_error_count=int(sum(1 for row in race_results if bool(row.get("missing_fetch")))),
        skipped_races=int(sum(1 for row in race_results if bool(row.get("skipped")))),
        status="ok",
    )
    summary_row = summary.to_dict()
    summary_row["race_results"] = race_results
    summary_row["adoption_score"] = calculate_adoption_score(
        real_odds_available=summary.real_odds_available,
        pending_unpublished=summary.pending_unpublished,
        real_odds_missing_fetch=summary.real_odds_missing_fetch,
    )
    return summary_row


def validate_summary(summary: dict[str, object]) -> None:
    required_keys = {
        "date",
        "phase",
        "total_races",
        "real_odds_available",
        "pending_unpublished",
        "real_odds_missing_fetch",
        "fetch_error_count",
        "skipped_races",
        "status",
    }
    missing = required_keys - set(summary.keys())
    if missing:
        raise ValueError(f"summary missing keys: {sorted(missing)}")
    if str(summary.get("phase", "")) not in {"morning", "late", "final"}:
        raise ValueError(f"invalid phase: {summary.get('phase')}")
    numeric_cols = [
        "total_races",
        "real_odds_available",
        "pending_unpublished",
        "real_odds_missing_fetch",
        "fetch_error_count",
        "skipped_races",
    ]
    for col in numeric_cols:
        if int(summary.get(col, 0)) < 0:
            raise ValueError(f"negative summary value: {col}={summary.get(col)}")
    if int(summary.get("pending_unpublished", 0)) + int(summary.get("real_odds_missing_fetch", 0)) > int(summary.get("total_races", 0)):
        raise ValueError("summary counters exceed total_races")


def _load_fetch_report(report_dir: Path) -> dict:
    path = report_dir / "odds_refresh_run.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_daily_odds_refresh(
    target_date: date,
    phase: str,
    *,
    delay: float = 1.0,
    refresh: bool = False,
    pending_only: bool = False,
) -> dict[str, object]:
    report_dir = report_dir_for(target_date)
    existing_report_dir = existing_report_dir_for(target_date)
    log_path = log_file_for("odds_refresh", target_date)
    normalized_phase = normalize_phase(phase, default="final")
    policy = load_odds_refresh_policy()
    append_log(
        log_path,
        f"[run:start] pipeline=odds_refresh date={target_date.isoformat()} phase={normalized_phase}",
    )

    odds_date_key = target_date.strftime("%Y%m%d")
    py_cmd = sys.executable
    daily_candidates_path = (existing_report_dir / "trifecta_candidates.csv") if (existing_report_dir / "trifecta_candidates.csv").exists() else (report_dir / "trifecta_candidates.csv")
    daily_race_card_path = (existing_report_dir / "today_win_proba.csv") if (existing_report_dir / "today_win_proba.csv").exists() else (report_dir / "today_win_proba.csv")
    daily_odds_path = (existing_report_dir / "today_trifecta_odds.csv") if (existing_report_dir / "today_trifecta_odds.csv").exists() else (report_dir / "today_trifecta_odds.csv")
    daily_ev_output_path = report_dir / "ev_analysis.csv"
    daily_skip_path = report_dir / "skip_decisions.csv"
    daily_exacta_output_path = report_dir / "skip_decisions_exacta_mode.csv"
    existing_skip_path = existing_report_dir / "skip_decisions.csv"
    skip_path = daily_skip_path if daily_skip_path.exists() else (existing_skip_path if existing_skip_path.exists() else ROOT / "data/strategy_outputs/skip_decisions.csv")
    before_snapshot = _load_skip_snapshot(skip_path)
    steps: list[dict[str, object]] = []
    refresh_result: dict = {}
    run_status = "ok"
    failure_step = None
    run_started_at = iso_now()

    step_specs = [
        (
            "fetch_today_odds",
            [
                py_cmd,
                "-m",
                "src.odds.fetch_daily_trifecta_odds",
                "--date",
                target_date.isoformat(),
                "--timeout",
                "15",
                "--retries",
                "2",
                "--retry-sleep",
                "1.5",
                "--settle-retry-rounds",
                "1",
                "--settle-retry-sleep",
                "120",
                "--pending-retry-rounds",
                "3",
                "--pending-retry-sleep",
                "180",
                "--request-interval",
                str(delay),
                "--unpublished-retry-rounds",
                "2",
                "--unpublished-retry-sleep",
                "300",
                "--refresh",
            ],
            True,
        ),
        (
            "evaluate_ev_and_skip",
            [
                py_cmd,
                "-m",
                "src.strategy.evaluate_ev_and_skip",
                "--candidates-path",
                str(daily_candidates_path if daily_candidates_path.exists() else ROOT / "data/strategy_outputs/trifecta_candidates.csv"),
                "--race-card-path",
                str(daily_race_card_path if daily_race_card_path.exists() else ROOT / "data/model_outputs/today_win_proba.csv"),
                "--odds-path",
                str(daily_odds_path if daily_odds_path.exists() else ROOT / "data/odds/today_trifecta_odds.csv"),
                "--ev-output-path",
                str(daily_ev_output_path),
                "--skip-output-path",
                str(daily_skip_path),
            ],
            False,
        ),
        (
            "build_exacta_mode_predictions",
            [
                py_cmd,
                "-m",
                "src.strategy.build_exacta_mode_predictions",
                "--input",
                str(daily_skip_path),
                "--output",
                str(daily_exacta_output_path),
            ],
            True,
        ),
        (
            "analyze_gate_health",
            [
                py_cmd,
                "-m",
                "src.eval.analyze_gate_health",
                "--skip-path",
                str(daily_skip_path),
                "--out-dir",
                str(report_dir),
            ],
            True,
        ),
    ]
    if not refresh:
        step_specs[0][1].pop()
    if pending_only:
        step_specs[0][1].append("--pending-only")

    total_steps = len(step_specs)
    for index, (label, cmd, allow_failure) in enumerate(step_specs, start=1):
        print(f"[STEP {index}/{total_steps}] {label} started at {iso_now()}")
        step = run_step(label, cmd, allow_failure=allow_failure, log_path=log_path)
        if label == "fetch_today_odds":
            refresh_result = dict(step)
        steps.append(step)
        print(
            f"[STEP {index}/{total_steps}] {label} "
            f"{'OK' if step['returncode'] == 0 else step['status'].upper()} "
            f"({step['duration_sec']}s)"
        )
        if step["returncode"] != 0 and not allow_failure:
            run_status = "failed"
            failure_step = label
            break

    artifacts: dict[str, str] = {}
    after_snapshot: dict[str, object] = {}
    if run_status == "ok":
        after_snapshot = _load_skip_snapshot(daily_skip_path)
        artifact_map = {
            "today_odds": ROOT / "data/odds/today_trifecta_odds.csv",
            "today_odds_report": ROOT / "data/odds" / odds_date_key / "fetch_report.json",
            "today_odds_race_status": ROOT / "data/odds" / odds_date_key / "race_status.csv",
            "today_odds_targets": ROOT / "data/odds" / odds_date_key / "race_targets.csv",
            "today_odds_failures": ROOT / "data/odds" / odds_date_key / "failed_races.csv",
            "skip_decisions": daily_skip_path,
            "skip_decisions_exacta_mode": daily_exacta_output_path,
            "ev_analysis": daily_ev_output_path,
            "gate_health_summary": report_dir / "gate_health_summary.json",
        }
        for key, src in artifact_map.items():
            if src.exists() and src.parent == report_dir:
                artifacts[key] = str(src)
                continue
            copied = copy_artifact(src, report_dir)
            if copied:
                artifacts[key] = copied

    fetch_report = _load_fetch_report(report_dir)
    race_results = _load_race_results(report_dir, fetch_report)
    if not race_results and isinstance(fetch_report, dict):
        pending_ids = set(fetch_report.get("pending_unpublished_race_ids", []) or [])
        failed_ids = set(fetch_report.get("failed_race_ids", []) or [])
        race_results = [
            {
                "race_id": race_id,
                "date": target_date.isoformat(),
                "jcd": "",
                "stadium": "",
                "race_no": 0,
                "fetch_status": "pending_unpublished" if race_id in pending_ids else ("failed" if race_id in failed_ids else "skipped"),
                "used_cache": False,
                "state": "pending_unpublished" if race_id in pending_ids else ("missing_fetch" if race_id in failed_ids else "skipped"),
                "available": False,
                "pending_unpublished": race_id in pending_ids,
                "missing_fetch": race_id in failed_ids,
                "skipped": race_id not in pending_ids and race_id not in failed_ids,
                "missing_odds_cells": 120 if race_id in pending_ids or race_id in failed_ids else 0,
                "failed_reason": "",
                "fetched_at": "",
                "source_url": "",
            }
            for race_id in list(pending_ids | failed_ids)
        ]

    summary = build_refresh_summary(target_date.isoformat(), normalized_phase, race_results)
    summary["status"] = run_status
    summary["fetch_error_count"] = int(summary["real_odds_missing_fetch"])
    summary["adoption_score"] = calculate_adoption_score(
        real_odds_available=summary["real_odds_available"],
        pending_unpublished=summary["pending_unpublished"],
        real_odds_missing_fetch=summary["real_odds_missing_fetch"],
    )
    validate_summary(summary)
    summary["phase_order"] = {"morning": 1, "late": 2, "final": 3}[normalized_phase]
    summary["measured_at"] = run_started_at
    summary["source"] = str(refresh_result.get("label", "odds_refresh")) if refresh_result else "odds_refresh"
    summary["pipeline_report_path"] = str(report_dir / "odds_refresh_run.json")
    summary["command"] = " ".join(step_specs[0][1] if step_specs else [])
    summary["real_odds_missing_fetch_failed"] = summary["real_odds_missing_fetch"]
    summary["real_odds_missing_never_fetched"] = 0
    summary_row = dict(summary)
    summary_row.pop("race_results", None)
    summary_path, _ = upsert_daily_summary(
        summary_row,
        path=ODDS_REFRESH_SUMMARY_PATH,
        seed_from_time_series=True,
        policy=policy,
    )
    append_log(
        log_path,
        "[summary] "
        f"path={summary_path} phase={normalized_phase} total_races={summary.get('total_races', 0)} "
        f"real_odds_available={summary.get('real_odds_available', 0)} "
        f"pending_unpublished={summary.get('pending_unpublished', 0)} "
        f"real_odds_missing_fetch={summary.get('real_odds_missing_fetch', 0)} "
        f"adoption_score={summary.get('adoption_score', 0)}",
    )

    report = {
        "generated_at": target_date.isoformat(),
        "pipeline": "odds_refresh",
        "date": target_date.isoformat(),
        "phase": normalized_phase,
        "status": run_status,
        "failure_step": failure_step,
        "log_path": str(log_path),
        "steps": steps,
        "artifacts": artifacts,
        "refresh": bool(refresh),
        "refresh_result": refresh_result,
        "summary_path": str(summary_path),
        "summary_row": summary_row,
        "comparison": {
            "before": before_snapshot,
            "after": after_snapshot,
            "buy_count_delta": int(after_snapshot.get("buy_count", 0) - before_snapshot.get("buy_count", 0)),
            "real_odds_missing_delta": int(after_snapshot.get("real_odds_missing", 0) - before_snapshot.get("real_odds_missing", 0)),
            "real_odds_pending_before_deadline_delta": int(after_snapshot.get("real_odds_pending_before_deadline", 0) - before_snapshot.get("real_odds_pending_before_deadline", 0)),
            "real_odds_pending_unpublished_delta": int(after_snapshot.get("real_odds_pending_unpublished", 0) - before_snapshot.get("real_odds_pending_unpublished", 0)),
            "pending_delta": int(after_snapshot.get("pending", 0) - before_snapshot.get("pending", 0)),
        },
        "notes": {
            "purpose": "最新オッズをスクレイピング取得して、当日判定だけを再生成する",
            "partial_failure": "オッズ取得失敗時も、取得できた範囲で後続を継続する",
        },
    }
    write_json(report_dir / "odds_refresh_run.json", report)
    append_log(
        log_path,
        f"[run:end] pipeline=odds_refresh phase={normalized_phase} status={run_status} failure_step={failure_step}",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh today's scraped odds and regenerate decisions.")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--phase", choices=["morning", "late", "final"], default="final")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--refresh", action="store_true", help="Force refetch even when cached daily odds exist.")
    parser.add_argument("--pending-only", action="store_true", help="Retry only races previously marked pending_unpublished.")
    args = parser.parse_args()

    target_date = parse_date(args.date, default=date.today())
    summary = run_daily_odds_refresh(
        target_date,
        args.phase,
        delay=float(args.delay),
        refresh=bool(args.refresh),
        pending_only=bool(args.pending_only),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
