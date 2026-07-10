from __future__ import annotations

from src.evaluation import backtest_range, compare_prediction_sources


def _settlement(date_key: str, *, source: str) -> dict:
    bets = 10 if source == "live" else 20 if source == "ui_recovered" else 30
    settled = 1 if source == "live" else 2 if source == "ui_recovered" else 3
    return {
        "date": date_key,
        "jcd": "all",
        "generatedAt": "2026-04-25T12:00:00",
        "stakeUnit": 100,
        "venues": ["24"],
        "venueSummaries": [],
        "raceCount": 1,
        "resultReadyCount": 1,
        "resultMissingCount": 0,
        "resultOkCount": 1,
        "resultPendingCount": 0,
        "resultParseErrorCount": 0,
        "resultRefundCount": 0,
        "resultCanceledCount": 0,
        "resultNoContestCount": 0,
        "liveSettledBetCount": settled if source == "live" else 0,
        "uiRecoveredSettledBetCount": settled if source == "ui_recovered" else 0,
        "backfillSettledBetCount": settled if source == "backfill" else 0,
        "liveBetCount": bets if source == "live" else 0,
        "uiRecoveredBetCount": bets if source == "ui_recovered" else 0,
        "backfillBetCount": bets if source == "backfill" else 0,
        "liveSettlementCoverage": 0.1 if source == "live" else None,
        "backfillSettlementCoverage": 0.1 if source == "backfill" else None,
        "sourceTypeCounts": {"live_frozen": bets if source == "live" else 0, "ui_recovered": bets if source == "ui_recovered" else 0, "backfill": bets if source == "backfill" else 0, "missing": 0},
        "buyCount": bets,
        "watchCount": 0,
        "skipCount": 0,
        "betCount": bets,
        "frozenStakeAmount": float(bets * 100),
        "settledBetCount": settled,
        "settledStakeAmount": float(settled * 100),
        "unresolvedBetCount": 0,
        "unresolvedStakeAmount": 0.0,
        "voidBetCount": 0,
        "voidStakeAmount": 0.0,
        "hitCount": settled,
        "missCount": 0,
        "pendingCount": 0,
        "voidCount": 0,
        "parseErrorCount": 0,
        "noResultCount": 0,
        "stakeAmount": float(bets * 100),
        "payoutAmount": float(settled * 100),
        "profit": float((settled - bets) * 100),
        "roi": float(settled / bets) if bets else None,
        "settledRoi": float(settled / bets) if bets else None,
        "hitRate": 1.0,
        "resultsStatus": "ok",
        "errorsCount": 0,
        "missingCount": 0,
        "warnings": [],
        "settlements": [],
        "bets": [],
    }


def test_compare_prediction_sources_writes_comparison(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backtest_range, "REPORT_ROOT", tmp_path / "reports" / "backtest")

    def fake_source_info(*, date_key: str, jcd: str = "all") -> dict:
        return {"date": date_key, "jcd": jcd, "source": "frozen", "frozenState": "present", "uiState": "present", "legacyState": "missing", "hasFrozenBets": True, "hasUiJson": True, "hasAiPredictions": True, "warnings": []}

    def fake_settle(**kwargs):
        source = kwargs.get("prediction_source", "auto")
        return _settlement(kwargs["date"], source=source if source in {"live", "ui_recovered", "backfill"} else "live")

    monkeypatch.setattr(backtest_range, "inspect_prediction_sources", fake_source_info)
    monkeypatch.setattr(backtest_range, "settle_daily_predictions", fake_settle)

    result = compare_prediction_sources.compare_prediction_sources(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100)
    assert result["predictionSource"] == "all"
    assert set(result["sources"]) == {"live", "ui_recovered", "backfill"}
    comparison = result["comparison"]
    assert len(comparison["sources"]) == 3
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_source_comparison.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_source_comparison.csv").exists()
