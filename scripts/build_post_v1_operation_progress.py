from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"
REPORTS_ANALYSIS_ROOT = ROOT / "reports" / "analysis"
REPORTS_EXTERNAL_ROOT = ROOT / "reports" / "external" / "baseline_compare"
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"
PAPER_VALIDATION_SUMMARY_JSON = REPORTS_MONITORING_ROOT / "paper_validation_summary.json"
PAPER_VALIDATION_GATE_JSON = REPORTS_MONITORING_ROOT / "paper_validation_gate.json"
LIVE_PAPER_SPLIT_MD = REPO_AUDIT_ROOT / "live_vs_paper_metric_split.md"
LIVE_PAPER_SPLIT_JSON = REPO_AUDIT_ROOT / "live_vs_paper_metric_split.json"
PAPER_VALIDATION_RUNNING_MD = REPO_AUDIT_ROOT / "PAPER_VALIDATION_RUNNING_PROGRESS.md"
PAPER_VALIDATION_RUNNING_JSON = REPO_AUDIT_ROOT / "PAPER_VALIDATION_RUNNING_PROGRESS.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pick_present_value(*mappings: dict[str, Any], key: str, default: Any = None) -> Any:
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        if key in mapping:
            return mapping.get(key)
    return default


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_date_text(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except Exception:
        try:
            return datetime.strptime(value, "%Y%m%d")
        except Exception:
            return None


def _date_range(start_text: str, end_text: str) -> list[str]:
    start = _parse_date_text(start_text)
    end = _parse_date_text(end_text)
    if start is None or end is None or end < start:
        return []
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _existing_dates(root: Path, filename: str) -> list[str]:
    if not root.exists():
        return []
    dates: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / filename).exists():
            digits = "".join(ch for ch in child.name if ch.isdigit())
            if len(digits) >= 8:
                dates.append(digits[:8])
    return sorted(set(dates))


def _count_prediction_days() -> tuple[int, int, int]:
    sheet_days = _existing_dates(REPORTS_PREDICTIONS_ROOT, "prediction_sheet.json")
    review_days = _existing_dates(REPORTS_PREDICTIONS_ROOT, "prediction_review.json")
    daily_summary_days = _existing_dates(REPORTS_DAILY_ROOT, "daily_summary.json")
    watch_paper_days = len(sorted(set(sheet_days).intersection(review_days).intersection(daily_summary_days)))
    return len(sheet_days), len(review_days), watch_paper_days


def _count_consensus_days() -> int:
    return len(_existing_dates(REPORTS_CONSENSUS_ROOT, "consensus_sheet.json"))


def _count_external_days() -> int:
    if not REPORTS_EXTERNAL_ROOT.exists():
        return 0
    days: set[str] = set()
    for path in REPORTS_EXTERNAL_ROOT.glob("*_external_baselines.json"):
        payload = _load_json(path)
        start = str(payload.get("startDate") or "").strip()
        end = str(payload.get("endDate") or "").strip()
        for day in _date_range(start, end):
            days.add(day)
    return len(days)


def _latest_daily_ops_check() -> dict[str, Any]:
    if not REPORTS_DAILY_ROOT.exists():
        return {}
    candidates = []
    for child in REPORTS_DAILY_ROOT.iterdir():
        if child.is_dir():
            path = child / "daily_paper_ops_check.json"
            if path.exists():
                candidates.append((child.name, path))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0])
    return _load_json(candidates[-1][1])


