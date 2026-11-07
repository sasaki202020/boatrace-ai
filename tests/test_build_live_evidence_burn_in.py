from __future__ import annotations

import json
import os
from pathlib import Path

import scripts.build_live_evidence_burn_in as module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _legacy_row(*, candidate_id: str, settled: bool = True) -> dict:
    return {
        "candidateId": candidate_id,
        "raceId": "20260710-12-01",
        "raceDate": "2026-07-10",
        "venueCode": "12",
        "raceNo": "1",
        "combination": "1-2-3",
        "modelVersion": "legacy_unknown",
        "calibratorVersion": "legacy_unknown",
        "policyVersion": "legacy_unknown",
        "predictionHash": f"pred-{candidate_id}",
        "snapshotHash": "legacy_unknown",
        "featureVersion": "legacy_unknown",
        "odds": "21.4",
        "oddsCapturedAt": "legacy_unknown",
        "deadlineAt": "2026-07-10T12:00:00",
        "policyDecision": "WATCH",
        "guardDecision": "SKIP",
        "guardReason": "legacy",
        "frozenAt": "2026-07-10T11:56:00",
        "frozenExists": "true",
        "settlementExists": "true" if settled else "false",
        "resultAvailable": "true" if settled else "false",
        "settlementStatus": "available" if settled else "pending",
        "traceStatus": "complete" if settled else "result_unconfirmed",
        "hit": "true" if settled else "false",
        "payoutAmount": "850" if settled else "0",
        "pnl": "750" if settled else "-100",
    }


def _strict_row(*, candidate_id: str, hit: bool) -> dict:
    return {
        "candidateId": candidate_id,
        "raceId": "20260710-12-01",
        "raceDate": "2026-07-10",
        "venueCode": "12",
        "raceNo": "1",
        "combination": "1-2-3",
        "modelVersion": "baseline_rule_v1",
        "calibratorVersion": "cal_v1",
        "policyVersion": "paper_shadow_policy_v1",
        "predictionHash": f"pred-{candidate_id}",
        "snapshotHash": f"snap-{candidate_id}",
        "featureVersion": "feature_v1",
        "odds": "21.4",
        "oddsCapturedAt": "2026-07-10T11:55:00",
        "deadlineAt": "2026-07-10T12:00:00",
        "policyDecision": "PAPER",
        "guardDecision": "KEEP",
        "guardReason": "ok",
        "frozenAt": "2026-07-10T11:56:00",
        "frozenExists": "true",
        "settlementExists": "true",
        "resultAvailable": "true",
        "settlementStatus": "available",
        "traceStatus": "complete",
        "hit": "true" if hit else "false",
        "payoutAmount": "850" if hit else "0",
        "pnl": "750" if hit else "-100",
    }


