from __future__ import annotations

from pathlib import Path

import src.evaluation.paper_validation_gate as gate_module
import src.evaluation.paper_validation_summary as summary_module


ROOT = Path(__file__).resolve().parents[1]


def _configure_isolated_report_roots(tmp_path, monkeypatch) -> None:
    reports_root = tmp_path / "reports"
    monitoring_root = reports_root / "monitoring"
    repo_audit_root = reports_root / "repo_audit"
    monkeypatch.setattr(summary_module, "REPORTS_DAILY_ROOT", reports_root / "daily")
    monkeypatch.setattr(summary_module, "REPORTS_PREDICTIONS_ROOT", reports_root / "predictions")
    monkeypatch.setattr(summary_module, "REPORTS_CONSENSUS_ROOT", reports_root / "consensus")
    monkeypatch.setattr(summary_module, "REPORTS_ANALYSIS_ROOT", reports_root / "analysis")
    monkeypatch.setattr(summary_module, "REPORTS_MONITORING_ROOT", monitoring_root)
    monkeypatch.setattr(summary_module, "REPORTS_REPO_AUDIT_ROOT", repo_audit_root)
    monkeypatch.setattr(summary_module, "WATCH_PAPER_PERFORMANCE_JSON", reports_root / "analysis" / "watch_paper_performance.json")
    monkeypatch.setattr(gate_module, "REPORTS_MONITORING_ROOT", monitoring_root)
    monkeypatch.setattr(gate_module, "REPORTS_REPO_AUDIT_ROOT", repo_audit_root)


def test_paper_validation_summary_split(tmp_path, monkeypatch) -> None:
    _configure_isolated_report_roots(tmp_path, monkeypatch)
    payload = summary_module.paper_validation_summary(start_date="20260425", end_date="20260507")
    summary = payload["summary"]
    assert summary["liveSettledBetCount"] == 0
    assert summary["liveRevenueGateStatus"] == "NOT_READY"
    assert summary["paperValidationGateStatus"] == "NOT_READY"
    assert summary["paperCandidateCount"] == 0
    assert summary["paperSettledCandidateCount"] == 0


def test_paper_validation_gate_split(tmp_path, monkeypatch) -> None:
    _configure_isolated_report_roots(tmp_path, monkeypatch)
    summary_module.paper_validation_summary(start_date="20260425", end_date="20260507")
    payload = gate_module.paper_validation_gate(start_date="20260425", end_date="20260507")
    assert payload["liveRevenueGateStatus"] == "NOT_READY"
    assert payload["paperValidationGateStatus"] == "NOT_READY"
    assert payload["primaryBlocker"] == "paper_candidate_missing"


def test_predictions_banner_mentions_split() -> None:
    html = (ROOT / "src" / "web" / "static" / "predictions.html").read_text(encoding="utf-8")
    assert "本番BUYのlive検証は未開始です" in html
    assert "WATCH/PAPER/合意スコア候補の紙上検証を蓄積中です" in html
