from __future__ import annotations

import json
from pathlib import Path

from src.pipeline import daily_report as daily_report_mod
from src.pipeline import pipeline_utils


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_daily_report_reads_settlement_and_zero_stake(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(daily_report_mod, "REPORTS_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(daily_report_mod, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(pipeline_utils, "REPORTS_ROOT", tmp_path / "reports" / "daily")

    settlement = {
        "date": "20260425",
        "venues": ["24"],
        "raceCount": 1,
        "resultReadyCount": 1,
        "resultMissingCount": 0,
        "buyCount": 3,
        "frozenStakeAmount": 300.0,
        "settledBetCount": 2,
        "settledStakeAmount": 200.0,
        "unresolvedBetCount": 0,
        "unresolvedStakeAmount": 0.0,
        "voidBetCount": 0,
        "voidStakeAmount": 0.0,
        "watchCount": 1,
        "skipCount": 2,
        "betCount": 3,
        "hitCount": 2,
        "missCount": 1,
        "stakeAmount": 300.0,
        "payoutAmount": 1180.0,
        "profit": 980.0,
        "roi": 4.9,
        "settledRoi": 4.9,
        "hitRate": 0.6667,
        "resultsStatus": "ok",
        "missingCount": 0,
        "generatedAt": "2026-04-25T12:00:00",
        "warnings": ["high_buy_count"],
        "settlements": [],
    }
    _write_json(tmp_path / "reports" / "daily" / "20260425_settlement.json", settlement)
    (tmp_path / "reports" / "errors").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "errors" / "20260425_errors.jsonl").write_text(
        json.dumps({"type": "result_parse_partial"}) + "\n",
        encoding="utf-8",
    )

    report = daily_report_mod.daily_report(target_date="2026-04-25", jcd="all")
    assert report["roi"] == 4.9
    assert report["settledRoi"] == 4.9
    assert report["hitRate"] == 0.6667
    assert report["resultsStatus"] == "ok"
    assert "high_buy_count" in report["warnings"]
    canonical_dir = tmp_path / "reports" / "daily" / "2026-04-25"
    assert (canonical_dir / "daily_report.json").exists()
    assert (canonical_dir / "daily_summary.json").exists()


def test_daily_report_handles_zero_stake(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(daily_report_mod, "REPORTS_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(daily_report_mod, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(pipeline_utils, "REPORTS_ROOT", tmp_path / "reports" / "daily")

    settlement = {
        "date": "20260425",
        "venues": ["24"],
        "raceCount": 1,
        "resultReadyCount": 0,
        "resultMissingCount": 1,
        "buyCount": 0,
        "frozenStakeAmount": 0.0,
        "settledBetCount": 0,
        "settledStakeAmount": 0.0,
        "unresolvedBetCount": 1,
        "unresolvedStakeAmount": 100.0,
        "voidBetCount": 0,
        "voidStakeAmount": 0.0,
        "watchCount": 1,
        "skipCount": 2,
        "betCount": 0,
        "hitCount": 0,
        "missCount": 0,
        "stakeAmount": 0.0,
        "payoutAmount": 0.0,
        "profit": 0.0,
        "roi": None,
        "settledRoi": None,
        "hitRate": None,
        "resultsStatus": "missing",
        "missingCount": 1,
        "generatedAt": "2026-04-25T12:00:00",
        "warnings": [],
        "settlements": [],
    }
    _write_json(tmp_path / "reports" / "daily" / "20260425_settlement.json", settlement)
    report = daily_report_mod.daily_report(target_date="2026-04-25", jcd="all")
    assert report["stakeAmount"] == 0.0
    assert report["payoutAmount"] == 0.0
    assert report["hitRate"] is None
    assert report["roi"] is None
    assert report["unresolvedBetCount"] == 1
    assert report["voidBetCount"] == 0


def test_daily_report_zero_result_ready_does_not_emit_negative_roi(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(daily_report_mod, "REPORTS_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(daily_report_mod, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(pipeline_utils, "REPORTS_ROOT", tmp_path / "reports" / "daily")

    settlement = {
        "date": "20260425",
        "venues": ["24"],
        "raceCount": 1,
        "resultReadyCount": 0,
        "resultMissingCount": 1,
        "buyCount": 36,
        "frozenStakeAmount": 3600.0,
        "settledBetCount": 0,
        "settledStakeAmount": 0.0,
        "unresolvedBetCount": 36,
        "unresolvedStakeAmount": 3600.0,
        "voidBetCount": 0,
        "voidStakeAmount": 0.0,
        "betCount": 36,
        "hitCount": 0,
        "missCount": 0,
        "stakeAmount": 3600.0,
        "payoutAmount": 0.0,
        "profit": 0.0,
        "roi": None,
        "settledRoi": None,
        "hitRate": None,
        "resultsStatus": "missing",
        "missingCount": 1,
        "generatedAt": "2026-04-25T12:00:00",
        "warnings": [],
        "settlements": [],
    }
    _write_json(tmp_path / "reports" / "daily" / "20260425_settlement.json", settlement)
    report = daily_report_mod.daily_report(target_date="2026-04-25", jcd="all")
    assert report["roi"] is None
    assert report["hitRate"] is None
    assert report["unresolvedBetCount"] == 36
    assert report["voidBetCount"] == 0
    assert report["parseErrorCount"] == 0
