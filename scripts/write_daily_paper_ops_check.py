from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DAILY_ROOT = ROOT / "reports" / "daily"
REPORT_PRED_ROOT = ROOT / "reports" / "predictions"
REPORT_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORT_AUDIT_ROOT = ROOT / "reports" / "repo_audit"


def _date_dir(date_text: str) -> Path:
    return REPORT_DAILY_ROOT / date_text


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _bool_file(path: Path) -> bool:
    return path.exists()


def _load_health_check() -> dict[str, Any]:
    return _load_json(REPORT_AUDIT_ROOT / "health_check.json")


def _json_status(path: Path) -> str:
    payload = _load_json(path)
    if not payload:
        return ""
    status = payload.get("status") or payload.get("results_status") or payload.get("resultStatus")
    return str(status or "").strip().lower()


def _step_status(log_dir: Path, step_name: str, *, ready_artifact: bool = False) -> str:
    status_path = log_dir / f"step_{step_name}.status"
    done_path = log_dir / f"step_{step_name}.done"
    skipped_path = log_dir / f"step_{step_name}.skipped_existing"
    failed_path = log_dir / f"step_{step_name}.failed"
    result_missing_path = log_dir / f"step_{step_name}.result_data_missing"

    if status_path.exists():
        try:
            token = status_path.read_text(encoding="utf-8").strip().lower()
        except Exception:
            token = ""
        if token:
            if token in {"done", "ok"}:
                return "ok"
            return token

    for path, value in [
        (failed_path, "failed"),
        (result_missing_path, "result_data_missing"),
        (done_path, "ok"),
        (skipped_path, "skipped_existing"),
    ]:
        if path.exists():
            return value

    if status_path.exists():
        try:
            token = status_path.read_text(encoding="utf-8").strip().lower()
        except Exception:
            token = ""
        if token:
            return token

    if ready_artifact:
        return "skipped_existing"
    return "pending"


def _step_bucket(log_dir: Path, names: list[tuple[str, bool]]) -> dict[str, str]:
    return {name: _step_status(log_dir, name, ready_artifact=ready) for name, ready in names}


def _is_step_complete(status: str, *, allow_result_data_missing: bool = False) -> bool:
    normalized = str(status or "").strip().lower()
    allowed = {"ok", "skipped_existing"}
    if allow_result_data_missing:
        allowed.add("result_data_missing")
    return normalized in allowed


def _build_step_status(payload: dict[str, Any]) -> dict[str, Any]:
    date_text = str(payload["date"])
    daily_dir = _date_dir(date_text)
    pred_dir = REPORT_PRED_ROOT / date_text
    log_dir = daily_dir / "logs"
    preflight_class = str(payload.get("preflightSourceClassification") or "").lower()
    source_not_ready = preflight_class in {
        "future_date_not_ready",
        "source_not_ready",
        "official_index_unavailable",
        "official_index_empty",
        "official_index_parse_failed",
    }

    def _or_source_not_ready(name: str, ready: bool) -> str:
        status = _step_status(log_dir, name, ready_artifact=ready)
        if status == "pending" and source_not_ready:
            return "source_not_ready"
        return status

    return {
        "date": date_text,
        "generatedAt": payload.get("generatedAt", datetime.now().isoformat(timespec="seconds")),
        "morning": _step_bucket(
            log_dir,
            [
                ("pre_race", _bool_file(daily_dir / "pre_race_run.json")),
                ("odds_refresh", _bool_file(daily_dir / "odds_refresh_run.json")),
                ("prediction_sheet", _bool_file(pred_dir / "prediction_sheet.json")),
                ("frozen_bets", _bool_file(pred_dir / "frozen_bets.json")),
            ],
        ) if not source_not_ready else {
            "pre_race": _or_source_not_ready("pre_race", _bool_file(daily_dir / "pre_race_run.json")),
            "odds_refresh": _or_source_not_ready("odds_refresh", _bool_file(daily_dir / "odds_refresh_run.json")),
            "prediction_sheet": _or_source_not_ready("prediction_sheet", _bool_file(pred_dir / "prediction_sheet.json")),
            "frozen_bets": _or_source_not_ready("frozen_bets", _bool_file(pred_dir / "frozen_bets.json")),
        },
        "evening": _step_bucket(
            log_dir,
            [
                ("check_k_inbox", False),
                ("import_k_results", False),
                ("evening_settle", _bool_file(daily_dir / "daily_summary.json") or _bool_file(daily_dir / "post_race_run.json")),
                ("daily_report", _bool_file(daily_dir / "daily_report.json")),
                ("prediction_review", _bool_file(pred_dir / "prediction_review.json")),
            ],
        ) if not source_not_ready else {
            "check_k_inbox": _or_source_not_ready("check_k_inbox", False),
            "import_k_results": _or_source_not_ready("import_k_results", False),
            "evening_settle": _or_source_not_ready("evening_settle", _bool_file(daily_dir / "daily_summary.json") or _bool_file(daily_dir / "post_race_run.json")),
            "daily_report": _or_source_not_ready("daily_report", _bool_file(daily_dir / "daily_report.json")),
            "prediction_review": _or_source_not_ready("prediction_review", _bool_file(pred_dir / "prediction_review.json")),
        },
        "monitor": _step_bucket(
            log_dir,
            [
                ("live_operation_summary", _bool_file(REPORT_MONITORING_ROOT / "live_operation_summary.json")),
                ("tuning_gate", _bool_file(REPORT_MONITORING_ROOT / "tuning_gate.json")),
                ("daily_paper_ops_check", _bool_file(daily_dir / "daily_paper_ops_check.json")),
                ("final_goal_progress", _bool_file(REPORT_AUDIT_ROOT / "final_goal_progress.json")),
            ],
        ),
        "overallStatus": str(payload.get("status") or "pending"),
        "nextAction": str(payload.get("nextAction") or ""),
    }