def build_progress() -> dict[str, Any]:
    final_goal = _load_json(REPO_AUDIT_ROOT / "final_goal_progress.json")
    revenue_gate = _load_json(REPO_AUDIT_ROOT / "revenue_gate_progress.json")
    live_summary = _load_json(REPORTS_MONITORING_ROOT / "live_operation_summary.json")
    tuning_gate = _load_json(REPORTS_MONITORING_ROOT / "tuning_gate.json")
    paper_summary_bundle = _load_json(PAPER_VALIDATION_SUMMARY_JSON)
    paper_gate = _load_json(PAPER_VALIDATION_GATE_JSON)
    paper_eligible_dates = _load_json(REPORTS_ANALYSIS_ROOT / "paper_validation_eligible_dates.json")
    paper_next_dates = _load_json(REPORTS_ANALYSIS_ROOT / "paper_validation_next_dates.json")
    daily_ops = _latest_daily_ops_check()

    sheet_days, review_days, watch_paper_days = _count_prediction_days()
    consensus_days = _count_consensus_days()
    external_days = _count_external_days()

    live_summary_data = live_summary.get("summary") if isinstance(live_summary.get("summary"), dict) else {}
    if not isinstance(live_summary_data, dict):
        live_summary_data = {}
    paper_summary = paper_summary_bundle.get("summary") if isinstance(paper_summary_bundle.get("summary"), dict) else {}
    if not isinstance(paper_summary, dict):
        paper_summary = {}

    live_settled = int(_pick_present_value(live_summary_data, final_goal, revenue_gate, key="liveSettledBetCount", default=0) or 0)
    target_settled = int(_pick_present_value(final_goal, revenue_gate, key="targetSettledBetCount", default=100) or 100)
    remaining_settled = max(target_settled - live_settled, 0)
    live_coverage = _pick_present_value(live_summary_data, final_goal, revenue_gate, key="liveSettlementCoverage", default=None)
    try:
        live_coverage_value = float(live_coverage) if live_coverage is not None else None
    except Exception:
        live_coverage_value = None

    current_active_buy_count = int(paper_summary.get("currentActiveBuyCount") or live_summary_data.get("liveBetCount") or 0)
    paper_candidate_count = int(paper_summary.get("paperCandidateCount") or 0)
    paper_eligible_candidate_count = int(paper_summary.get("paperEligibleCandidateCount") or 0)
    paper_settled_candidate_count = int(paper_summary.get("paperSettledCandidateCount") or 0)
    paper_ineligible_candidate_count = int(paper_summary.get("paperIneligibleCandidateCount") or 0)
    paper_pending_candidate_count = int(paper_summary.get("paperPendingCandidateCount") or 0)
    target_paper_settled_candidate_count = 100
    remaining_paper_settled_candidate_count = max(target_paper_settled_candidate_count - paper_settled_candidate_count, 0)
    paper_settlement_coverage_raw = paper_summary.get("paperSettlementCoverageRaw")
    paper_settlement_coverage_eligible = paper_summary.get("paperSettlementCoverageEligible")
    paper_settlement_coverage = paper_summary.get("paperSettlementCoverage")
    try:
        paper_settlement_coverage_value = float(paper_settlement_coverage) if paper_settlement_coverage is not None else None
    except Exception:
        paper_settlement_coverage_value = None
    try:
        paper_settlement_coverage_eligible_value = float(paper_settlement_coverage_eligible) if paper_settlement_coverage_eligible is not None else None
    except Exception:
        paper_settlement_coverage_eligible_value = None
    backfill_settled_count = int(paper_summary.get("backfillSettledCount") or 0)
    paper_eligible_candidate_target = 100
    remaining_paper_eligible_candidate_count = max(paper_eligible_candidate_target - paper_eligible_candidate_count, 0)
    paper_eligible_day_count = int((paper_eligible_dates.get("summary") or {}).get("eligibleDayCount") or 0)
    paper_next_date_rows = paper_next_dates.get("topDates") if isinstance(paper_next_dates.get("topDates"), list) else []
    if not isinstance(paper_next_date_rows, list):
        paper_next_date_rows = []

    live_revenue_gate_status = str(paper_summary.get("liveRevenueGateStatus") or ("NOT_READY" if current_active_buy_count <= 0 else "RUNNING"))
    paper_validation_gate_status = str(paper_gate.get("paperValidationGateStatus") or paper_summary.get("paperValidationGateStatus") or ("NOT_READY" if paper_candidate_count <= 0 else "RUNNING"))
    live_revenue_ready = bool(paper_summary.get("liveRevenueValidationReady"))
    paper_validation_ready = bool(paper_gate.get("paperValidationReady") or paper_summary.get("paperValidationReady"))
    revenue_ready = live_revenue_ready and paper_validation_ready

    live_primary_blocker = str(paper_summary.get("livePrimaryBlocker") or ("current_active_buy_sample_zero" if current_active_buy_count <= 0 else "live_settlement_not_ready"))
    paper_primary_blocker = str(paper_gate.get("paperPrimaryBlocker") or paper_summary.get("paperPrimaryBlocker") or "paper_eligible_candidate_count_too_low")
    blocker = paper_primary_blocker
    next_action = str(paper_gate.get("nextAction") or "paper_validation_candidate_count_accumulate")

    payload = {
        "currentPhase": "PAPER_VALIDATION_RUNNING",
        "paperPredictionWebReady": True,
        "readyOpsTestCount": int(final_goal.get("readyOpsTestCount") or 0),
        "remainingReadyDaysNeeded": int(final_goal.get("remainingReadyDaysNeeded") or 0),
        "currentActiveBuyCount": current_active_buy_count,
        "liveSettledBetCount": live_settled,
        "targetSettledBetCount": target_settled,
        "remainingSettledBetCount": remaining_settled,
        "liveSettlementCoverage": live_coverage,
        "paperCandidateCount": paper_candidate_count,
        "paperEligibleCandidateCount": paper_eligible_candidate_count,
        "paperSettledCandidateCount": paper_settled_candidate_count,
        "remainingPaperEligibleCandidateCount": remaining_paper_eligible_candidate_count,
        "paperEligibleCandidateTarget": paper_eligible_candidate_target,
        "paperEligibleDayCount": paper_eligible_day_count,
        "paperIneligibleCandidateCount": paper_ineligible_candidate_count,
        "paperPendingCandidateCount": paper_pending_candidate_count,
        "targetPaperSettledCandidateCount": target_paper_settled_candidate_count,
        "remainingPaperSettledCandidateCount": remaining_paper_settled_candidate_count,
        "paperSettlementCoverage": paper_settlement_coverage,
        "paperSettlementCoverageRaw": paper_settlement_coverage_raw,
        "paperSettlementCoverageEligible": paper_settlement_coverage_eligible,
        "backfillSettledCount": backfill_settled_count,
        "liveRevenueGateStatus": live_revenue_gate_status,
        "paperValidationGateStatus": paper_validation_gate_status,
        "liveRevenueValidationReady": live_revenue_ready,
        "paperValidationReady": paper_validation_ready,
        "revenueValidationReady": revenue_ready,
        "livePrimaryBlocker": live_primary_blocker,
        "paperPrimaryBlocker": paper_primary_blocker,
        "primaryBlocker": blocker,
        "ineligibleSourceNotReadyCount": int(paper_summary.get("ineligibleSourceNotReadyCount") or 0),
        "raceIdMismatchCountBefore": int(paper_summary.get("raceIdMismatchCountBefore") or 0),
        "raceIdMismatchCountAfter": int(paper_summary.get("raceIdMismatchCountAfter") or 0),
        "predictionHashMissingDays": int(final_goal.get("predictionHashMissingDays") or paper_summary.get("predictionHashMissingDays") or 0),
        "frozenBetsMissingDays": int(final_goal.get("frozenBetsMissingDays") or paper_summary.get("frozenBetsMissingDays") or 0),
        "externalBaselineComparedDays": external_days,
        "consensusComparedDays": consensus_days,
        "watchPaperPerformanceDays": watch_paper_days,
        "predictionSheetDays": sheet_days,
        "predictionReviewDays": review_days,
        "paperEligibleNextDatesTop10": paper_next_date_rows,
        "latest_complete_ops_date": str(final_goal.get("latest_complete_ops_date") or ""),
        "nextAction": next_action,
        "dailyPaperOpsCheck": daily_ops.get("status") or final_goal.get("dailyPaperOpsCheck") or "",
    }
    return payload


