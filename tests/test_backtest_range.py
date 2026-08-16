from __future__ import annotations

import csv
import json
from pathlib import Path

from src.evaluation import backtest_range
from src.evaluation import settle_results
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file


def _settlement_day(date_key: str, *, bets: int, settled: int, void: int, result_ok: int, parse_error: int, pending: int, missing: int, live_settled: int = 0, ui_recovered_settled: int = 0, backfill_settled: int = 0, live_bets: int = 0, ui_recovered_bets: int = 0, backfill_bets: int = 0) -> dict:
    bet_rows = []
    for idx in range(bets):
        status = "hit" if idx == 0 and settled > 0 else ("miss" if idx < settled else "pending")
        if idx >= settled and idx < settled + void:
            status = "void"
        elif idx >= settled + void and idx < settled + void + parse_error:
            status = "parse_error"
        elif idx >= settled + void + parse_error and idx < settled + void + parse_error + pending:
            status = "pending"
        elif idx >= settled + void + parse_error + pending:
            status = "no_result"
        bet_rows.append(
            {
                "date": date_key,
                "jcd": "24",
                "venue": "大村",
                "rno": 1,
                "combo": f"1-2-{3 + (idx % 3)}",
                "decision": "BUY",
                "stake": 100,
                "prob": 0.03 + idx * 0.001,
                "odds": 12 + idx,
                "expectedValue": 0.9 + idx * 0.01,
                "edge": -0.1,
                "resultRaceStatus": "ok" if idx < result_ok else ("parse_error" if idx < result_ok + parse_error else "missing"),
                "resultCombo": "1-2-3" if idx == 0 else None,
                "resultPayout": 590 if idx == 0 else 0,
                "settleStatus": status,
                "isSettled": status in {"hit", "miss"},
                "isVoid": status == "void",
                "isPending": status in {"pending", "parse_error", "no_result"},
                "hit": status == "hit",
                "payout": 590 if status == "hit" else (0 if status == "miss" else None),
                "modelVersion": "baseline_rule_v1",
                "predictionHash": f"hash-{idx}",
            }
        )
    return {
        "date": date_key,
        "jcd": "all",
        "generatedAt": "2026-04-25T12:00:00",
        "stakeUnit": 100,
        "venues": ["24"],
        "venueSummaries": [
            {
                "jcd": "24",
                "venue": "大村",
                "raceCount": 1,
                "resultReadyCount": result_ok,
                "resultMissingCount": missing,
                "resultOkCount": result_ok,
                "resultPendingCount": pending,
                "resultParseErrorCount": parse_error,
                "resultRefundCount": 0,
                "resultCanceledCount": 0,
                "resultNoContestCount": 0,
                "buyCount": bets,
                "watchCount": 0,
                "skipCount": 0,
                "hitCount": 1 if settled else 0,
                "missCount": max(0, settled - 1),
                "voidCount": void,
                "parseErrorCount": parse_error,
                "pendingCount": pending,
                "noResultCount": missing,
                "stakeAmount": bets * 100.0,
                "payoutAmount": 590.0 if settled else 0.0,
            }
        ],
        "raceCount": 1,
        "resultReadyCount": result_ok,
        "resultMissingCount": missing,
        "resultOkCount": result_ok,
        "resultPendingCount": pending,
        "resultParseErrorCount": parse_error,
        "resultRefundCount": 0,
        "resultCanceledCount": 0,
        "resultNoContestCount": 0,
        "buyCount": bets,
        "watchCount": 0,
        "skipCount": 0,
        "betCount": bets,
        "frozenStakeAmount": bets * 100.0,
        "settledBetCount": settled,
        "settledStakeAmount": settled * 100.0,
        "unresolvedBetCount": parse_error + pending + missing,
        "unresolvedStakeAmount": (parse_error + pending + missing) * 100.0,
        "voidBetCount": void,
        "voidStakeAmount": void * 100.0,
        "hitCount": 1 if settled else 0,
        "missCount": max(0, settled - 1),
        "stakeAmount": bets * 100.0,
        "payoutAmount": 590.0 if settled else 0.0,
        "profit": (590.0 if settled else 0.0) - (settled * 100.0),
        "roi": None if settled == 0 else round((590.0 if settled else 0.0) / (settled * 100.0), 4),
        "settledRoi": None if settled == 0 else round((590.0 if settled else 0.0) / (settled * 100.0), 4),
        "hitRate": None if settled == 0 else round((1 if settled else 0) / settled, 4),
        "resultsStatus": "ok" if result_ok else "partial",
        "missingCount": missing,
        "liveSettledBetCount": live_settled,
        "uiRecoveredSettledBetCount": ui_recovered_settled,
        "backfillSettledBetCount": backfill_settled,
        "liveBetCount": live_bets,
        "uiRecoveredBetCount": ui_recovered_bets,
        "backfillBetCount": backfill_bets,
        "warnings": [],
        "settlements": [],
        "bets": bet_rows,
    }


