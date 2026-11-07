from __future__ import annotations

import json
from pathlib import Path

from src.evaluation import audit_historical_inputs
from src.pipeline import backfill_predictions


def test_audit_historical_inputs_breaks_missing_reason_down(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(backfill_predictions, "RAW_OFFICIAL_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")
    monkeypatch.setattr(backfill_predictions, "discover_venues_for_date", lambda date8, force=False: {"date": date8, "venues": [{"jcd": "24", "venueName": "大村", "isOpen": True}], "warnings": []})

    raw_dir = tmp_path / "data" / "raw" / "official" / "20260420" / "24"
    raw_dir.mkdir(parents=True, exist_ok=True)

    result = audit_historical_inputs.audit_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="24")
    row = result["rows"][0]
    assert row["missingReason"] in {"raw_racelist_missing", "no_prediction_source", "date_not_collected", "date_not_held"}
    assert row["missingReason"] != "no_input_files"


def test_audit_historical_inputs_reports_stage_specific_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(backfill_predictions, "RAW_OFFICIAL_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")

    raw_dir = tmp_path / "data" / "raw" / "official" / "20260420" / "24"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "racelist_01.html").write_text("<html>racelist</html>", encoding="utf-8")
    normalized_dir = tmp_path / "data" / "normalized" / "20260420" / "24"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "race_1.json").write_text('{"date":"20260420","jcd":"24","rno":1,"odds3t":{"1-2-3":12.3}}', encoding="utf-8")

    result = audit_historical_inputs.audit_historical_inputs(start_date="2026-04-20", end_date="2026-04-20", jcd="24")
    row = result["rows"][0]
    assert row["hasRawRacelist"] is True
    assert row["hasOdds"] is True
    assert row["missingReason"] in {"prediction_missing", "result_missing", "no_prediction_source", ""}


def test_audit_historical_inputs_marks_txt_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(backfill_predictions, "RAW_OFFICIAL_ROOT", tmp_path / "data" / "raw" / "official")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")
    monkeypatch.setattr(backfill_predictions, "discover_venues_for_date", lambda date8, force=False: {"date": date8, "venues": [{"jcd": "22", "venueName": "福岡", "isOpen": True}], "warnings": []})

    norm_dir = tmp_path / "data" / "normalized" / "20260404" / "22"
    norm_dir.mkdir(parents=True, exist_ok=True)
    (norm_dir / "race_1.json").write_text(
        json.dumps(
            {
                "date": "20260404",
                "jcd": "22",
                "rno": 1,
                "venue": "福岡",
                "result": {"raceStatus": "ok", "trifectaCombo": "1-2-3", "trifectaPayout": 470, "resultSource": "official_txt_k"},
                "source": {"resultSource": "official_txt_k", "kResultPath": "data/raw/official/results/K260404.TXT"},
                "dataStatus": {"result": "ok"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = audit_historical_inputs.audit_historical_inputs(start_date="2026-04-04", end_date="2026-04-04", jcd="22")
    row = result["rows"][0]
    assert row["hasResultTxt"] is True
    assert row["hasParsedResultTxt"] is True
    assert row["resultSource"] == "official_txt_k"
    assert row["canSettleFromTxt"] is True