def test_burn_in_reports_legacy_only_flow_and_writes_outputs(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "candidate_trace_audit.json"
    gate_path = tmp_path / "live_evidence_gate.json"
    summary_path = tmp_path / "live_operation_summary.json"
    rows = [_legacy_row(candidate_id="c1", settled=True)]
    _write_json(
        audit_path,
        {
            "dateRange": "20260710_20260710",
            "counts": {
                "candidateRowsScanned": 1,
                "completeRows": 1,
                "traceableRows": 1,
                "resultUnconfirmedRows": 0,
                "candidateIdDuplicateCount": 0,
            },
            "quality": {"classification": "trace_warning"},
            "rows": rows,
        },
    )
    _write_json(
        gate_path,
        {
            "dateRange": "20260710_20260710",
            "counts": {
                "observationDays": 1,
                "shadowCandidateCount": 1,
                "frozenCandidateCount": 0,
                "settledCandidateCount": 0,
                "strictEligibleCandidateCount": 0,
                "duplicateCandidateIdCount": 0,
                "preDeadlineTrueCount": 0,
                "settledCandidateCountAll": 1,
            },
            "metrics": {
                "traceCoverage": 1.0,
                "preDeadlineOddsCoverage": 0.0,
                "settlementCoverage": 1.0,
                "hitRate": None,
                "maxDrawdown": None,
            },
            "quality": {"classification": "live_evidence_blocked"},
            "blockers": ["strict_candidate_count_zero_missing_metadata"],
        },
    )
    _write_json(summary_path, {"summary": {"dateRange": "20260710_20260710", "days": 1}})

    payload = module.build_live_evidence_burn_in(
        candidate_trace_audit_path=audit_path,
        live_evidence_gate_path=gate_path,
        live_summary_path=summary_path,
    )

    assert payload["quality"]["classification"] == "burn_in_warning"
    assert payload["quality"]["productionAdoptionAllowed"] is False
    assert payload["inputSanity"]["phase6GateStatus"] == "live_evidence_blocked"
    assert payload["counts"]["shadowCandidateCount"] == 1
    assert payload["counts"]["legacyCandidateCount"] == 1
    assert payload["counts"]["strictCandidateCount"] == 0
    assert payload["counts"]["legacySettledCandidateCount"] == 1
    assert payload["counts"]["strictSettledCandidateCount"] == 0
    assert payload["coverage"]["strictMetadataCoverage"] == 0.0
    assert payload["coverage"]["legacyTraceCoverage"] == 1.0
    assert payload["lifecycle"]["shadowLifecycleState"] == payload["lifecycle"]["legacyLifecycleState"]
    assert payload["lifecycle"]["legacyLifecycleState"] == "first_candidate_settled"
    assert payload["lifecycle"]["strictLifecycleState"] == "awaiting_first_candidate"
    assert payload["lifecycle"]["overallLifecycleState"] == "legacy_only_flow"
    assert payload["lifecycle"]["burnInState"] == "burn_in_warning"
    assert payload["lifecycle"]["burnInReady"] is False
    assert "strict_settled_candidate_count_below_10" in payload["burnInBlockers"]
    assert "strict_metadata_coverage_below_1_0" in payload["burnInBlockers"]
    assert payload["legacyWarnings"] == []
    assert payload["lifecycle"]["currentBlockingStage"] == "legacy_only_flow"
    assert payload["projection"]["daysTo30StrictSettled"] is None
    assert payload["forwardPathAudit"]["morning_prediction"]["status"] == "connected"
    assert payload["forwardPathAudit"]["prediction_sheet"]["status"] == "connected"
    assert payload["forwardPathAudit"]["paper_shadow_candidate_generation"]["status"] == "connected"
    assert payload["forwardPathAudit"]["freeze_precheck"]["status"] == "connected"
    assert payload["forwardPathAudit"]["frozen_ledger_append"]["status"] == "connected"
    assert payload["forwardPathAudit"]["settlement_join"]["status"] == "conditional"
    assert payload["forwardPathAudit"]["backfill_prediction_generation"]["status"] == "not_applicable"

    monkeypatch.setattr(module, "OUT_JSON", tmp_path / "live_evidence_burn_in.json")
    monkeypatch.setattr(module, "OUT_CSV", tmp_path / "live_evidence_burn_in.csv")
    monkeypatch.setattr(module, "OUT_MD", tmp_path / "live_evidence_burn_in.md")
    module._write_outputs(payload)

    assert module.OUT_JSON.exists()
    assert module.OUT_CSV.exists()
    assert module.OUT_MD.exists()
    assert "Live Evidence Burn-in" in module.OUT_MD.read_text(encoding="utf-8")


def test_burn_in_becomes_ready_with_ten_strict_settled_candidates(tmp_path: Path) -> None:
    audit_path = tmp_path / "candidate_trace_audit.json"
    gate_path = tmp_path / "live_evidence_gate.json"
    summary_path = tmp_path / "live_operation_summary.json"
    rows = [_strict_row(candidate_id=f"c{index}", hit=index % 2 == 0) for index in range(10)]
    rows.append(_legacy_row(candidate_id="legacy1", settled=False))
    _write_json(
        audit_path,
        {
            "dateRange": "20260710_20260710",
            "counts": {
                "candidateRowsScanned": 11,
                "completeRows": 10,
                "traceableRows": 10,
                "resultUnconfirmedRows": 0,
                "candidateIdDuplicateCount": 0,
            },
            "quality": {"classification": "trace_ready"},
            "rows": rows,
        },
    )
    _write_json(
        gate_path,
        {
            "dateRange": "20260710_20260710",
            "counts": {
                "observationDays": 61,
                "shadowCandidateCount": 11,
                "frozenCandidateCount": 10,
                "settledCandidateCount": 10,
                "strictEligibleCandidateCount": 10,
                "duplicateCandidateIdCount": 0,
                "preDeadlineTrueCount": 10,
                "settledCandidateCountAll": 10,
            },
            "metrics": {
                "traceCoverage": 1.0,
                "preDeadlineOddsCoverage": 1.0,
                "settlementCoverage": 0.909091,
                "hitRate": 0.5,
                "maxDrawdown": 0.0,
            },
            "quality": {"classification": "live_evidence_ready"},
            "blockers": [],
        },
    )
    _write_json(summary_path, {"summary": {"dateRange": "20260710_20260710", "days": 10}})

    payload = module.build_live_evidence_burn_in(
        candidate_trace_audit_path=audit_path,
        live_evidence_gate_path=gate_path,
        live_summary_path=summary_path,
    )

    assert payload["quality"]["classification"] == "burn_in_ready"
    assert payload["quality"]["liveShadowReady"] is True
    assert payload["lifecycle"]["burnInReady"] is True
    assert payload["lifecycle"]["burnInState"] == "burn_in_ready"
    assert payload["lifecycle"]["strictLifecycleState"] == "burn_in_ready"
    assert payload["counts"]["strictCandidateCount"] == 10
    assert payload["counts"]["strictSettledCandidateCount"] == 10
    assert payload["counts"]["legacySettlementJoinFailedCount"] == 1
    assert payload["counts"]["strictSettlementJoinFailedCount"] == 0
    assert payload["coverage"]["strictMetadataCoverage"] == 1.0
    assert payload["burnInBlockers"] == []
    assert payload["legacyWarnings"] == ["legacy_settlement_join_failure_present"]
    assert payload["lifecycle"]["currentBlockingStage"] == "ready_for_burn_in"
    assert payload["forwardPathAudit"]["settlement_join"]["status"] == "conditional"
    assert payload["projection"]["daysTo30StrictSettled"] == 183.0


def test_main_dry_run_does_not_write_outputs(tmp_path: Path, monkeypatch, capsys) -> None:
    audit_path = tmp_path / "candidate_trace_audit.json"
    gate_path = tmp_path / "live_evidence_gate.json"
    summary_path = tmp_path / "live_operation_summary.json"
    _write_json(audit_path, {"counts": {"candidateRowsScanned": 0, "completeRows": 0, "traceableRows": 0, "candidateIdDuplicateCount": 0}, "rows": []})
    _write_json(gate_path, {"counts": {"observationDays": 0, "shadowCandidateCount": 0, "frozenCandidateCount": 0, "settledCandidateCount": 0, "strictEligibleCandidateCount": 0, "duplicateCandidateIdCount": 0, "preDeadlineTrueCount": 0, "settledCandidateCountAll": 0}, "metrics": {}, "quality": {"classification": "live_evidence_blocked"}})
    _write_json(summary_path, {"summary": {"dateRange": "20260710_20260710", "days": 0}})

    monkeypatch.setattr(module, "OUT_JSON", tmp_path / "out.json")
    monkeypatch.setattr(module, "OUT_CSV", tmp_path / "out.csv")
    monkeypatch.setattr(module, "OUT_MD", tmp_path / "out.md")

    exit_code = module.main(
        [
            "--dry-run",
            "--candidate-trace-audit",
            str(audit_path),
            "--live-evidence-gate",
            str(gate_path),
            "--live-summary",
            str(summary_path),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert not module.OUT_JSON.exists()
    assert not module.OUT_CSV.exists()
    assert not module.OUT_MD.exists()
    assert '"reportType": "live_evidence_burn_in"' in captured.out


def test_burn_in_uses_latest_audit_target_date_not_file_mtime(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "candidate_trace_audit.json"
    gate_path = tmp_path / "live_evidence_gate.json"
    summary_path = tmp_path / "live_operation_summary.json"
    _write_json(audit_path, {"counts": {"candidateRowsScanned": 0, "completeRows": 0, "traceableRows": 0}, "rows": []})
    _write_json(gate_path, {"counts": {}, "metrics": {}, "quality": {"classification": "live_evidence_blocked"}})
    _write_json(summary_path, {"summary": {"days": 0}})

    old_audit = tmp_path / "strict_evidence_daily_audit_2026-07-09.json"
    new_audit = tmp_path / "strict_evidence_daily_audit_2026-07-10.json"
    _write_json(
        old_audit,
        {
            "auditSchemaVersion": "1.0",
            "targetDate": "2026-07-09",
            "primaryBlockingReason": "expected_no_candidate",
            "currentBlockingStage": "expected_no_candidate",
            "watchdog": {"lastCandidateCreatedAt": "2026-07-09T10:00:00"},
        },
    )
    _write_json(
        new_audit,
        {
            "auditSchemaVersion": "1.0",
            "targetDate": "2026-07-10",
            "primaryBlockingReason": "missing_odds",
            "secondaryBlockingReasons": ["missing_metadata"],
            "currentBlockingStage": "missing_odds",
            "watchdog": {"lastCandidateCreatedAt": "2026-07-10T11:00:00"},
        },
    )
    os.utime(old_audit, (2_000_000_000, 2_000_000_000))
    os.utime(new_audit, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(module, "MONITORING_ROOT", tmp_path)
    selected_path, _ = module._latest_strict_daily_audit()
    assert selected_path == new_audit

    payload = module.build_live_evidence_burn_in(
        candidate_trace_audit_path=audit_path,
        live_evidence_gate_path=gate_path,
        live_summary_path=summary_path,
        strict_daily_audit_path=new_audit,
    )

    assert payload["sources"]["strictDailyAudit"] == str(new_audit)
    assert payload["inputSanity"]["strictDailyAuditTargetDate"] == "2026-07-10"
    assert payload["inputSanity"]["strictDailyAuditPrimaryBlockingReason"] == "missing_odds"
    assert payload["lifecycle"]["currentBlockingStage"] == "missing_odds"
    assert payload["watchdog"]["lastCandidateCreatedAt"] == "2026-07-10T11:00:00"