def test_backtest_range_builds_summary_and_buckets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backtest_range, "REPORT_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(backtest_range, "inspect_prediction_sources", lambda **kwargs: {"date": kwargs.get("date_key", kwargs.get("date")), "jcd": kwargs.get("jcd", "all"), "source": "frozen", "frozenState": "present", "uiState": "present", "legacyState": "missing", "hasFrozenBets": True, "hasUiJson": True, "hasAiPredictions": True, "warnings": []})
    monkeypatch.setattr(backtest_range, "settle_daily_predictions", lambda **kwargs: _settlement_day(kwargs["date"], bets=60 if kwargs["date"].endswith("21") else 4, settled=1 if kwargs["date"].endswith("20") else 0, void=1, result_ok=1 if kwargs["date"].endswith("20") else 0, parse_error=1, pending=1, missing=1))

    result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-21", jcd="all", stake_per_buy=100)
    summary = result["summary"]
    assert summary["days"] == 2
    assert summary["betCount"] > 0
    assert summary["settledBetCount"] >= 1
    assert summary["unresolvedBetCount"] > 0
    assert summary["voidBetCount"] > 0
    assert summary["settledRoi"] is not None or summary["settledBetCount"] == 0
    assert summary["hitRate"] is not None or summary["settledBetCount"] == 0
    assert "high_daily_buy_count" in summary["warnings"]
    assert "low_settlement_coverage" in summary["warnings"]
    assert (tmp_path / "reports" / "backtest" / "20260420_20260421_auto_summary.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260421_auto_by_day.csv").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260421_auto_by_venue.csv").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260421_auto_by_ev_bucket.csv").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260421_auto_by_odds_bucket.csv").exists()