def build_daily_paper_ops_check(
    *,
    date_text: str,
    preflight_class: str,
    full_route_executed: bool,
) -> dict[str, Any]:
    daily_dir = _date_dir(date_text)
    pred_dir = REPORT_PRED_ROOT / date_text
    preflight = _load_json(daily_dir / "preflight_source_check.json")
    sheet = _load_json(pred_dir / "prediction_sheet.json")
    review = _load_json(pred_dir / "prediction_review.json")
    daily_summary = _load_json(daily_dir / "daily_summary.json")
    live_summary_bundle = _load_json(REPORT_MONITORING_ROOT / "live_operation_summary.json")
    tuning_gate = _load_json(REPORT_MONITORING_ROOT / "tuning_gate.json")
    paper_validation_bundle = _load_json(REPORT_MONITORING_ROOT / "paper_validation_summary.json")
    paper_validation_gate = _load_json(REPORT_MONITORING_ROOT / "paper_validation_gate.json")
    final_goal = _load_json(REPORT_AUDIT_ROOT / "final_goal_progress.json")

    live_summary = live_summary_bundle.get("summary") if isinstance(live_summary_bundle.get("summary"), dict) else {}
    if not isinstance(live_summary, dict):
        live_summary = {}
    paper_validation_summary = paper_validation_bundle.get("summary") if isinstance(paper_validation_bundle.get("summary"), dict) else {}
    if not isinstance(paper_validation_summary, dict):
        paper_validation_summary = {}
    blockers = tuning_gate.get("reasons") or []
    if not isinstance(blockers, list):
        blockers = [str(blockers)]

    step_status = _build_step_status(
        {
            "date": date_text,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "preflightSourceClassification": preflight_class or str(preflight.get("sourceClassification") or "unknown"),
            "status": "pending",
            "nextAction": "",
        }
    )

    paper_prediction_generated = _bool_file(pred_dir / "prediction_sheet.json") or _bool_file(pred_dir / "prediction_sheet.csv")
    prediction_review_generated = _bool_file(pred_dir / "prediction_review.json") or _bool_file(pred_dir / "prediction_review.md")
    pre_race_status = step_status["morning"]["pre_race"]
    odds_refresh_status = step_status["morning"]["odds_refresh"]
    post_race_status = step_status["evening"]["evening_settle"]
    daily_report_exists = _bool_file(daily_dir / "daily_report.json")
    daily_report_status = step_status["evening"]["daily_report"]
    frozen_bets_generated = _bool_file(pred_dir / "frozen_bets.json") or _bool_file(pred_dir / "frozen_bets.csv")
    morning_route_done = all(
        _is_step_complete(step_status["morning"][name])
        for name in ["pre_race", "odds_refresh", "prediction_sheet", "frozen_bets"]
    )
    evening_route_done = all(
        _is_step_complete(step_status["evening"][name], allow_result_data_missing=(name == "evening_settle"))
        for name in ["check_k_inbox", "import_k_results", "evening_settle", "prediction_review"]
    )
    health_check_done = _bool_file(REPORT_MONITORING_ROOT / f"{date_text.replace('-', '')}_health_check.json")
    revenue_gate_updated = _bool_file(REPORT_MONITORING_ROOT / "live_operation_summary.json") and _bool_file(REPORT_MONITORING_ROOT / "tuning_gate.json")
    target_settled = int(final_goal.get("targetSettledBetCount") or 100)
    remaining_settled = max(target_settled - int(live_summary.get("liveSettledBetCount") or 0), 0)
    current_active_buy_count = int(paper_validation_summary.get("currentActiveBuyCount") or live_summary.get("liveBetCount") or 0)
    paper_candidate_count = int(paper_validation_summary.get("paperCandidateCount") or 0)
    paper_settled_candidate_count = int(paper_validation_summary.get("paperSettledCandidateCount") or 0)
    paper_settlement_coverage = paper_validation_summary.get("paperSettlementCoverage")
    backfill_settled_count = int(paper_validation_summary.get("backfillSettledCount") or 0)
    live_revenue_gate_status = str(paper_validation_summary.get("liveRevenueGateStatus") or ("NOT_READY" if current_active_buy_count <= 0 else "RUNNING"))
    paper_validation_gate_status = str(paper_validation_gate.get("paperValidationGateStatus") or paper_validation_summary.get("paperValidationGateStatus") or ("NOT_READY" if paper_candidate_count <= 0 else "RUNNING"))
    live_revenue_gate_reason = str(paper_validation_summary.get("liveRevenueGateReason") or ("current_active_buy_sample_zero" if current_active_buy_count <= 0 else "live_settlement_not_ready"))
    paper_validation_gate_reason = str(paper_validation_gate.get("paperValidationGateReason") or paper_validation_summary.get("paperValidationGateReason") or ("paperSettledCandidateCount_below_100" if paper_settled_candidate_count < 100 else "ready"))
    live_primary_blocker = str(paper_validation_summary.get("livePrimaryBlocker") or ("current_active_buy_sample_zero" if current_active_buy_count <= 0 else "live_settlement_not_ready"))
    paper_primary_blocker = str(paper_validation_gate.get("paperPrimaryBlocker") or paper_validation_summary.get("paperPrimaryBlocker") or paper_validation_gate_reason)
    primary_blocker = paper_primary_blocker
    next_action = str(
        paper_validation_gate.get("nextAction")
        or paper_validation_summary.get("nextAction")
        or ("paper_validation_candidate_count_accumulate" if paper_validation_gate_status != "READY" else "review_candidate_quality")
    )

    payload = {
        "date": date_text,
        "requestedDate": date_text,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "preflightSourceClassification": preflight_class or str(preflight.get("sourceClassification") or "unknown"),
        "preflightReady": (preflight_class or str(preflight.get("sourceClassification") or "unknown")) == "ready",
        "morningRouteDone": morning_route_done,
        "eveningRouteDone": evening_route_done,
        "paperPredictionGenerated": paper_prediction_generated,
        "predictionSheetGenerated": paper_prediction_generated,
        "frozenBetsGenerated": frozen_bets_generated,
        "predictionReviewGenerated": prediction_review_generated,
        "dailySummaryExists": _bool_file(daily_dir / "daily_summary.json"),
        "dailyReportExists": daily_report_exists,
        "dailyReportReady": _is_step_complete(daily_report_status),
        "latest_complete_ops_date": str(final_goal.get("latest_complete_ops_date") or daily_summary.get("latest_complete_ops_date") or ""),
        "currentActiveBuyCount": current_active_buy_count,
        "liveSettledBetCount": int(live_summary.get("liveSettledBetCount") or 0),
        "liveSettlementCoverage": live_summary.get("liveSettlementCoverage"),
        "paperCandidateCount": paper_candidate_count,
        "paperSettledCandidateCount": paper_settled_candidate_count,
        "paperSettlementCoverage": paper_settlement_coverage,
        "backfillSettledCount": backfill_settled_count,
        "liveRevenueGateStatus": live_revenue_gate_status,
        "paperValidationGateStatus": paper_validation_gate_status,
        "liveRevenueGateReason": live_revenue_gate_reason,
        "paperValidationGateReason": paper_validation_gate_reason,
        "livePrimaryBlocker": live_primary_blocker,
        "paperPrimaryBlocker": paper_primary_blocker,
        "revenueValidationReady": bool(live_revenue_gate_status == "READY" and paper_validation_gate_status == "READY"),
        "targetSettledBetCount": target_settled,
        "remainingSettledBetCount": remaining_settled,
        "primaryBlocker": primary_blocker,
        "healthCheckDone": health_check_done,
        "revenueGateUpdated": revenue_gate_updated,
        "blockers": blockers,
        "nextAction": next_action,
        "dailySummaryStatus": str(daily_summary.get("results_status") or daily_summary.get("status") or "missing"),
        "status": "partial",
        "artifacts": {
            "preflightSourceCheck": _bool_file(daily_dir / "preflight_source_check.json"),
            "morningRouteStatus": _bool_file(REPORT_MONITORING_ROOT / "morning_route_status.json"),
            "predictionSheetJson": _bool_file(pred_dir / "prediction_sheet.json"),
            "predictionReviewJson": _bool_file(pred_dir / "prediction_review.json"),
            "dailySummaryJson": _bool_file(daily_dir / "daily_summary.json"),
            "dailyReportJson": daily_report_exists,
            "liveOperationSummaryJson": _bool_file(REPORT_MONITORING_ROOT / "live_operation_summary.json"),
            "tuningGateJson": _bool_file(REPORT_MONITORING_ROOT / "tuning_gate.json"),
        },
        "monitoring": {
            "liveOperationSummaryPath": str(REPORT_MONITORING_ROOT / "live_operation_summary.json"),
            "tuningGatePath": str(REPORT_MONITORING_ROOT / "tuning_gate.json"),
            "healthCheckPath": str(REPORT_MONITORING_ROOT / f"{date_text.replace('-', '')}_health_check.json"),
        },
    }
    payload["fullRouteExecuted"] = bool(full_route_executed or (morning_route_done and evening_route_done))
    daily_summary_status = str(payload["dailySummaryStatus"]).lower()
    complete_summary_statuses = {"ok", "available", "settled"}
    incomplete_summary_statuses = {"missing", "raw_missing", "result_data_missing", "source_not_ready", "future_date_not_ready"}
    if preflight_class in {"future_date_not_ready", "source_not_ready", "official_index_unavailable", "official_index_empty", "official_index_parse_failed"}:
        payload["status"] = "source_not_ready"
    elif any(step in {"failed", "error", "exception"} for step in [pre_race_status, odds_refresh_status, post_race_status]):
        payload["status"] = "failed"
    elif morning_route_done and evening_route_done and daily_summary_status in complete_summary_statuses and health_check_done and revenue_gate_updated:
        payload["status"] = "complete_ops"
    elif morning_route_done and evening_route_done and daily_summary_status in incomplete_summary_statuses:
        payload["status"] = "result_data_missing"
    elif preflight_class == "ready" and not morning_route_done:
        payload["status"] = "running"
    elif preflight_class == "ready" and morning_route_done and not evening_route_done:
        payload["status"] = "partial"
    else:
        payload["status"] = "partial"
    if payload["status"] == "source_not_ready":
        payload["nextAction"] = "wait_for_publication"
    elif payload["status"] == "running":
        payload["nextAction"] = "run_morning_route"
    elif payload["status"] == "partial":
        if morning_route_done and not evening_route_done:
            payload["nextAction"] = "run_evening_route_after_results"
        elif not morning_route_done:
            payload["nextAction"] = "run_morning_route"
        else:
            payload["nextAction"] = "rerun_monitor"
    elif payload["status"] == "result_data_missing":
        payload["nextAction"] = "place_missing_k_files_in_inbox"
    elif payload["status"] == "complete_ops":
        if not bool(payload.get("paperValidationReady")):
            payload["nextAction"] = "paper_validation_candidate_count_accumulate"
        elif not bool(payload["revenueValidationReady"]):
            payload["nextAction"] = "revenue_gate_wait_live_settled_bets"
        else:
            payload["nextAction"] = "start_tuning_review"
    payload["paperOpsStepStatus"] = step_status
    payload["paperOpsStepStatus"]["overallStatus"] = payload["status"]
    payload["paperOpsStepStatus"]["nextAction"] = payload["nextAction"]
    return payload


