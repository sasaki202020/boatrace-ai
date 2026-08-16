from __future__ import annotations

from src.evaluation.audit_k_result_coverage import audit_k_result_coverage, k_date_from_filename, k_filename_for_date


def test_k_filename_date_roundtrip() -> None:
    assert k_filename_for_date("20260404") == "K260404.TXT"
    assert k_date_from_filename("K260404.TXT") == "20260404"
    assert k_date_from_filename("k260404.txt") == "20260404"
    assert k_date_from_filename("invalid.txt") is None


def test_k_result_coverage_audit_writes_reports(tmp_path, monkeypatch, official_k_file) -> None:
    monkeypatch.setattr("src.evaluation.audit_k_result_coverage.REPORT_ROOT", tmp_path / "reports" / "backtest")
    input_dir = official_k_file.parent

    result = audit_k_result_coverage(start_date="20260404", end_date="20260406", input_dir=str(input_dir))
    summary = result["summary"]
    rows = result["rows"]

    assert summary["totalDays"] == 3
    assert summary["daysWithKFile"] >= 1
    assert summary["daysMissingKFile"] >= 1
    assert "20260406" in summary["missingDates"]
    assert any(row["date"] == "20260404" and row["hasKFile"] for row in rows)
    assert any(row["date"] == "20260404" and row["canUseForSettlement"] for row in rows)

    json_path = tmp_path / "reports" / "backtest" / "20260404_20260406_k_result_coverage.json"
    csv_path = tmp_path / "reports" / "backtest" / "20260404_20260406_k_result_coverage.csv"
    assert json_path.exists()
    assert csv_path.exists()