def _render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# POST_V1_OPERATION_PROGRESS",
        "",
        f"- currentPhase: {payload.get('currentPhase', '')}",
        f"- paperPredictionWebReady: {payload.get('paperPredictionWebReady', False)}",
        f"- currentActiveBuyCount: {payload.get('currentActiveBuyCount', 0)}",
        f"- liveSettledBetCount: {payload.get('liveSettledBetCount', 0)}",
        f"- paperCandidateCount: {payload.get('paperCandidateCount', 0)}",
        f"- paperSettledCandidateCount: {payload.get('paperSettledCandidateCount', 0)}",
        f"- paperEligibleCandidateCount: {payload.get('paperEligibleCandidateCount', 0)} / {payload.get('paperEligibleCandidateTarget', 100)}",
        f"- remainingPaperEligibleCandidateCount: {payload.get('remainingPaperEligibleCandidateCount', 0)}",
        f"- paperEligibleDayCount: {payload.get('paperEligibleDayCount', 0)}",
        f"- targetPaperSettledCandidateCount: {payload.get('targetPaperSettledCandidateCount', 100)}",
        f"- remainingPaperSettledCandidateCount: {payload.get('remainingPaperSettledCandidateCount', 0)}",
        f"- targetSettledBetCount: {payload.get('targetSettledBetCount', 100)}",
        f"- remainingSettledBetCount: {payload.get('remainingSettledBetCount', 0)}",
        f"- liveSettlementCoverage: {payload.get('liveSettlementCoverage')}",
        f"- paperSettlementCoverage: {payload.get('paperSettlementCoverage')}",
        f"- backfillSettledCount: {payload.get('backfillSettledCount', 0)}",
        f"- liveRevenueGateStatus: {payload.get('liveRevenueGateStatus', '')}",
        f"- paperValidationGateStatus: {payload.get('paperValidationGateStatus', '')}",
        f"- livePrimaryBlocker: {payload.get('livePrimaryBlocker', '')}",
        f"- paperPrimaryBlocker: {payload.get('paperPrimaryBlocker', '')}",
        f"- revenueValidationReady: {payload.get('revenueValidationReady', False)}",
        f"- primaryBlocker: {payload.get('primaryBlocker', '')}",
        f"- predictionHashMissingDays: {payload.get('predictionHashMissingDays', 0)}",
        f"- frozenBetsMissingDays: {payload.get('frozenBetsMissingDays', 0)}",
        f"- externalBaselineComparedDays: {payload.get('externalBaselineComparedDays', 0)}",
        f"- consensusComparedDays: {payload.get('consensusComparedDays', 0)}",
        f"- watchPaperPerformanceDays: {payload.get('watchPaperPerformanceDays', 0)}",
        f"- nextAction: {payload.get('nextAction', '')}",
        "",
    ]
    next_dates = payload.get("paperEligibleNextDatesTop10") if isinstance(payload.get("paperEligibleNextDatesTop10"), list) else []
    if next_dates:
        lines.extend(["## 次に回すべき日付 TOP10"])
        for row in next_dates[:10]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('date', '')} | {row.get('priorityClass', '')} | {row.get('next_action', '')} | "
                f"eligible={row.get('eligible_day', False)} | blocker={row.get('blocker', '')} | est+{row.get('estimated_increase', 0)}"
            )
        lines.append("")
    lines.extend([
        "## まだやらないこと",
        "- BUY閾値変更",
        "- EV条件緩和",
        "- hard_guard緩和",
        "- 実賭け",
        "- 外部予想をBUY判定に混ぜる",
        "- 1日だけの結果で本番反映",
    ])
    return "\n".join(lines) + "\n"