def _render_md(payload: dict[str, Any]) -> str:
    blockers = payload.get("blockers") or []
    if isinstance(blockers, list):
        blocker_text = ", ".join(str(x) for x in blockers) if blockers else "-"
    else:
        blocker_text = str(blockers)
    lines = [
        f"# Daily Paper Ops Check {payload['date']}",
        "",
        f"- preflightSourceClassification: {payload['preflightSourceClassification']}",
        f"- fullRouteExecuted: {payload['fullRouteExecuted']}",
        f"- paperPredictionGenerated: {payload['paperPredictionGenerated']}",
        f"- predictionReviewGenerated: {payload['predictionReviewGenerated']}",
        f"- dailyReportExists: {payload['dailyReportExists']}",
        f"- dailyReportReady: {payload['dailyReportReady']}",
        f"- latest_complete_ops_date: {payload['latest_complete_ops_date'] or '-'}",
        f"- currentActiveBuyCount: {payload['currentActiveBuyCount']}",
        f"- liveSettledBetCount: {payload['liveSettledBetCount']}",
        f"- liveRevenueGateStatus: {payload.get('liveRevenueGateStatus', '')}",
        f"- paperValidationGateStatus: {payload.get('paperValidationGateStatus', '')}",
        f"- livePrimaryBlocker: {payload.get('livePrimaryBlocker', '')}",
        f"- paperPrimaryBlocker: {payload.get('paperPrimaryBlocker', '')}",
        f"- paperCandidateCount: {payload.get('paperCandidateCount', '')}",
        f"- paperSettledCandidateCount: {payload.get('paperSettledCandidateCount', '')}",
        f"- targetSettledBetCount: {payload.get('targetSettledBetCount', '')}",
        f"- remainingSettledBetCount: {payload.get('remainingSettledBetCount', '')}",
        f"- liveSettlementCoverage: {payload['liveSettlementCoverage']}",
        f"- paperSettlementCoverage: {payload.get('paperSettlementCoverage', '')}",
        f"- backfillSettledCount: {payload.get('backfillSettledCount', '')}",
        f"- revenueValidationReady: {payload['revenueValidationReady']}",
        f"- primaryBlocker: {payload.get('primaryBlocker', '')}",
        f"- blockers: {blocker_text}",
        f"- nextAction: {payload['nextAction'] or '-'}",
        f"- dailySummaryStatus: {payload['dailySummaryStatus']}",
        f"- status: {payload['status']}",
        "",
        "## Artifacts",
    ]
    artifacts = payload.get("artifacts") or {}
    for key, value in artifacts.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def _update_final_goal_progress(payload: dict[str, Any]) -> None:
    path = REPORT_AUDIT_ROOT / "final_goal_progress.json"
    current = _load_json(path)
    health = _load_health_check()
    if not current:
        current = {}

    blockers = payload.get("blockers") or []
    if not isinstance(blockers, list):
        blockers = [str(blockers)]

    current["liveSettledBetCount"] = payload.get("liveSettledBetCount", current.get("liveSettledBetCount"))
    current["liveSettlementCoverage"] = payload.get("liveSettlementCoverage", current.get("liveSettlementCoverage"))
    current["currentActiveBuyCount"] = payload.get("currentActiveBuyCount", current.get("currentActiveBuyCount"))
    current["targetSettledBetCount"] = payload.get("targetSettledBetCount", current.get("targetSettledBetCount", 100))
    current["remainingSettledBetCount"] = payload.get("remainingSettledBetCount", current.get("remainingSettledBetCount"))
    current["paperCandidateCount"] = payload.get("paperCandidateCount", current.get("paperCandidateCount"))
    current["paperSettledCandidateCount"] = payload.get("paperSettledCandidateCount", current.get("paperSettledCandidateCount"))
    current["paperSettlementCoverage"] = payload.get("paperSettlementCoverage", current.get("paperSettlementCoverage"))
    current["backfillSettledCount"] = payload.get("backfillSettledCount", current.get("backfillSettledCount"))
    current["liveRevenueGateStatus"] = payload.get("liveRevenueGateStatus", current.get("liveRevenueGateStatus"))
    current["paperValidationGateStatus"] = payload.get("paperValidationGateStatus", current.get("paperValidationGateStatus"))
    current["liveRevenueValidationReady"] = payload.get("liveRevenueValidationReady", current.get("liveRevenueValidationReady"))
    current["paperValidationReady"] = payload.get("paperValidationReady", current.get("paperValidationReady"))
    current["revenueValidationReady"] = bool(payload.get("revenueValidationReady"))
    if health:
        current["latest_complete_ops_date"] = health.get("latest_complete_ops_date", current.get("latest_complete_ops_date", ""))
        current["latest_ready_daily_date"] = health.get("latest_ready_daily_date", current.get("latest_ready_daily_date", ""))
        current["latest_daily_issue_classification"] = health.get(
            "latest_daily_issue_classification",
            current.get("latest_daily_issue_classification", ""),
        )
    current["blocker"] = blockers[0] if blockers else current.get("blocker", "")
    current["primaryBlocker"] = payload.get("primaryBlocker") or current.get("primaryBlocker") or current["blocker"]
    current["nextAction"] = payload.get("nextAction") or current.get("nextAction") or ""
    current["dailyPaperOpsCheck"] = {
        "date": payload["date"],
        "preflightSourceClassification": payload["preflightSourceClassification"],
        "fullRouteExecuted": payload["fullRouteExecuted"],
        "paperPredictionGenerated": payload["paperPredictionGenerated"],
        "predictionReviewGenerated": payload["predictionReviewGenerated"],
        "dailySummaryStatus": payload["dailySummaryStatus"],
        "status": payload["status"],
    }

    md_path = REPORT_AUDIT_ROOT / "final_goal_progress.md"
    md_lines = [
        "# Final Goal Progress",
        "",
        f"- currentPhase: {current.get('currentPhase', '')}",
        f"- latest_complete_ops_date: {current.get('latest_complete_ops_date', '')}",
        f"- readyOpsTestCount: {current.get('readyOpsTestCount', '')}",
        f"- remainingReadyDaysNeeded: {current.get('remainingReadyDaysNeeded', '')}",
        f"- completeOpsCount: {current.get('completeOpsCount', '')}",
        f"- resultDataMissingCount: {current.get('resultDataMissingCount', '')}",
        f"- sourceNotReadyCount: {current.get('sourceNotReadyCount', '')}",
        f"- pipelineFailureCount: {current.get('pipelineFailureCount', '')}",
        f"- liveSettledBetCount: {current.get('liveSettledBetCount', '')}",
        f"- targetSettledBetCount: {current.get('targetSettledBetCount', '')}",
        f"- remainingSettledBetCount: {current.get('remainingSettledBetCount', '')}",
        f"- liveSettlementCoverage: {current.get('liveSettlementCoverage', '')}",
        f"- predictionHashMissingDays: {current.get('predictionHashMissingDays', '')}",
        f"- frozenBetsMissingDays: {current.get('frozenBetsMissingDays', '')}",
        f"- paperPredictionWebReady: {current.get('paperPredictionWebReady', '')}",
        f"- runPaperPredictionDayReadyRouteVerified: {current.get('runPaperPredictionDayReadyRouteVerified', '')}",
        f"- runPaperPredictionDaySourceNotReadyVerified: {current.get('runPaperPredictionDaySourceNotReadyVerified', '')}",
        f"- revenueValidationReady: {current.get('revenueValidationReady', '')}",
        f"- blocker: {current.get('blocker', '')}",
        f"- primaryBlocker: {current.get('primaryBlocker', '')}",
        f"- nextAction: {current.get('nextAction', '')}",
        f"- dailyPaperOpsCheck: {current['dailyPaperOpsCheck']['status']}",
    ]
    _save_json(path, current)
    _save_text(md_path, "\n".join(md_lines) + "\n")


