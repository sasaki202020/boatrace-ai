from __future__ import annotations

import json
from pathlib import Path

from src.evaluation import audit_historical_inputs
from src.pipeline import backfill_predictions


def test_historical_input_audit_detects_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(backfill_predictions, "RAW_OFFICIAL_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")

    raw_dir = tmp_path / "data" / "raw" / "official" / "20260420" / "24"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "racelist_1.html").write_text("<html>racelist</html>", encoding="utf-8")

    normalized_dir = tmp_path / "data" / "normalized" / "20260420" / "24"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "race_1.json").write_text(
        json.dumps(
            {
                "date": "20260420",
                "jcd": "24",
                "rno": 1,
                "boats": [{}],
                "odds3t": {"1-2-3": 12.3},
                "beforeInfo": {"weather": {"sky": "晴"}},
                "result": {"raceStatus": "ok", "trifectaCombo": "1-2-3", "trifectaPayout": 1230},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ui_dir = tmp_path / "data" / "ui" / "20260420"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "raceyosou_24.json").write_text(json.dumps({"date": "20260420", "jcd": "24", "races": [{"raceNumber": 1, "aiPredictions": [{"combo": "1-2-3"}]}]}, ensure_ascii=False, indent=2), encoding="utf-8")

    pred_dir = tmp_path / "data" / "predictions" / "20260420"
    pred_dir.mkdir(parents=True, exist_ok=True)
    (pred_dir / "frozen_bets_24.json").write_text(json.dumps({"date": "20260420", "jcd": "24", "races": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    backfill_dir = tmp_path / "data" / "predictions_backfill" / "20260420"
    backfill_dir.mkdir(parents=True, exist_ok=True)
    (backfill_dir / "backfilled_bets_24.json").write_text(json.dumps({"date": "20260420", "jcd": "24", "races": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    result = audit_historical_inputs.audit_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="24")
    row = result["rows"][0]
    assert row["hasRawRacelist"] is True
    assert row["hasNormalizedRace"] is True
    assert row["hasUiJson"] is True
    assert row["hasOdds"] is True
    assert row["hasResult"] is True
    assert row["hasFrozenBets"] is True
    assert row["hasBackfilledBets"] is True
    assert row["canBackfillOddsStage"] is True
    assert row["canSettle"] is True


def test_historical_input_audit_reports_missing_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(backfill_predictions, "RAW_OFFICIAL_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")

    result = audit_historical_inputs.audit_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="24")
    row = result["rows"][0]
    assert row["missingReason"] == "no_input_files"
