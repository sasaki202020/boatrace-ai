from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.pipeline.ops_goal_board import build_ops_goal_board
from src.pipeline import task_status as task_status_mod


ROOT = Path(__file__).resolve().parents[2]
REPORT_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORT_DAILY_ROOT = ROOT / "reports" / "daily"
NORMALIZED_ROOT = ROOT / "data" / "normalized"
UI_ROOT = ROOT / "data" / "ui"
PRED_ROOT = ROOT / "data" / "predictions"
ERRORS_ROOT = ROOT / "reports" / "errors"
FINAL_GOAL_PROGRESS_JSON = ROOT / "reports" / "repo_audit" / "final_goal_progress.json"
TASK_LAST_RUN_NAMES = {
    "dailyFreezeLastRun": ("Boatrace_PaperOps_Morning", "Boatrace_DailyFreeze"),
    "eveningSettleLastRun": ("Boatrace_PaperOps_Evening", "Boatrace_EveningSettle"),
    "dailyReportLastRun": ("Boatrace_PaperOps_Monitor", "Boatrace_DailyReport"),
    "healthCheckLastRun": ("Boatrace_PaperOps_Monitor", "Boatrace_HealthCheck"),
}
PRE_RACE_TASK_NAMES = {"Boatrace_PaperOps_Preflight", "Boatrace_PaperOps_Morning"}


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "ok", "done", "available", "ready", "complete_ops"}


def _task_last_run(tasks: Any, task_names: tuple[str, ...]) -> str | None:
    if not isinstance(tasks, list):
        return None
    for task_name in task_names:
        for task in tasks:
            if not isinstance(task, dict) or task.get("taskName") != task_name:
                continue
            last_run = task.get("lastRunTime")
            if last_run:
                return str(last_run)
    return None


def _task_names_with_status(tasks: Any, statuses: set[str]) -> list[str]:
    if not isinstance(tasks, list):
        return []
    return [
        str(task["taskName"])
        for task in tasks
        if isinstance(task, dict)
        and str(task.get("taskName") or "")
        and str(task.get("status") or "").lower() in statuses
    ]


def _load_errors(date_key: str) -> list[dict[str, Any]]:
    path = ERRORS_ROOT / f"{date_key}_errors.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _ready_result_status(status: str) -> bool:
    return status in {"ok", "refund", "canceled", "no_contest", "available_without_trifecta"}


def _count_normalized(date_key: str) -> dict[str, int]:
    root = NORMALIZED_ROOT / date_key
    counts = {"odds3tOkCount": 0, "beforeinfoOkCount": 0, "resultReadyCount": 0, "resultMissingCount": 0}
    if not root.exists():
        return counts
    for path in sorted(root.rglob("race_*.json")):
        payload = _load_json(path)
        if not payload:
            continue
        data_status = payload.get("data_status") or payload.get("dataStatus") or {}
        if not isinstance(data_status, dict):
            data_status = {}
        odds_status = str(data_status.get("odds3t") or "").lower()
        before_status = str(data_status.get("beforeinfo") or "").lower()
        result_payload = payload.get("result") or {}
        if not isinstance(result_payload, dict):
            result_payload = {}
        result_status = str(result_payload.get("raceStatus") or result_payload.get("dataStatus") or data_status.get("result") or "").lower()
        if odds_status in {"ok", "available", "ready"}:
            counts["odds3tOkCount"] += 1
        if before_status in {"ok", "available", "ready"}:
            counts["beforeinfoOkCount"] += 1
        if _ready_result_status(result_status):
            counts["resultReadyCount"] += 1
        else:
            counts["resultMissingCount"] += 1
    return counts


