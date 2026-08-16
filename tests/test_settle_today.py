from __future__ import annotations

import json
from pathlib import Path

from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file
from src.evaluation import settle_results


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _setup_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settle_results, "ROOT", tmp_path)
    monkeypatch.setattr(settle_results, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(settle_results, "NORM_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(settle_results, "REPORT_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(settle_results, "ERRORS_ROOT", tmp_path / "reports" / "errors")


def _frozen_payload() -> dict:
    return {
        "date": "20260425",
        "jcd": "24",
        "venue": "大村",
        "stage": "odds",
        "generatedAt": "2026-04-25T00:00:00",
        "modelVersion": "baseline_rule_v1",
        "predictionHash": "abc123",
        "races": [
            {"rno": 1, "bets": [{"combo": "1-2-3", "decision": "BUY", "stake": 100, "predictionHash": "a"}]},
            {"rno": 2, "bets": [{"combo": "1-2-4", "decision": "BUY", "stake": 100, "predictionHash": "b"}]},
            {"rno": 3, "bets": [{"combo": "1-2-5", "decision": "BUY", "stake": 100, "predictionHash": "c"}]},
            {"rno": 4, "bets": [{"combo": "1-2-6", "decision": "BUY", "stake": 100, "predictionHash": "d"}]},
            {"rno": 5, "bets": [{"combo": "1-3-4", "decision": "BUY", "stake": 100, "predictionHash": "e"}]},
            {"rno": 6, "bets": [{"combo": "1-3-5", "decision": "BUY", "stake": 100, "predictionHash": "f"}]},
        ],
    }


def test_settle_today_statuses_and_accounting(tmp_path, monkeypatch) -> None:
    _setup_paths(tmp_path, monkeypatch)
    _write_json(tmp_path / "data" / "predictions" / "20260425" / "frozen_bets_all.json", _frozen_payload())
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_1.json",
        {"result": {"dataStatus": "ok", "trifectaCombo": "1-2-3", "trifectaPayout": 590, "raceStatus": "ok"}, "data_status": {"result": "ok"}, "source": {"resultUrl": "https://example.invalid"}},
    )
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_2.json",
        {"result": {"dataStatus": "ok", "trifectaCombo": "1-2-3", "trifectaPayout": 590, "raceStatus": "ok"}, "data_status": {"result": "ok"}, "source": {"resultUrl": "https://example.invalid"}},
    )
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_3.json",
        {"result": {"dataStatus": "pending", "raceStatus": "pending"}, "data_status": {"result": "pending"}, "source": {"resultUrl": "https://example.invalid"}},
    )
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_4.json",
        {"result": {"dataStatus": "missing", "raceStatus": "missing"}, "data_status": {"result": "missing"}, "source": {"resultUrl": "https://example.invalid"}},
    )
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_5.json",
        {"result": {"dataStatus": "parse_error", "raceStatus": "parse_error"}, "data_status": {"result": "parse_error"}, "source": {"resultUrl": "https://example.invalid"}},
    )
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_6.json",
        {"result": {"dataStatus": "refund", "raceStatus": "refund", "trifectaCombo": "1-3-5", "trifectaPayout": 0}, "data_status": {"result": "refund"}, "source": {"resultUrl": "https://example.invalid"}},
    )

    summary = settle_results.settle_daily_predictions(date="20260425", jcd="all", stake_per_buy=100)
    assert summary["betCount"] == 6
    assert summary["frozenStakeAmount"] == 600.0
    assert summary["settledBetCount"] == 2
    assert summary["settledStakeAmount"] == 200.0
    assert summary["unresolvedBetCount"] == 3
    assert summary["unresolvedStakeAmount"] == 300.0
    assert summary["voidBetCount"] == 1
    assert summary["voidStakeAmount"] == 100.0
    assert summary["hitCount"] == 1
    assert summary["missCount"] == 1
    assert summary["payoutAmount"] == 590.0
    assert summary["profit"] == 390.0
    assert summary["settledRoi"] == 2.95
    assert summary["hitRate"] == 0.5
    bets = summary["bets"]
    statuses = {row["rno"]: row["settleStatus"] for row in bets}
    assert statuses[1] == "hit"
    assert statuses[2] == "miss"
    assert statuses[3] == "pending"
    assert statuses[4] == "no_result"
    assert statuses[5] == "parse_error"
    assert statuses[6] == "void"


def test_settle_today_all_unresolved_has_null_roi(tmp_path, monkeypatch) -> None:
    _setup_paths(tmp_path, monkeypatch)
    payload = _frozen_payload()
    payload["races"] = [{"rno": 1, "bets": [{"combo": "1-2-3", "decision": "BUY", "stake": 100, "predictionHash": "a"}]}]
    _write_json(tmp_path / "data" / "predictions" / "20260425" / "frozen_bets_all.json", payload)
    _write_json(
        tmp_path / "data" / "normalized" / "20260425" / "24" / "race_1.json",
        {"result": {"dataStatus": "missing", "raceStatus": "missing"}, "data_status": {"result": "missing"}, "source": {"resultUrl": "https://example.invalid"}},
    )

    summary = settle_results.settle_daily_predictions(date="20260425", jcd="all", stake_per_buy=100)
    assert summary["resultReadyCount"] == 0
    assert summary["resultMissingCount"] == 1
    assert summary["frozenStakeAmount"] == 100.0
    assert summary["settledStakeAmount"] == 0.0
    assert summary["unresolvedStakeAmount"] == 100.0
    assert summary["settledRoi"] is None
    assert summary["roi"] is None
    assert summary["hitRate"] is None
    assert summary["missCount"] == 0


def test_settle_today_uses_official_txt_k_results(tmp_path, monkeypatch, official_k_file) -> None:
    _setup_paths(tmp_path, monkeypatch)
    k_file = official_k_file
    k_payload = parse_official_k_result_file(k_file)["races"][0]
    combo = str(k_payload["trifectaCombo"])

    _write_json(
        tmp_path / "data" / "predictions" / "20260404" / "frozen_bets_all.json",
        {
            "date": "20260404",
            "jcd": "22",
            "venue": "福岡",
            "stage": "odds",
            "generatedAt": "2026-04-04T00:00:00",
            "modelVersion": "baseline_rule_v1",
            "predictionHash": "abc123",
            "races": [{"rno": 1, "bets": [{"combo": combo, "decision": "BUY", "stake": 100, "predictionHash": "abc123"}]}],
        },
    )
    _write_json(
        tmp_path / "data" / "normalized" / "20260404" / "22" / "race_1.json",
        {
            "date": "20260404",
            "jcd": "22",
            "rno": 1,
            "result": k_payload,
            "source": {"resultSource": "official_txt_k", "kResultPath": str(k_file)},
            "data_status": {"result": "ok"},
        },
    )

    summary = settle_results.settle_daily_predictions(date="20260404", jcd="22", stake_per_buy=100)
    assert summary["settledBetCount"] == 1
    assert summary["hitCount"] == 1
    assert summary["resultTxtOkCount"] >= 1
    assert summary["resultHtmlOkCount"] == 0
    assert summary["resultSourceBreakdown"]["official_txt_k"] >= 1
