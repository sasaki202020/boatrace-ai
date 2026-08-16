from __future__ import annotations

import json
from pathlib import Path

import scripts.build_candidate_trace_audit as module


def _configure_isolated_roots(tmp_path, monkeypatch, *, date_range: str) -> None:
    reports_root = tmp_path / "reports"
    monitoring_root = reports_root / "monitoring"
    quality_path = monitoring_root / "candidate_quality_review.json"
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    quality_path.write_text(json.dumps({"dateRange": date_range}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(module, "REPORTS_DAILY_ROOT", reports_root / "daily")
    monkeypatch.setattr(module, "REPORTS_PRED_ROOT", reports_root / "predictions")
    monkeypatch.setattr(module, "REPORTS_MONITORING_ROOT", monitoring_root)
    monkeypatch.setattr(module, "CANDIDATE_QUALITY_REVIEW", quality_path)
    monkeypatch.setattr(module, "PAPER_VALIDATION_SUMMARY", monitoring_root / "paper_validation_summary.json")
    monkeypatch.setattr(module, "OUT_JSON", monitoring_root / "candidate_trace_audit.json")
    monkeypatch.setattr(module, "OUT_MD", monitoring_root / "candidate_trace_audit.md")
    monkeypatch.setattr(module, "OUT_CSV", monitoring_root / "candidate_trace_rows.csv")


def test_build_candidate_trace_audit_one_day(tmp_path, monkeypatch):
    _configure_isolated_roots(tmp_path, monkeypatch, date_range="20260630_20260630")

    audit = module.build_candidate_trace_audit(start="2026-06-30", end="2026-06-30")
    summary = audit["summary"]

    assert audit["dateRange"] == {"start": "2026-06-30", "end": "2026-06-30"}
    assert summary["counts"]["candidateRowsScanned"] == 0
    assert summary["counts"]["completeRows"] == 0
    assert summary["counts"]["resultUnconfirmedRows"] == 0
    assert summary["counts"]["candidateIdDuplicateCount"] == 0
    assert summary["counts"]["traceCoverage"] is None
    assert summary["quality"]["classification"] == "trace_warning"
    assert summary["traceStatusCounts"] == {}
    assert summary["traceReasonCounts"] == {}
    assert summary["traceReasonCategoryCounts"]["complete"] == 0
    assert "legacy_field_missing" in summary["traceReasonCategoryCounts"]
    assert summary["canonicalMissingCounts"]["policyVersionMissing"] >= 0
    assert summary["canonicalMissingCounts"]["oddsCapturedAtMissing"] >= 0
    assert audit["rows"] == []

    exit_code = module._main(["--start", "2026-06-30", "--end", "2026-06-30"])
    assert exit_code == 0
    assert module.OUT_JSON.exists()
    assert module.OUT_MD.exists()
    assert module.OUT_CSV.exists()

    payload = json.loads(module.OUT_JSON.read_text(encoding="utf-8"))
    assert payload["counts"]["candidateRowsScanned"] == 0
    assert payload["traceStatusCounts"] == {}
    assert payload["counts"]["candidateIdDuplicateCount"] == 0
    assert payload["traceReasonCategoryCounts"]["complete"] == 0


def test_build_candidate_trace_audit_defaults_to_monitoring_range(tmp_path, monkeypatch):
    _configure_isolated_roots(tmp_path, monkeypatch, date_range="20260425_20260708")

    assert module._infer_date_range(None, None) == ("2026-04-25", "2026-07-08")