def test_backtest_range_writes_coverage_and_diagnostics(tmp_path, monkeypatch) -> None:
    def fake_source_info(*, date_key: str, jcd: str = "all") -> dict:
        if date_key.endswith("20"):
            return {"date": date_key, "jcd": jcd, "source": "frozen", "frozenState": "present", "uiState": "present", "legacyState": "missing", "hasFrozenBets": True, "hasUiJson": True, "hasAiPredictions": True, "warnings": []}
        if date_key.endswith("21"):
            return {"date": date_key, "jcd": jcd, "source": "ui_recovered", "frozenState": "missing", "uiState": "present", "legacyState": "missing", "hasFrozenBets": False, "hasUiJson": True, "hasAiPredictions": True, "uiStage": "result", "warnings": ["prediction_hash_missing", "ui_recovered_predictions_used"]}
        return {"date": date_key, "jcd": jcd, "source": "missing", "frozenState": "missing", "uiState": "missing", "legacyState": "missing", "hasFrozenBets": False, "hasUiJson": False, "hasAiPredictions": False, "warnings": []}

    monkeypatch.setattr(backtest_range, "REPORT_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(backtest_range, "inspect_prediction_sources", fake_source_info)
    def fake_settle(**kwargs):
        date_key = kwargs["date"]
        return _settlement_day(
            date_key,
            bets=12,
            settled=0,
            void=1,
            result_ok=0,
            parse_error=1,
            pending=1,
            missing=1,
            live_bets=12 if date_key.endswith("20") else 0,
            ui_recovered_bets=1 if date_key.endswith("21") else 0,
        )

    monkeypatch.setattr(backtest_range, "settle_daily_predictions", fake_settle)

    result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-22", jcd="all", stake_per_buy=100)
    summary = result["summary"]
    files = result["files"]
    coverage_path = Path(files["coverage"])
    diagnostics_path = Path(files["diagnostics"])
    tuning_path = Path(files["tuning"])

    assert summary["canTuneBuyThreshold"] is False
    assert "insufficient_settled_sample_do_not_tune" in summary["warnings"]
    assert "low_settled_sample_tuning_unreliable" in summary["warnings"]
    assert coverage_path.exists()
    assert diagnostics_path.exists()
    assert tuning_path.exists()

    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    tuning = json.loads(tuning_path.read_text(encoding="utf-8"))
    assert coverage["totalDays"] == 3
    assert coverage["daysWithFrozenBets"] == 1
    assert coverage["daysWithUiRecovered"] == 1
    assert coverage["daysMissingPredictions"] == 1
    assert coverage["settlementCoverage"] is not None
    assert tuning["canTuneBuyThreshold"] is False
    assert tuning["minimumRequiredSettledBetCount"] == 100

    with diagnostics_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    reasons = {row["reason"] for row in rows}
    assert "frozen_bets_missing_and_ui_missing" in reasons or "frozen_bets_missing_but_ui_available" in reasons
    assert "ui_recovered_predictions_used" in reasons or "prediction_hash_missing" in reasons

    with (tmp_path / "reports" / "backtest" / "20260420_20260422_auto_by_ev_bucket.csv").open("r", encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    assert "canEvaluateBucket" in header
    assert "settlementCoverage" in header


def test_backtest_range_source_types_and_tuning_flags(tmp_path, monkeypatch) -> None:
    def fake_source_info(*, date_key: str, jcd: str = "all") -> dict:
        return {"date": date_key, "jcd": jcd, "source": "ui_recovered", "frozenState": "missing", "uiState": "present", "legacyState": "missing", "hasFrozenBets": False, "hasUiJson": True, "hasAiPredictions": True, "warnings": ["ui_recovered_predictions_used"]}

    def fake_settle(**kwargs):
        return _settlement_day(
            kwargs["date"],
            bets=40,
            settled=5,
            void=2,
            result_ok=2,
            parse_error=1,
            pending=1,
            missing=1,
            live_settled=3,
            ui_recovered_settled=2,
            backfill_settled=0,
            live_bets=10,
            ui_recovered_bets=30,
            backfill_bets=0,
        )

    monkeypatch.setattr(backtest_range, "REPORT_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(backtest_range, "inspect_prediction_sources", fake_source_info)
    monkeypatch.setattr(backtest_range, "settle_daily_predictions", fake_settle)

    result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100)
    summary = result["summary"]
    coverage = json.loads(Path(result["files"]["coverage"]).read_text(encoding="utf-8"))
    tuning = json.loads(Path(result["files"]["tuning"]).read_text(encoding="utf-8"))
    assert summary["liveSettledBetCount"] == 3
    assert summary["uiRecoveredSettledBetCount"] == 2
    assert summary["backfillSettledBetCount"] == 0
    assert summary["canTuneWithLiveOnly"] is False
    assert summary["canTuneWithBackfill"] is False
    assert summary["sourceType"] == "auto"
    assert summary["tuningDataSourceRecommendation"] == "continue_forward_collection"
    assert coverage["canTuneWithLiveOnly"] is False
    assert coverage["daysWithUiRecoveredBets"] == 1
    assert tuning["canTuneWithLiveOnly"] is False
    assert tuning["canTuneWithBackfill"] is False


def test_backtest_range_prediction_source_outputs_are_separated(tmp_path, monkeypatch) -> None:
    seen_sources: list[str] = []

    def fake_source_info(*, date_key: str, jcd: str = "all") -> dict:
        return {"date": date_key, "jcd": jcd, "source": "frozen", "frozenState": "present", "uiState": "present", "legacyState": "missing", "hasFrozenBets": True, "hasUiJson": True, "hasAiPredictions": True, "warnings": []}

    def fake_settle(**kwargs):
        seen_sources.append(kwargs.get("prediction_source", "auto"))
        source = kwargs.get("prediction_source", "auto")
        if source == "live":
            return _settlement_day(kwargs["date"], bets=20, settled=1, void=0, result_ok=1, parse_error=0, pending=0, missing=0, live_settled=1, live_bets=20)
        if source == "ui_recovered":
            return _settlement_day(kwargs["date"], bets=10, settled=2, void=0, result_ok=2, parse_error=0, pending=0, missing=0, ui_recovered_settled=2, ui_recovered_bets=10)
        if source == "backfill":
            return _settlement_day(kwargs["date"], bets=30, settled=3, void=0, result_ok=3, parse_error=0, pending=0, missing=0, backfill_settled=3, backfill_bets=30)
        return _settlement_day(kwargs["date"], bets=5, settled=1, void=0, result_ok=1, parse_error=0, pending=0, missing=0)

    monkeypatch.setattr(backtest_range, "REPORT_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(backtest_range, "inspect_prediction_sources", fake_source_info)
    monkeypatch.setattr(backtest_range, "settle_daily_predictions", fake_settle)

    live_result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100, prediction_source="live")
    backfill_result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100, prediction_source="backfill")
    ui_result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100, prediction_source="ui_recovered")
    auto_result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100, prediction_source="auto")
    all_result = backtest_range.run_backtest_range(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stake_per_buy=100, prediction_source="all")

    assert live_result["summary"]["sourceType"] == "live"
    assert backfill_result["summary"]["sourceType"] == "backfill"
    assert ui_result["summary"]["sourceType"] == "ui_recovered"
    assert auto_result["summary"]["sourceType"] == "auto"
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_live_summary.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_backfill_summary.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_ui_recovered_summary.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_auto_summary.json").exists()
    assert (tmp_path / "reports" / "backtest" / "20260420_20260420_source_comparison.json").exists()
    assert all_result["predictionSource"] == "all"
    assert {"live", "ui_recovered", "backfill"} <= set(all_result["sources"])
    assert "live" in seen_sources and "backfill" in seen_sources and "ui_recovered" in seen_sources


