from __future__ import annotations

import json
from pathlib import Path

from src.pipeline import backfill_predictions


def test_backfill_predictions_writes_shadow_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")
    monkeypatch.setattr(backfill_predictions, "ARCHIVE_ROOT", tmp_path / "_archive")

    normalized_dir = tmp_path / "data" / "normalized" / "20260420" / "24"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_payload = {
        "date": "20260420",
        "jcd": "24",
        "venueName": "大村",
        "rno": 1,
        "deadline": "12:00",
        "raceTitle": "1R",
        "stage": "odds",
        "boats": [
            {
                "boat_no": i,
                "racer_name": f"{i}号艇",
                "racer_id": f"R{i}",
                "branch": "長崎",
                "class": "B1",
                "age": 28 + i,
                "weight": 52,
                "avg_st": 0.15 + i * 0.01,
                "national_win_rate": 30 + i,
                "national_2rate": 40 + i,
                "national_3rate": 50 + i,
                "local_win_rate": 25 + i,
                "local_2rate": 35 + i,
                "local_3rate": 45 + i,
                "motor_no": 10 + i,
                "motor_2rate": 20 + i,
                "boat_no_equipment": 30 + i,
                "boat_2rate": 40 + i,
                "f_count": 0,
                "l_count": 0,
                "data_status": "available",
            }
            for i in range(1, 7)
        ],
        "weather": None,
        "startExhibition": [],
        "odds3t": {"1-2-3": 12.0},
        "result": {},
        "source": {},
        "dataStatus": {"racelist": "ok", "odds3t": "ok", "beforeinfo": "pending", "result": "pending"},
    }
    (normalized_dir / "race_1.json").write_text(json.dumps(normalized_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = backfill_predictions.backfill_predictions(start_date="2026-04-20", end_date="2026-04-20", jcd="24", stage="odds")
    all_path = tmp_path / "data" / "predictions_backfill" / "20260420" / "backfilled_bets_all.json"
    venue_path = tmp_path / "data" / "predictions_backfill" / "20260420" / "backfilled_bets_24.json"
    assert all_path.exists()
    assert venue_path.exists()
    payload = json.loads(venue_path.read_text(encoding="utf-8"))
    assert payload["freezeType"] == "backfill"
    assert payload["warning"] == "backfilled_predictions_not_live"
    assert payload["leakageGuardStatus"] == "ok"
    assert payload["inputAvailability"]
    assert result["summaryRows"]
    assert result["written"]
    assert all_path.as_posix() in result["written"][0] or any(Path(p).name == "backfilled_bets_all.json" for p in result["written"])


def test_backfill_predictions_dry_run_and_empty_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backfill_predictions, "ROOT", tmp_path)
    monkeypatch.setattr(backfill_predictions, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(backfill_predictions, "BACKFILL_ROOT", tmp_path / "data" / "predictions_backfill")
    monkeypatch.setattr(backfill_predictions, "ARCHIVE_ROOT", tmp_path / "_archive")

    result = backfill_predictions.backfill_predictions(start_date="2026-04-20", end_date="2026-04-20", jcd="all", stage="odds", dry_run=True)
    assert result["dryRun"] is True
    assert result["dryRunSummary"]["targetDays"] == 1
    assert result["dryRunSummary"]["backfillPossibleDays"] == 0
    assert result["written"] == []
    assert not (tmp_path / "data" / "predictions_backfill").exists()

    # create a normalized day with no races so the normal path still emits empty all.json
    normalized_dir = tmp_path / "data" / "normalized" / "20260420" / "24"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "race_1.json").write_text(json.dumps({"date": "20260420", "jcd": "24", "rno": 1, "boats": [], "dataStatus": {"odds3t": "pending"}}), encoding="utf-8")
    result2 = backfill_predictions.backfill_predictions(start_date="2026-04-20", end_date="2026-04-20", jcd="24", stage="odds", dry_run=False)
    all_path = tmp_path / "data" / "predictions_backfill" / "20260420" / "backfilled_bets_all.json"
    assert all_path.exists()
    assert result2["written"]