def _write_paper_ops_step_status(payload: dict[str, Any]) -> None:
    daily_dir = _date_dir(payload["date"])
    _save_json(daily_dir / "paper_ops_step_status.json", payload.get("paperOpsStepStatus") or {})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Write daily paper ops check reports")
    parser.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    parser.add_argument("--preflight-class", default="unknown", help="Preflight source classification")
    parser.add_argument("--full-route-executed", default="0", help="Whether full route was executed")
    args = parser.parse_args()

    date_text = args.date
    full_route_executed = str(args.full_route_executed).strip() in {"1", "true", "yes", "y"}
    payload = build_daily_paper_ops_check(
        date_text=date_text,
        preflight_class=args.preflight_class,
        full_route_executed=full_route_executed,
    )

    daily_dir = _date_dir(date_text)
    _save_json(daily_dir / "daily_paper_ops_check.json", payload)
    _save_text(daily_dir / "daily_paper_ops_check.md", _render_md(payload))
    revenue_gate_payload = {
        "date": payload["date"],
        "preflightSourceClassification": payload["preflightSourceClassification"],
        "fullRouteExecuted": payload["fullRouteExecuted"],
        "liveSettledBetCount": payload["liveSettledBetCount"],
        "currentActiveBuyCount": payload["currentActiveBuyCount"],
        "targetSettledBetCount": payload.get("targetSettledBetCount", ""),
        "remainingSettledBetCount": payload.get("remainingSettledBetCount", ""),
        "liveSettlementCoverage": payload["liveSettlementCoverage"],
        "paperCandidateCount": payload.get("paperCandidateCount", ""),
        "paperSettledCandidateCount": payload.get("paperSettledCandidateCount", ""),
        "paperSettlementCoverage": payload.get("paperSettlementCoverage", ""),
        "backfillSettledCount": payload.get("backfillSettledCount", ""),
        "liveRevenueGateStatus": payload.get("liveRevenueGateStatus", ""),
        "paperValidationGateStatus": payload.get("paperValidationGateStatus", ""),
        "liveRevenueGateReason": payload.get("liveRevenueGateReason", ""),
        "paperValidationGateReason": payload.get("paperValidationGateReason", ""),
        "livePrimaryBlocker": payload.get("livePrimaryBlocker", ""),
        "paperPrimaryBlocker": payload.get("paperPrimaryBlocker", ""),
        "predictionHashMissingDays": _load_json(REPORT_AUDIT_ROOT / "final_goal_progress.json").get("predictionHashMissingDays"),
        "frozenBetsMissingDays": _load_json(REPORT_AUDIT_ROOT / "final_goal_progress.json").get("frozenBetsMissingDays"),
        "revenueValidationReady": payload["revenueValidationReady"],
        "primaryBlocker": payload.get("primaryBlocker", ""),
        "blockers": payload["blockers"],
        "nextAction": payload["nextAction"] or "-",
        "generatedAt": payload["generatedAt"],
    }
    _save_json(REPORT_AUDIT_ROOT / "revenue_gate_progress.json", revenue_gate_payload)
    _save_text(
        REPORT_AUDIT_ROOT / "revenue_gate_progress.md",
        "\n".join(
            [
                "# Revenue Gate Progress",
                "",
                f"- date: {payload['date']}",
                f"- preflightSourceClassification: {payload['preflightSourceClassification']}",
                f"- fullRouteExecuted: {payload['fullRouteExecuted']}",
                f"- liveSettledBetCount: {payload['liveSettledBetCount']}",
                f"- targetSettledBetCount: {payload.get('targetSettledBetCount', '')}",
                f"- remainingSettledBetCount: {payload.get('remainingSettledBetCount', '')}",
                f"- liveSettlementCoverage: {payload['liveSettlementCoverage']}",
                f"- revenueValidationReady: {payload['revenueValidationReady']}",
                f"- primaryBlocker: {payload.get('primaryBlocker', '')}",
                f"- blockers: {', '.join(payload['blockers']) if payload['blockers'] else '-'}",
                f"- nextAction: {payload['nextAction'] or '-'}",
            ]
        )
        + "\n",
    )
    _write_paper_ops_step_status(payload)
    _update_final_goal_progress(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