def _load_backfill_readiness() -> dict[str, Any]:
    candidates = sorted((ROOT / "reports" / "backtest").glob("*_backfill_tuning_readiness.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        if payload:
            return payload
    return {}


def health_check(*, target_date: str) -> dict[str, Any]:
    date_key = _normalize_date(target_date)
    canonical_dir = REPORT_DAILY_ROOT / f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    preflight_path = canonical_dir / "preflight_source_check.json"
    pre_race_path = canonical_dir / "pre_race_run.json"
    post_race_path = canonical_dir / "post_race_run.json"
    preflight_run = _load_json(preflight_path)
    today_venues_path = NORMALIZED_ROOT / date_key / "today_venues.json"
    ui_dir = UI_ROOT / date_key
    frozen_path = PRED_ROOT / date_key / "frozen_bets_all.json"
    daily_summary_path = canonical_dir / "daily_summary.json"
    if not daily_summary_path.exists():
        daily_summary_path = REPORT_DAILY_ROOT / f"{date_key}_summary.json"
    daily_settlement_path = canonical_dir / "daily_settlement.json"
    if not daily_settlement_path.exists():
        daily_settlement_path = REPORT_DAILY_ROOT / f"{date_key}_settlement.json"
    daily_report_json = canonical_dir / "daily_report.json"
    if not daily_report_json.exists():
        daily_report_json = REPORT_DAILY_ROOT / date_key / "daily_report.json"
    ops_board_path = canonical_dir / "ops_board.json"
    ops_board_ui_path = ui_dir / "ops_board.json"

    today_venues = _load_json(today_venues_path)
    pre_race_run = _load_json(pre_race_path)
    post_race_run = _load_json(post_race_path)
    frozen_payload = _load_json(frozen_path)
    daily_summary = _load_json(daily_summary_path) or _load_json(daily_settlement_path)
    ops_board = _load_json(ops_board_path) or _load_json(ops_board_ui_path)
    final_goal = _load_json(FINAL_GOAL_PROGRESS_JSON)
    normalized_counts = _count_normalized(date_key)
    errors = _load_errors(date_key)
    backfill_readiness = _load_backfill_readiness()
    try:
        task_status_result = task_status_mod.task_status()
        task_scheduler_summary = task_status_result.get("summary") or {}
    except Exception as exc:
        task_scheduler_summary = {
            "status": "warning",
            "error": str(exc),
            "tasks": [],
            "latestTaskLogs": {},
        }
    failed_scheduled_tasks = _task_names_with_status(
        task_scheduler_summary.get("tasks"),
        {"failed", "missing", "warning"},
    )
    pre_race_scheduler_failed = bool(PRE_RACE_TASK_NAMES.intersection(failed_scheduled_tasks))
    ui_files = sorted(ui_dir.glob("raceyosou_*.json")) if ui_dir.exists() else []
    ui_loaded = 0
    ui_invalid = 0
    for path in ui_files:
        if _load_json(path):
            ui_loaded += 1
        else:
            ui_invalid += 1

    has_today_venues = bool(today_venues)
    has_frozen_bets = bool(frozen_payload)
    has_daily_report = daily_summary_path.exists() or daily_report_json.exists()
    frozen_prediction_hash = bool(frozen_payload.get("predictionHash"))
    result_missing = int(daily_summary.get("resultMissingCount") or normalized_counts["resultMissingCount"])
    result_ready = int(daily_summary.get("resultReadyCount") or normalized_counts["resultReadyCount"])
    settled_bet_count = int(daily_summary.get("settledBetCount") or 0)
    unresolved_bet_count = int(daily_summary.get("unresolvedBetCount") or 0)
    error_count = len(errors) + ui_invalid
    result_parse_error_count = int(daily_summary.get("resultParseErrorCount") or 0)
    live_bet_count = int(daily_summary.get("liveBetCount") or daily_summary.get("betCount") or 0)
    live_settled_bet_count = int(daily_summary.get("liveSettledBetCount") or settled_bet_count)
    live_settlement_coverage = daily_summary.get("liveSettlementCoverage")
    if live_settlement_coverage is None and live_bet_count > 0:
        live_settlement_coverage = round(live_settled_bet_count / live_bet_count, 4)
    revenue_validation_ready = _truthy(final_goal.get("revenueValidationReady"))
    if not revenue_validation_ready:
        revenue_validation_ready = bool(
            has_today_venues
            and has_frozen_bets
            and has_daily_report
            and frozen_prediction_hash
            and live_settled_bet_count >= 100
            and float(live_settlement_coverage or 0) >= 0.5
            and result_parse_error_count == 0
        )
    latest_complete_ops_date = str(final_goal.get("latest_complete_ops_date") or "")
    primary_blocker = str(final_goal.get("primaryBlocker") or final_goal.get("primary_blocker") or "")
    next_action = str(final_goal.get("nextAction") or "")
    paper_eligible_candidate_count = int(final_goal.get("paperEligibleCandidateCount") or 0)
    remaining_paper_eligible_candidate_count = int(final_goal.get("remainingPaperEligibleCandidateCount") or 0)
    can_tune_with_live_only = bool(
        has_today_venues
        and has_frozen_bets
        and has_daily_report
        and frozen_prediction_hash
        and live_settled_bet_count >= 100
        and float(live_settlement_coverage or 0) >= 0.5
        and result_parse_error_count == 0
    )
    status = "ok"
    warnings: list[str] = list(daily_summary.get("warnings") or [])
    if not has_today_venues:
        warnings.append("today_venues_missing")
    if not has_frozen_bets:
        warnings.append("frozen_bets_missing")
    if not has_daily_report:
        warnings.append("daily_report_missing")
    if not frozen_prediction_hash:
        warnings.append("prediction_hash_missing")
    if result_missing > 0:
        warnings.append("result_missing")
    if result_parse_error_count > 0:
        warnings.append("result_parse_error")
    if error_count > 0:
        warnings.append("error_log_present")
    if str(task_scheduler_summary.get("status") or "").lower() not in {"", "ok", "unsupported_platform"}:
        warnings.append("task_scheduler_warning")
    if pre_race_scheduler_failed:
        warnings.append("pre_race_scheduler_failure")
    if not has_today_venues or not has_frozen_bets or not has_daily_report:
        status = "warning"
    if ui_invalid > 0:
        status = "error"
    if "task_scheduler_warning" in warnings and status == "ok":
        status = "warning"

    daily_issue_classification = "unknown"
    preflight_classification = str((preflight_run or {}).get("sourceClassification") or "")
    pre_race_status = str((pre_race_run or {}).get("status") or "")
    pre_race_source_classification = str(
        (pre_race_run or {}).get("sourceClassification")
        or (pre_race_run or {}).get("failure_reason")
        or ""
    )
    post_race_status = str((post_race_run or {}).get("status") or "")
    summary_results_status = str(daily_summary.get("results_status") or "").lower()
    if pre_race_scheduler_failed:
        daily_issue_classification = "pre_race_scheduler_failure"
    elif preflight_classification and preflight_classification != "ready":
        daily_issue_classification = preflight_classification
    elif pre_race_status == "source_not_ready":
        daily_issue_classification = pre_race_source_classification or "pre_race_source_unavailable"
    elif summary_results_status and summary_results_status != "available":
        daily_issue_classification = "result_data_missing"
    elif post_race_status == "missing_data":
        daily_issue_classification = "result_data_missing"
    elif not has_today_venues:
        daily_issue_classification = "pre_race_pipeline_failure"
    elif not has_frozen_bets or not has_daily_report:
        daily_issue_classification = "pipeline_failure"

    ops_board_schema_ok = bool(
        isinstance(ops_board, dict)
        and isinstance(ops_board.get("cards"), list)
        and isinstance(ops_board.get("summary"), dict)
        and isinstance(ops_board.get("status"), str)
    )
    if not ops_board_schema_ok:
        try:
            ops_board = build_ops_goal_board(date_key)
            ops_board_schema_ok = bool(
                isinstance(ops_board, dict)
                and isinstance(ops_board.get("cards"), list)
                and isinstance(ops_board.get("summary"), dict)
                and isinstance(ops_board.get("status"), str)
            )
        except Exception as exc:
            ops_board = {
                "status": "unknown",
                "boardStatus": "unknown",
                "nextAction": "",
                "primaryBlocker": "",
                "completeOpsReady": False,
                "warnings": [f"ops_board_error:{exc}"],
                "summary": {},
                "cards": [],
            }
            ops_board_schema_ok = False

    report = {
        "date": date_key,
        "status": status,
        "dailyIssueClassification": daily_issue_classification,
        "preflightSourceClassification": preflight_classification,
        "todayVenuesPath": str(today_venues_path),
        "todayVenuesExists": today_venues_path.exists(),
        "todayVenuesOk": has_today_venues,
        "uiDir": str(ui_dir),
        "uiJsonCount": len(ui_files),
        "uiJsonLoadedCount": ui_loaded,
        "uiJsonInvalidCount": ui_invalid,
        "frozenBetsPath": str(frozen_path),
        "frozenBetsExists": has_frozen_bets,
        "dailySummaryPath": str(daily_summary_path),
        "dailySettlementPath": str(daily_settlement_path),
        "dailyReportPath": str(daily_report_json),
        "dailyReportExists": has_daily_report,
        "opsBoardPath": str(ops_board_path),
        "opsBoardUiPath": str(ops_board_ui_path),
        "opsBoardExists": ops_board_path.exists() or ops_board_ui_path.exists(),
        "opsBoardSchemaOk": ops_board_schema_ok,
        "opsBoardStatus": str(ops_board.get("status") or ""),
        "opsBoardBoardStatus": str(ops_board.get("boardStatus") or ""),
        "opsBoardNextAction": str(ops_board.get("nextAction") or ""),
        "opsBoardPrimaryBlocker": str(ops_board.get("primaryBlocker") or ""),
        "opsBoardCompleteOpsReady": bool(ops_board.get("completeOpsReady")),
        "opsBoardWarnings": ops_board.get("warnings") or [],
        "opsBoardSummary": ops_board.get("summary") or {},
        "latestCompleteOpsDate": latest_complete_ops_date,
        "completeOpsReady": bool(ops_board.get("completeOpsReady")),
        "primaryBlocker": primary_blocker or str(ops_board.get("primaryBlocker") or ""),
        "nextAction": next_action or str(ops_board.get("nextAction") or "continue_live_operation"),
        "paperEligibleCandidateCount": paper_eligible_candidate_count,
        "remainingPaperEligibleCandidateCount": remaining_paper_eligible_candidate_count,
        "liveSettledBetCount": live_settled_bet_count,
        "revenueValidationReady": revenue_validation_ready,
        "odds3tOkCount": normalized_counts["odds3tOkCount"],
        "beforeinfoOkCount": normalized_counts["beforeinfoOkCount"],
        "resultReadyCount": result_ready,
        "resultMissingCount": result_missing,
        "settledBetCount": settled_bet_count,
        "unresolvedBetCount": unresolved_bet_count,
        "errorCount": error_count,
        "resultParseErrorCount": result_parse_error_count,
        "canTuneWithLiveOnly": can_tune_with_live_only,
        "canTuneWithBackfill": bool(backfill_readiness.get("canTuneWithBackfill")),
        "recommendedNextAction": "continue_live_operation",
        "warnings": sorted(dict.fromkeys(warnings)),
        "predictionHashPresent": frozen_prediction_hash,
        "taskSchedulerStatus": task_scheduler_summary,
        "failedScheduledTasks": failed_scheduled_tasks,
        "preRaceSchedulerFailed": pre_race_scheduler_failed,
        "latestTaskLogs": task_scheduler_summary.get("latestTaskLogs") or {},
        "dailyFreezeLastRun": _task_last_run(task_scheduler_summary.get("tasks"), TASK_LAST_RUN_NAMES["dailyFreezeLastRun"]),
        "eveningSettleLastRun": _task_last_run(task_scheduler_summary.get("tasks"), TASK_LAST_RUN_NAMES["eveningSettleLastRun"]),
        "dailyReportLastRun": _task_last_run(task_scheduler_summary.get("tasks"), TASK_LAST_RUN_NAMES["dailyReportLastRun"]),
        "healthCheckLastRun": _task_last_run(task_scheduler_summary.get("tasks"), TASK_LAST_RUN_NAMES["healthCheckLastRun"]),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    if pre_race_scheduler_failed:
        report["recommendedNextAction"] = "wait_for_next_scheduled_pre_race"
    elif not has_today_venues:
        report["recommendedNextAction"] = "run_discover_today"
    elif not has_frozen_bets:
        report["recommendedNextAction"] = "run_daily_freeze"
    elif not has_daily_report:
        report["recommendedNextAction"] = "run_daily_report"
    elif not can_tune_with_live_only:
        report["recommendedNextAction"] = "continue_collecting_live_settled_bets"

    REPORT_MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_MONITORING_ROOT / f"{date_key}_health_check.json"
    md_path = REPORT_MONITORING_ROOT / f"{date_key}_health_check.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_lines = [
        f"# Health Check ({date_key})",
        "",
        f"- status: {status}",
        f"- dailyIssueClassification: {daily_issue_classification}",
        f"- preflightSourceClassification: {preflight_classification}",
        f"- recommendedNextAction: {report['recommendedNextAction']}",
        f"- todayVenuesExists: {report['todayVenuesExists']}",
        f"- frozenBetsExists: {report['frozenBetsExists']}",
        f"- dailyReportExists: {report['dailyReportExists']}",
        f"- opsBoardExists: {report['opsBoardExists']}",
        f"- opsBoardSchemaOk: {report['opsBoardSchemaOk']}",
        f"- opsBoardStatus: {report['opsBoardStatus']}",
        f"- opsBoardNextAction: {report['opsBoardNextAction']}",
        f"- opsBoardPrimaryBlocker: {report['opsBoardPrimaryBlocker']}",
        f"- opsBoardCompleteOpsReady: {report['opsBoardCompleteOpsReady']}",
        f"- latestCompleteOpsDate: {report['latestCompleteOpsDate'] or ''}",
        f"- completeOpsReady: {report['completeOpsReady']}",
        f"- primaryBlocker: {report['primaryBlocker'] or ''}",
        f"- nextAction: {report['nextAction'] or ''}",
        f"- paperEligibleCandidateCount: {report['paperEligibleCandidateCount']}",
        f"- remainingPaperEligibleCandidateCount: {report['remainingPaperEligibleCandidateCount']}",
        f"- liveSettledBetCount: {report['liveSettledBetCount']}",
        f"- revenueValidationReady: {report['revenueValidationReady']}",
        f"- uiJsonCount: {report['uiJsonCount']}",
        f"- uiJsonLoadedCount: {report['uiJsonLoadedCount']}",
        f"- uiJsonInvalidCount: {report['uiJsonInvalidCount']}",
        f"- odds3tOkCount: {report['odds3tOkCount']}",
        f"- beforeinfoOkCount: {report['beforeinfoOkCount']}",
        f"- resultReadyCount: {report['resultReadyCount']}",
        f"- resultMissingCount: {report['resultMissingCount']}",
        f"- settledBetCount: {report['settledBetCount']}",
        f"- unresolvedBetCount: {report['unresolvedBetCount']}",
        f"- errorCount: {report['errorCount']}",
        f"- resultParseErrorCount: {report['resultParseErrorCount']}",
        f"- canTuneWithLiveOnly: {report['canTuneWithLiveOnly']}",
        f"- canTuneWithBackfill: {report['canTuneWithBackfill']}",
        f"- taskSchedulerStatus: {task_scheduler_summary.get('status')}",
        f"- failedScheduledTasks: {', '.join(report['failedScheduledTasks'])}",
        f"- preRaceSchedulerFailed: {report['preRaceSchedulerFailed']}",
        f"- dailyFreezeLastRun: {report['dailyFreezeLastRun'] or ''}",
        f"- eveningSettleLastRun: {report['eveningSettleLastRun'] or ''}",
        f"- dailyReportLastRun: {report['dailyReportLastRun'] or ''}",
        f"- healthCheckLastRun: {report['healthCheckLastRun'] or ''}",
        "",
        "## Warnings",
    ]
    if report["warnings"]:
        md_lines.extend(f"- {item}" for item in report["warnings"])
    else:
        md_lines.append("- none")
    md_lines.extend(
        [
            "",
            "## Files",
            f"- today_venues: `{report['todayVenuesPath']}`",
            f"- frozen_bets_all: `{report['frozenBetsPath']}`",
            f"- daily_summary: `{report['dailySummaryPath']}`",
            f"- daily_settlement: `{report['dailySettlementPath']}`",
        ]
    )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return {"summary": report, "files": {"json": str(json_path), "md": str(md_path)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Run a live operation health check.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    args = parser.parse_args()
    result = health_check(target_date=args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
