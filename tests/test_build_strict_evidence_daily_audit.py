from __future__ import annotations

import json
from pathlib import Path

import scripts.build_strict_evidence_daily_audit as module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _row(
    candidate_id: str,
    *,
    metadata: bool = True,
    odds_at: str = "2026-07-10T11:55:00",
    deadline_at: str = "2026-07-10T12:00:00",
    frozen: bool = True,
    settled: bool = False,
    settlement_exists: bool = False,
    policy: str = "PAPER",
    guard: str = "KEEP",
) -> dict:
    row = {
        "candidateId": candidate_id if metadata else "",
        "raceId": f"20260710-09-{candidate_id}",
        "raceDate": "2026-07-10",
        "modelVersion": "model_v1" if metadata else "legacy_unknown",
        "policyVersion": "policy_v1" if metadata else "legacy_unknown",
        "predictionHash": f"prediction-{candidate_id}" if metadata else "",
        "oddsCapturedAt": odds_at,
        "deadlineAt": deadline_at,
        "frozenAt": "2026-07-10T11:56:00" if frozen else "",
        "frozenExists": "true" if frozen else "false",
        "settlementExists": "true" if settlement_exists else "false",
        "resultAvailable": "true" if settled else "false",
        "settlementStatus": "settled" if settled else "pending",
        "traceStatus": "complete" if settled else "result_unconfirmed",
        "policyDecision": policy,
        "guardDecision": guard,
    }
    return row


def test_audit_classifies_lifecycle_and_keeps_legacy_out_of_strict_counts(tmp_path: Path) -> None:
    trace_path = tmp_path / "candidate_trace_audit.json"
    gate_path = tmp_path / "live_evidence_gate.json"
    summary_path = tmp_path / "live_operation_summary.json"
    rows = [
        _row("frozen_waiting", settled=False),
        _row("settlement_failed", settled=False, settlement_exists=True),
        _row("freeze_missing", frozen=False),
        _row("metadata_missing", metadata=False),
        _row("settled", settled=True, settlement_exists=True),
    ]
    _write_json(trace_path, {"startDate": "2026-07-10", "endDate": "2026-07-10", "rows": rows})
    _write_json(gate_path, {"metrics": {"traceCoverage": 0.8, "settlementCoverage": 0.6}})
    _write_json(summary_path, {"summary": {"days": 1}})

    payload = module.build_strict_evidence_daily_audit(
        target_date="2026-07-10",
        candidate_trace_audit_path=trace_path,
        live_evidence_gate_path=gate_path,
        live_summary_path=summary_path,
        now="2026-07-10T13:00:00",
    )

    summary = payload["summary"]
    assert summary["auditSchemaVersion"] == "1.0"
    assert summary["targetDate"] == "2026-07-10"
    assert summary["newMetadataCandidateCount"] == 4
    assert summary["strictEligibleCandidateCount"] == 3
    assert summary["frozenCandidateCount"] == 3
    assert summary["settledCandidateCount"] == 1
    assert summary["preDeadlineOddsCoverage"] == 1.0
    assert summary["duplicateCandidateIdCount"] == 0
    assert summary["strictSettlementJoinFailedCount"] == 1
    assert summary["primaryBlockingReason"] == "missing_metadata"
    assert "freeze_not_run" in summary["secondaryBlockingReasons"]
    assert "settlement_join_failure" in {row["lifecycle"] for row in payload["rows"]}
    assert "result_waiting" in {row["lifecycle"] for row in payload["rows"]}
    assert summary["legacyReference"]["legacyMixedIntoStrict"] is False


def test_scope_mismatch_has_priority_when_forward_sources_do_not_cover_date(tmp_path: Path) -> None:
    trace_path = tmp_path / "candidate_trace_audit.json"
    ops_path = tmp_path / "daily_paper_ops_check.json"
    _write_json(trace_path, {"startDate": "2026-07-03", "endDate": "2026-07-08", "rows": []})
    _write_json(
        ops_path,
        {
            "date": "2026-07-10",
            "predictionSheetGenerated": False,
            "frozenBetsGenerated": False,
            "paperPredictionGenerated": False,
        },
    )

    payload = module.build_strict_evidence_daily_audit(
        target_date="2026-07-10",
        candidate_trace_audit_path=trace_path,
        daily_ops_check_path=ops_path,
        now="2026-07-10T23:00:00",
    )

    assert payload["summary"]["primaryBlockingReason"] == "scope_mismatch"
    assert payload["summary"]["currentBlockingStage"] == "scope_mismatch"
    assert payload["summary"]["strictEligibleCandidateCount"] == 0


def test_duplicate_candidate_ids_are_reported_and_not_strict_eligible(tmp_path: Path) -> None:
    trace_path = tmp_path / "candidate_trace_audit.json"
    _write_json(
        trace_path,
        {
            "startDate": "2026-07-10",
            "endDate": "2026-07-10",
            "rows": [_row("duplicate"), _row("duplicate")],
        },
    )

    payload = module.build_strict_evidence_daily_audit(
        target_date="2026-07-10",
        candidate_trace_audit_path=trace_path,
        now="2026-07-10T13:00:00",
    )

    assert payload["summary"]["duplicateCandidateIdCount"] == 1
    assert payload["summary"]["strictEligibleCandidateCount"] == 0
    assert payload["summary"]["primaryBlockingReason"] == "result_waiting"
