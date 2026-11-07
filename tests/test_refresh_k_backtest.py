from __future__ import annotations

from pathlib import Path

from src.pipeline import refresh_k_backtest


def test_refresh_k_backtest_calls_steps_and_writes_summary(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(refresh_k_backtest, "REPORT_ROOT", tmp_path / "reports" / "backtest")

    def fake_audit_k_result_coverage(**kwargs):
        calls.append("audit_k_result_coverage")
        return {
            "summary": {
                "daysWithKFile": 2,
                "daysMissingKFile": 1,
                "missingDates": ["20260405"],
                "parsedResultTxtRaceCount": 10,
                "resultTxtOkCount": 8,
                "resultTxtMissingCount": 2,
                "resultTxtParseErrorCount": 0,
            },
            "rows": [],
        }

    def fake_collect_official_k_results_range(**kwargs):
        calls.append("collect_official_k_results_range")
        return {"summary": {"resultTxtOkCount": 8}, "details": [], "normalized": []}

    def fake_collect_historical_inputs(**kwargs):
        calls.append("collect_historical_inputs")
        return {"summary": {"resultTxtOkCount": 8}, "details": [], "normalized": []}

    def fake_audit_historical_inputs(**kwargs):
        calls.append("audit_historical_inputs")
        return {"summary": {"canSettleFromTxt": True}, "rows": []}

    backtest_calls: list[int] = []

    def fake_run_backtest_range(**kwargs):
        calls.append("run_backtest_range")
        backtest_calls.append(1)
        if len(backtest_calls) == 1:
            return {
                "summary": {
                    "backfillSettledBetCount": 120,
                    "settlementCoverage": 0.4,
                    "canTuneWithBackfill": False,
                    "remainingSettledBetCountNeeded": 180,
                    "resultSourceBreakdown": {"official_txt_k": 120},
                },
                "tuning": {"canTuneWithBackfill": False, "remainingSettledBetCountNeeded": 180},
            }
        return {
            "summary": {
                "backfillSettledBetCount": 240,
                "settlementCoverage": 0.6,
                "canTuneWithBackfill": False,
                "remainingSettledBetCountNeeded": 60,
                "resultSourceBreakdown": {"official_txt_k": 240},
            },
            "tuning": {"canTuneWithBackfill": False, "remainingSettledBetCountNeeded": 60},
        }

    def fake_compare_prediction_sources(**kwargs):
        calls.append("compare_prediction_sources")
        return {"comparison": {"sources": [{"sourceType": "backfill"}]}, "files": {}}

    monkeypatch.setattr(refresh_k_backtest, "audit_k_result_coverage", fake_audit_k_result_coverage)
    monkeypatch.setattr(refresh_k_backtest, "collect_official_k_results_range", fake_collect_official_k_results_range)
    monkeypatch.setattr(refresh_k_backtest, "collect_historical_inputs", fake_collect_historical_inputs)
    monkeypatch.setattr(refresh_k_backtest, "audit_historical_inputs", fake_audit_historical_inputs)
    monkeypatch.setattr(refresh_k_backtest, "run_backtest_range", fake_run_backtest_range)
    monkeypatch.setattr(refresh_k_backtest, "compare_prediction_sources", fake_compare_prediction_sources)

    result = refresh_k_backtest.refresh_k_backtest(
        start_date="20260401",
        end_date="20260425",
        jcd="all",
        input_dir="data/raw/official/results",
        stake=100,
    )
    summary = result["summary"]

    assert calls.count("audit_k_result_coverage") == 2
    assert calls.count("run_backtest_range") == 2
    assert calls.count("compare_prediction_sources") == 1
    assert summary["missingKDates"] == ["20260405"]
    assert summary["backfillSettledBetCountBefore"] == 120
    assert summary["backfillSettledBetCountAfter"] == 240
    assert summary["remainingSettledBetCountNeeded"] == 60
    assert summary["estimatedAdditionalKDaysNeeded"] == 1
    assert summary["canTuneWithBackfill"] is False
    assert Path(result["files"]["json"]).exists()
