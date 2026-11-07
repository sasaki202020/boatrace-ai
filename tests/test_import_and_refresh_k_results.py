from __future__ import annotations

import uuid
from pathlib import Path

from src.pipeline import import_and_refresh_k_results


def test_import_and_refresh_k_results_calls_steps_and_reports_before_after(monkeypatch) -> None:
    tmp_root = Path.home() / ".codex" / "memories" / "k_result_tests" / f"refresh_{uuid.uuid4().hex}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    calls: list[str] = []

    monkeypatch.setattr(import_and_refresh_k_results, "REPORT_ROOT", tmp_root / "reports" / "backtest")
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "check_k_inbox",
        lambda **kwargs: (
            calls.append("check_k_inbox"),
            {
                "summary": {
                    "inputDirExists": True,
                    "txtFileCount": 1,
                    "zipFileCount": 0,
                    "totalEntries": 1,
                    "importTargetCount": 1,
                    "skipTargetCount": 0,
                    "invalidTargetCount": 0,
                    "missingChecklistCount": 1,
                    "recommendedNextAction": "run_import_k_results",
                },
                "rows": [],
                "files": {"json": str(tmp_root / "reports" / "backtest" / "k_inbox_check.json"), "csv": str(tmp_root / "reports" / "backtest" / "k_inbox_check.csv")},
            },
        )[1],
    )

    monkeypatch.setattr(
        import_and_refresh_k_results,
        "audit_k_result_coverage",
        lambda **kwargs: (calls.append("audit_k_result_coverage"), {"summary": {"daysWithKFile": 11, "daysMissingKFile": 14, "missingDates": ["20260406"], "resultTxtOkCount": 1201, "resultTxtMissingCount": 26}, "rows": []})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "run_backtest_range",
        lambda **kwargs: (
            calls.append("run_backtest_range"),
            {"summary": {"backfillSettledBetCount": 153 if len([c for c in calls if c == "run_backtest_range"]) == 1 else 220, "settlementCoverage": 1.0, "canTuneWithBackfill": False, "remainingSettledBetCountNeeded": 80, "resultSourceBreakdown": {"official_txt_k": 3}}, "tuning": {"canTuneWithBackfill": False}},
        )[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "import_k_results",
        lambda **kwargs: (calls.append("import_k_results"), {"summary": {"importedFileCount": 2, "skippedFileCount": 1, "parseErrorFileCount": 0}, "files": {"json": str(tmp_root / "reports" / "backtest" / "k_result_import_manifest.json")}})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "collect_official_k_results_range",
        lambda **kwargs: (calls.append("collect_official_k_results_range"), {"summary": {"resultTxtOkCount": 1201}, "details": [], "normalized": []})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "collect_historical_inputs",
        lambda **kwargs: (calls.append("collect_historical_inputs"), {"summary": {"resultTxtOkCount": 1201}, "details": [], "normalized": []})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "refresh_k_backtest",
        lambda **kwargs: (calls.append("refresh_k_backtest"), {"summary": {"remainingSettledBetCountNeeded": 80, "estimatedAdditionalKDaysNeeded": 6, "canTuneWithBackfill": False, "recommendedNextAction": "collect_missing_k_files_and_rerun"}, "files": {"json": str(tmp_root / "reports" / "backtest" / "20260401_20260425_k_refresh_summary.json")}})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "export_missing_k_checklist",
        lambda **kwargs: (calls.append("export_missing_k_checklist"), {"summary": {"totalMissing": 1}, "rows": [], "files": {"md": str(tmp_root / "reports" / "backtest" / "20260401_20260425_missing_k_checklist.md"), "csv": str(tmp_root / "reports" / "backtest" / "20260401_20260425_missing_k_checklist.csv")}})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "audit_historical_inputs",
        lambda **kwargs: (calls.append("audit_historical_inputs"), {"summary": {"rows": 1}, "rows": []})[1],
    )
    monkeypatch.setattr(
        import_and_refresh_k_results,
        "compare_prediction_sources",
        lambda **kwargs: (calls.append("compare_prediction_sources"), {"comparison": {"sources": []}})[1],
    )

    result = import_and_refresh_k_results.import_and_refresh_k_results(
        input_dir="data/inbox/k_results",
        start_date="20260401",
        end_date="20260425",
        jcd="all",
        stake=100,
        target_dir=str(tmp_root / "raw" / "official" / "results"),
    )
    summary = result["summary"]

    assert calls[0] == "check_k_inbox"
    assert calls[1] == "audit_k_result_coverage"
    assert "import_k_results" in calls
    assert "refresh_k_backtest" in calls
    assert calls[-1] == "compare_prediction_sources"
    assert summary["daysWithKFileBefore"] == 11
    assert summary["daysWithKFileAfter"] == 11
    assert summary["backfillSettledBetCountBefore"] == 153
    assert summary["backfillSettledBetCountAfter"] == 220
    assert summary["remainingSettledBetCountNeeded"] == 80
    assert summary["canTuneWithBackfill"] is False
    assert Path(result["files"]["json"]).exists()
