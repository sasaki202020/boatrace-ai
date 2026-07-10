from __future__ import annotations

import json

from src.pipeline import prepare_backtest_dataset


def test_prepare_backtest_dataset_orchestrates_and_writes_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(prepare_backtest_dataset, "REPORTS_ROOT", tmp_path / "reports" / "backtest")
    calls: list[str] = []

    monkeypatch.setattr(
        prepare_backtest_dataset,
        "audit_historical_inputs",
        lambda **kwargs: calls.append("audit") or {"summary": {"stage": "audit"}, "rows": []},
    )
    monkeypatch.setattr(
        prepare_backtest_dataset,
        "collect_historical_inputs",
        lambda **kwargs: calls.append("collect") or {"summary": {"stage": "collect"}, "files": {"summary": "collect.json"}},
    )
    monkeypatch.setattr(
        prepare_backtest_dataset,
        "backfill_predictions",
        lambda **kwargs: calls.append("backfill") or {"summary": {"stage": "backfill"}, "files": {"summary": "backfill.json"}},
    )
    monkeypatch.setattr(
        prepare_backtest_dataset,
        "run_backtest_range",
        lambda **kwargs: calls.append("backtest") or {
            "summary": {"stage": "backtest", "canTuneWithBackfill": False, "liveSettledBetCount": 3},
            "files": {"tuning": "tuning.json"},
        },
    )
    monkeypatch.setattr(
        prepare_backtest_dataset,
        "compare_prediction_sources",
        lambda **kwargs: calls.append("compare") or {"summary": {"stage": "compare"}},
    )

    result = prepare_backtest_dataset.prepare_backtest_dataset(start_date="2026-04-01", end_date="2026-04-25", jcd="all")
    assert calls == ["audit", "collect", "backfill", "backtest", "compare"]
    assert result["summary"]["backtest"]["canTuneWithBackfill"] is False
    summary_path = tmp_path / "reports" / "backtest" / "20260401_20260425_prepare_summary.json"
    assert summary_path.exists()
    saved = json.loads(summary_path.read_text(encoding="utf-8"))
    assert saved["backtest"]["canTuneWithBackfill"] is False
    assert saved["compare"]["stage"] == "compare"

