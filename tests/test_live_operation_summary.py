from __future__ import annotations

import json
from pathlib import Path

from src.evaluation import live_operation_summary as live_operation_summary_mod


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_live_operation_summary_aggregates_multiple_days(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(live_operation_summary_mod, "ROOT", tmp_path)
    monkeypatch.setattr(live_operation_summary_mod, "REPORT_DAILY_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(live_operation_summary_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(live_operation_summary_mod, "REPORT_BACKTEST_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(live_operation_summary_mod, "PRED_ROOT", tmp_path / "data" / "predictions")

    _write_json(
        tmp_path / "reports" / "daily" / "20260425_summary.json",
        {
            "date": "20260425",
            "liveBetCount": 20,
            "liveSettledBetCount": 10,
            "liveUnresolvedBetCount": 5,
            "liveVoidBetCount": 5,
            "settledStakeAmount": 1000.0,
            "payoutAmount": 500.0,
            "hitCount": 2,
            "settledRoi": 0.5,
            "hitRate": 0.2,
            "resultParseErrorCount": 0,
            "resultReadyCount": 1,
            "resultMissingCount": 0,
            "warnings": [],
        },
    )
    _write_json(tmp_path / "reports" / "daily" / "20260425_settlement.json", {"date": "20260425"})
    _write_json(tmp_path / "data" / "predictions" / "20260425" / "frozen_bets_all.json", {"date": "20260425", "predictionHash": "abc", "venues": []})
    _write_json(
        tmp_path / "reports" / "daily" / "20260426_summary.json",
        {
            "date": "20260426",
            "liveBetCount": 50,
            "liveSettledBetCount": 30,
            "liveUnresolvedBetCount": 10,
            "liveVoidBetCount": 10,
            "settledStakeAmount": 3000.0,
            "payoutAmount": 0.0,
            "hitCount": 0,
            "settledRoi": 0.0,
            "hitRate": 0.0,
            "resultParseErrorCount": 1,
            "resultReadyCount": 2,
            "resultMissingCount": 1,
            "warnings": ["sample_warning"],
        },
    )
    _write_json(tmp_path / "reports" / "daily" / "20260426_settlement.json", {"date": "20260426"})
    _write_json(tmp_path / "data" / "predictions" / "20260426" / "frozen_bets_all.json", {"date": "20260426", "predictionHash": "def", "venues": []})
    _write_json(
        tmp_path / "reports" / "backtest" / "20260401_20260425_backfill_tuning_readiness.json",
        {"canTuneWithBackfill": False},
    )

    result = live_operation_summary_mod.live_operation_summary(start_date="20260425", end_date="20260426")
    summary = result["summary"]

    assert summary["days"] == 2
    assert summary["daysWithFrozenBets"] == 2
    assert summary["daysWithSettlement"] == 2
    assert summary["liveBetCount"] == 70
    assert summary["liveSettledBetCount"] == 40
    assert summary["liveUnresolvedBetCount"] == 15
    assert summary["liveVoidBetCount"] == 15
    assert summary["canTuneWithLiveOnly"] is False
    assert Path(result["files"]["json"]).exists()
    assert Path(result["files"]["csv"]).exists()