def test_backtest_range_uses_official_txt_k_results(tmp_path, monkeypatch, official_k_file) -> None:
    monkeypatch.setattr(backtest_range, "REPORT_ROOT", tmp_path / "reports" / "backtest")
    monkeypatch.setattr(settle_results, "ROOT", tmp_path)
    monkeypatch.setattr(settle_results, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(settle_results, "NORM_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(settle_results, "REPORT_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(settle_results, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(
        backtest_range,
        "inspect_prediction_sources",
        lambda **kwargs: {"date": kwargs.get("date_key", kwargs.get("date")), "jcd": kwargs.get("jcd", "22"), "source": "frozen", "frozenState": "present", "uiState": "missing", "legacyState": "missing", "hasFrozenBets": True, "hasUiJson": False, "hasAiPredictions": True, "warnings": []},
    )

    k_file = official_k_file
    k_payload = parse_official_k_result_file(k_file)["races"][0]
    combo = str(k_payload["trifectaCombo"])

    frozen_dir = tmp_path / "data" / "predictions" / "20260404"
    frozen_dir.mkdir(parents=True, exist_ok=True)
    (frozen_dir / "frozen_bets_all.json").write_text(
        json.dumps(
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
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    norm_dir = tmp_path / "data" / "normalized" / "20260404" / "22"
    norm_dir.mkdir(parents=True, exist_ok=True)
    (norm_dir / "race_1.json").write_text(
        json.dumps(
            {
                "date": "20260404",
                "jcd": "22",
                "rno": 1,
                "result": k_payload,
                "source": {"resultSource": "official_txt_k", "kResultPath": str(k_file)},
                "dataStatus": {"result": "ok"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = backtest_range.run_backtest_range(start_date="2026-04-04", end_date="2026-04-04", jcd="22", stake_per_buy=100, prediction_source="live")
    summary = result["summary"]
    tuning = json.loads(Path(result["files"]["tuning"]).read_text(encoding="utf-8"))
    assert summary["settledBetCount"] == 1
    assert summary["resultOkCount"] >= 1
    assert summary["liveSettledBetCount"] == 1
    assert summary["resultSourceBreakdown"].get("official_txt_k", 0) >= 1
    assert summary["currentBackfillSettledBetCount"] == summary["backfillSettledBetCount"]
    assert summary["remainingSettledBetCountNeeded"] == max(0, 300 - summary["backfillSettledBetCount"])
    assert tuning["resultSourceBreakdown"].get("official_txt_k", 0) >= 1
    assert "remainingSettledBetCountNeeded" in tuning
    assert Path(result["files"]["summary"]).exists()
