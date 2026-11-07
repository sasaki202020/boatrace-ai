from __future__ import annotations

from pathlib import Path

from src.evaluation.export_missing_k_checklist import export_missing_k_checklist


def test_missing_k_checklist_uses_missing_dates_and_priorities(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.evaluation.export_missing_k_checklist.REPORT_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(
        "src.evaluation.export_missing_k_checklist.audit_k_result_coverage",
        lambda **kwargs: {
            "summary": {"missingDates": ["20260406"], "daysWithKFile": 2, "daysMissingKFile": 1},
            "rows": [
                {"date": "20260405", "hasKFile": True},
                {"date": "20260406", "hasKFile": False},
                {"date": "20260407", "hasKFile": True},
            ],
        },
    )
    monkeypatch.setattr("src.evaluation.export_missing_k_checklist._load_reference_average", lambda date_tag: 12.5)

    result = export_missing_k_checklist(start_date="20260401", end_date="20260425", input_dir="data/raw/official/results")
    summary = result["summary"]
    rows = result["rows"]

    assert summary["totalMissing"] == 1
    assert rows[0]["expectedFileName"] == "K260406.TXT"
    assert rows[0]["priority"] == "high"
    assert rows[0]["estimatedSettledGain"] == 12.5
    assert Path(result["files"]["md"]).exists()
    assert Path(result["files"]["csv"]).exists()
