from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORTS_REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"


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


def paper_validation_gate(*, start_date: str, end_date: str) -> dict[str, Any]:
    summary_payload = _load_json(REPORTS_MONITORING_ROOT / "paper_validation_summary.json")
    summary = summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
    if not isinstance(summary, dict):
        summary = {}

    current_active_buy_count = int(summary.get("currentActiveBuyCount") or 0)
    paper_candidate_count = int(summary.get("paperCandidateCount") or 0)
    paper_eligible_candidate_count = int(summary.get("paperEligibleCandidateCount") or 0)
    paper_settled_candidate_count = int(summary.get("paperSettledCandidateCount") or 0)
    paper_settlement_coverage = summary.get("paperSettlementCoverage")
    paper_settlement_coverage_raw = summary.get("paperSettlementCoverageRaw")
    paper_settlement_coverage_eligible = summary.get("paperSettlementCoverageEligible")
    try:
        paper_settlement_coverage_value = float(paper_settlement_coverage) if paper_settlement_coverage is not None else None
    except Exception:
        paper_settlement_coverage_value = None
    try:
        paper_settlement_coverage_eligible_value = float(paper_settlement_coverage_eligible) if paper_settlement_coverage_eligible is not None else None
    except Exception:
        paper_settlement_coverage_eligible_value = None
    paper_ineligible_candidate_count = int(summary.get("paperIneligibleCandidateCount") or 0)
    paper_pending_candidate_count = int(summary.get("paperPendingCandidateCount") or 0)
    prediction_hash_missing_days = int(summary.get("predictionHashMissingDays") or 0)
    frozen_bets_missing_days = int(summary.get("frozenBetsMissingDays") or 0)

    live_revenue_gate_status = "NOT_READY" if current_active_buy_count <= 0 else ("READY" if bool(summary.get("liveRevenueValidationReady")) else "RUNNING")
    live_revenue_gate_reason = "current_active_buy_sample_zero" if current_active_buy_count <= 0 else ("live_settlement_not_ready" if live_revenue_gate_status != "READY" else "ready")

    if paper_candidate_count <= 0:
        paper_validation_gate_status = "NOT_READY"
        paper_validation_gate_reason = "paper_candidate_missing"
    elif paper_eligible_candidate_count <= 0:
        paper_validation_gate_status = "RUNNING"
        paper_validation_gate_reason = "paper_eligible_candidate_count_too_low"
    elif paper_eligible_candidate_count < 100:
        paper_validation_gate_status = "RUNNING"
        paper_validation_gate_reason = "paper_eligible_candidate_count_too_low"
    elif paper_settled_candidate_count < 100:
        paper_validation_gate_status = "RUNNING"
        paper_validation_gate_reason = "paper_settled_candidate_count_below_100"
    elif (paper_settlement_coverage_eligible_value or 0.0) < 0.5:
        paper_validation_gate_status = "RUNNING"
        paper_validation_gate_reason = "paper_settlement_coverage_below_0_5"
    elif prediction_hash_missing_days > 0:
        paper_validation_gate_status = "RUNNING"
        paper_validation_gate_reason = "predictionHash_missing"
    elif frozen_bets_missing_days > 0:
        paper_validation_gate_status = "RUNNING"
        paper_validation_gate_reason = "frozen_bets_missing"
    else:
        paper_validation_gate_status = "READY"
        paper_validation_gate_reason = "ready"

    paper_validation_ready = paper_validation_gate_status == "READY"
    live_revenue_ready = live_revenue_gate_status == "READY"
    live_primary_blocker = "current_active_buy_sample_zero" if current_active_buy_count <= 0 else live_revenue_gate_reason
    paper_primary_blocker = paper_validation_gate_reason
    primary_blocker = paper_primary_blocker
    next_action = "paper_validation_candidate_count_accumulate" if not paper_validation_ready else "review_candidate_quality"

    blockers = [
        live_primary_blocker,
        paper_primary_blocker,
    ]
    if paper_eligible_candidate_count < 100 and paper_candidate_count > 0 and paper_primary_blocker != "paper_eligible_candidate_count_too_low":
        blockers.append("paper_eligible_candidate_count_too_low")
    if (paper_settlement_coverage_eligible_value or 0.0) < 0.5 and paper_eligible_candidate_count > 0 and paper_primary_blocker != "paper_settlement_coverage_below_0_5":
        blockers.append("paper_settlement_coverage_below_0_5")
    blockers = list(dict.fromkeys(blockers))

    report = {
        "dateRange": str(summary.get("dateRange") or f"{start_date}_{end_date}"),
        "currentActiveBuyCount": current_active_buy_count,
        "paperCandidateCount": paper_candidate_count,
        "paperEligibleCandidateCount": paper_eligible_candidate_count,
        "paperSettledCandidateCount": paper_settled_candidate_count,
        "paperSettlementCoverage": paper_settlement_coverage,
        "paperSettlementCoverageRaw": paper_settlement_coverage_raw,
        "paperSettlementCoverageEligible": paper_settlement_coverage_eligible,
        "paperIneligibleCandidateCount": paper_ineligible_candidate_count,
        "paperPendingCandidateCount": paper_pending_candidate_count,
        "watchSettledCount": int(summary.get("watchSettledCount") or 0),
        "paperSettledCount": int(summary.get("paperSettledCount") or 0),
        "consensusSettledCount": int(summary.get("consensusSettledCount") or 0),
        "externalAgreementSettledCount": int(summary.get("consensusSettledCount") or 0),
        "backfillSettledCount": int(summary.get("backfillSettledCount") or 0),
        "liveSettledBetCount": int(summary.get("liveSettledBetCount") or 0),
        "liveRevenueGateStatus": live_revenue_gate_status,
        "paperValidationGateStatus": paper_validation_gate_status,
        "liveRevenueGateReason": live_revenue_gate_reason,
        "paperValidationGateReason": paper_validation_gate_reason,
        "liveRevenueValidationReady": live_revenue_ready,
        "paperValidationReady": paper_validation_ready,
        "predictionHashMissingDays": prediction_hash_missing_days,
        "frozenBetsMissingDays": frozen_bets_missing_days,
        "livePrimaryBlocker": live_primary_blocker,
        "paperPrimaryBlocker": paper_primary_blocker,
        "primaryBlocker": primary_blocker,
        "blockers": blockers,
        "nextAction": next_action,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    _save_json(REPORTS_MONITORING_ROOT / "paper_validation_gate.json", report)

    md = "\n".join(
        [
            "# Paper Validation Gate",
            "",
            f"- dateRange: {report['dateRange']}",
            f"- liveRevenueGateStatus: {report['liveRevenueGateStatus']}",
            f"- paperValidationGateStatus: {report['paperValidationGateStatus']}",
            f"- currentActiveBuyCount: {report['currentActiveBuyCount']}",
            f"- livePrimaryBlocker: {report['livePrimaryBlocker']}",
            f"- paperPrimaryBlocker: {report['paperPrimaryBlocker']}",
            f"- paperCandidateCount: {report['paperCandidateCount']}",
            f"- paperSettledCandidateCount: {report['paperSettledCandidateCount']}",
            f"- paperSettlementCoverage: {report['paperSettlementCoverage']}",
            f"- backfillSettledCount: {report['backfillSettledCount']}",
            f"- primaryBlocker: {report['primaryBlocker']}",
            f"- nextAction: {report['nextAction']}",
        ]
    ) + "\n"
    _save_text(REPORTS_REPO_AUDIT_ROOT / "paper_validation_progress.md", md)
    _save_json(REPORTS_REPO_AUDIT_ROOT / "paper_validation_progress.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build paper validation gate.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    payload = paper_validation_gate(start_date=args.start_date, end_date=args.end_date)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
