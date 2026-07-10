from __future__ import annotations

import json
from pathlib import Path

import scripts.build_candidate_trace_audit as module


def test_build_candidate_trace_audit_one_day(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "OUT_JSON", tmp_path / "candidate_trace_audit.json")
    monkeypatch.setattr(module, "OUT_MD", tmp_path / "candidate_trace_audit.md")
    monkeypatch.setattr(module, "OUT_CSV", tmp_path / "candidate_trace_rows.csv")

    audit = module.build_candidate_trace_audit(start="2026-06-30", end="2026-06-30")
    summary = audit["summary"]

    assert audit["dateRange"] == {"start": "2026-06-30", "end": "2026-06-30"}
    assert summary["counts"]["candidateRowsScanned"] == 10
    assert summary["counts"]["completeRows"] == 9
    assert summary["counts"]["resultUnconfirmedRows"] == 1
    assert summary["counts"]["candidateIdDuplicateCount"] == 0
    assert summary["counts"]["traceCoverage"] is not None
    assert summary["quality"]["classification"] == "trace_warning"
    assert summary["traceStatusCounts"]["complete"] == 9
    assert summary["traceStatusCounts"]["result_unconfirmed"] == 1
    assert summary["traceReasonCounts"]["predictionHash → frozen_bets → settlement linked"] == 9
    assert summary["traceReasonCategoryCounts"]["complete"] == 9
    assert "legacy_field_missing" in summary["traceReasonCategoryCounts"]
    assert summary["canonicalMissingCounts"]["policyVersionMissing"] >= 0
    assert summary["canonicalMissingCounts"]["oddsCapturedAtMissing"] >= 0
    assert all(row["predictionHashMatch"] for row in audit["rows"])
    assert all(row["frozenExists"] for row in audit["rows"])
    assert all(row["settlementExists"] for row in audit["rows"])
    assert all(row["candidateId"] for row in audit["rows"])
    assert len({row["candidateId"] for row in audit["rows"]}) == len(audit["rows"])
    first_row = audit["rows"][0]
    for field in ("raceId", "raceDate", "venueCode", "modelVersion", "policyDecision", "guardDecision", "settlementStatus"):
        assert field in first_row

    exit_code = module._main(["--start", "2026-06-30", "--end", "2026-06-30"])
    assert exit_code == 0
    assert module.OUT_JSON.exists()
    assert module.OUT_MD.exists()
    assert module.OUT_CSV.exists()

    payload = json.loads(module.OUT_JSON.read_text(encoding="utf-8"))
    assert payload["counts"]["candidateRowsScanned"] == 10
    assert payload["traceStatusCounts"]["complete"] == 9
    assert payload["traceStatusCounts"]["result_unconfirmed"] == 1
    assert payload["counts"]["candidateIdDuplicateCount"] == 0
    assert payload["traceReasonCategoryCounts"]["complete"] == 9


def test_build_candidate_trace_audit_defaults_to_monitoring_range(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "OUT_JSON", tmp_path / "candidate_trace_audit.json")
    monkeypatch.setattr(module, "OUT_MD", tmp_path / "candidate_trace_audit.md")
    monkeypatch.setattr(module, "OUT_CSV", tmp_path / "candidate_trace_rows.csv")

    audit = module.build_candidate_trace_audit()
    summary = audit["summary"]

    assert audit["dateRange"] == {"start": "2026-04-25", "end": "2026-07-08"}
    assert summary["authoritative"]["paperCandidateCount"] == 426
    assert summary["authoritative"]["paperEligibleCandidateCount"] == 105
    assert summary["authoritative"]["paperValidationReady"] is True
    assert summary["counts"]["candidateRowsScanned"] >= 400
    assert summary["counts"]["completeRows"] >= 90
    assert summary["counts"]["traceableRows"] >= summary["counts"]["completeRows"]
    assert summary["quality"]["classification"] == "trace_warning"