def _render_live_vs_paper_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Live vs Paper Metric Split",
        "",
        f"- liveSettledBetCount: {payload.get('liveSettledBetCount', 0)}",
        f"- currentActiveBuyCount: {payload.get('currentActiveBuyCount', 0)}",
        f"- liveRevenueGateStatus: {payload.get('liveRevenueGateStatus', '')}",
        f"- paperCandidateCount: {payload.get('paperCandidateCount', 0)}",
        f"- paperSettledCandidateCount: {payload.get('paperSettledCandidateCount', 0)}",
        f"- paperSettlementCoverage: {payload.get('paperSettlementCoverage')}",
        f"- paperValidationGateStatus: {payload.get('paperValidationGateStatus', '')}",
        f"- backfillSettledCount: {payload.get('backfillSettledCount', 0)}",
        f"- primaryBlocker: {payload.get('primaryBlocker', '')}",
        "",
        "## 説明",
        "- liveSettledBetCount=0 は current_active_buy_sample_zero のため正しい",
        "- backfill settlement は live に混ぜない",
        "- paper validation は WATCH / PAPER / consensus の紙上検証を追う",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build post v1 operation progress.")
    args = parser.parse_args()
    payload = build_progress()

    md_path = REPO_AUDIT_ROOT / "POST_V1_OPERATION_PROGRESS.md"
    json_path = REPO_AUDIT_ROOT / "POST_V1_OPERATION_PROGRESS.json"
    _save_text(md_path, _render_md(payload))
    _save_json(json_path, payload)

    final_goal_path = REPO_AUDIT_ROOT / "final_goal_progress.json"
    final_goal_md_path = REPO_AUDIT_ROOT / "final_goal_progress.md"
    final_goal = _load_json(final_goal_path)
    final_goal.update({
        "currentPhase": payload["currentPhase"],
        "paperPredictionWebReady": payload["paperPredictionWebReady"],
        "currentActiveBuyCount": payload["currentActiveBuyCount"],
        "liveSettledBetCount": payload["liveSettledBetCount"],
        "targetSettledBetCount": payload["targetSettledBetCount"],
        "remainingSettledBetCount": payload["remainingSettledBetCount"],
        "liveSettlementCoverage": payload["liveSettlementCoverage"],
        "paperCandidateCount": payload["paperCandidateCount"],
        "paperSettledCandidateCount": payload["paperSettledCandidateCount"],
        "remainingPaperEligibleCandidateCount": payload["remainingPaperEligibleCandidateCount"],
        "paperEligibleCandidateTarget": payload["paperEligibleCandidateTarget"],
        "paperEligibleDayCount": payload["paperEligibleDayCount"],
        "targetPaperSettledCandidateCount": payload["targetPaperSettledCandidateCount"],
        "remainingPaperSettledCandidateCount": payload["remainingPaperSettledCandidateCount"],
        "paperSettlementCoverage": payload["paperSettlementCoverage"],
        "backfillSettledCount": payload["backfillSettledCount"],
        "liveRevenueGateStatus": payload["liveRevenueGateStatus"],
        "paperValidationGateStatus": payload["paperValidationGateStatus"],
        "liveRevenueValidationReady": payload["liveRevenueValidationReady"],
        "paperValidationReady": payload["paperValidationReady"],
        "revenueValidationReady": payload["revenueValidationReady"],
        "livePrimaryBlocker": payload["livePrimaryBlocker"],
        "paperPrimaryBlocker": payload["paperPrimaryBlocker"],
        "primaryBlocker": payload["primaryBlocker"],
        "predictionHashMissingDays": payload["predictionHashMissingDays"],
        "frozenBetsMissingDays": payload["frozenBetsMissingDays"],
        "paperEligibleNextDatesTop10": payload["paperEligibleNextDatesTop10"],
        "nextAction": payload["nextAction"],
    })
    _save_json(final_goal_path, final_goal)
    _save_text(final_goal_md_path, _render_md(payload))
    _save_json(LIVE_PAPER_SPLIT_JSON, payload)
    _save_text(LIVE_PAPER_SPLIT_MD, _render_live_vs_paper_md(payload))
    _save_json(PAPER_VALIDATION_RUNNING_JSON, payload)
    _save_text(PAPER_VALIDATION_RUNNING_MD, _render_md(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
