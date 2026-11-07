from __future__ import annotations

import json

from src.pipeline import run_today


def test_run_today_writes_frozen_bets_for_odds_stage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_today, "ROOT", tmp_path)
    monkeypatch.setattr(run_today, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(run_today, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(run_today, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(run_today, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(run_today, "discover_today", lambda **kwargs: {"venues": [{"jcd": "24"}], "dataStatus": "available"})

    def fake_fetch_day(**kwargs):
        stage = kwargs["stage"]
        boats = [
            {"boat_no": i, "racer_name": f"{i}号艇", "national_win_rate": 30 + i, "local_win_rate": 20 + i, "motor_no": 10 + i, "motor_2ren_rate": 35 + i, "boat_no_equipment": 20 + i, "boat_2ren_rate": 25 + i, "avg_st": 0.15 + i * 0.01, "data_status": "available"}
            for i in range(1, 7)
        ]
        return [
            {
                "date": "2026-04-24",
                "jcd": "24",
                "venue_name": "大村",
                "race_no": 1,
                "race_id": "20260424-24-01",
                "race_title": "1R",
                "deadline": "12:00",
                "racelist": {"dataStatus": "available", "missingReason": [], "parsed": {"boats": boats, "dataStatus": "available"}, "url": "https://example.invalid/racelist", "fetchedAt": "2026-04-24T00:00:00"},
                "beforeinfo": {"dataStatus": "pending", "missingReason": ["beforeinfo_unavailable"], "parsed": {"boats": [], "start_exhibition": [], "weather": None}},
                "odds3t": {"dataStatus": "unavailable" if stage == "pre_race" else "available", "missingReason": [], "parsed": [{"combo": "1-2-3", "odds": 12.0}]},
                "result": {"dataStatus": "pending", "parsed": {}},
                "stage": stage,
            }
        ]

    monkeypatch.setattr(run_today, "fetch_day", fake_fetch_day)
    result = run_today.run_today(target_date="2026-04-24", jcd="all", races=[1], stage="odds")
    frozen_path = tmp_path / "data" / "predictions" / "20260424" / "frozen_bets_24.json"
    frozen_all_path = tmp_path / "data" / "predictions" / "20260424" / "frozen_bets_all.json"
    payload = json.loads(frozen_path.read_text(encoding="utf-8"))
    all_payload = json.loads(frozen_all_path.read_text(encoding="utf-8"))
    assert result["stage"] == "odds"
    assert frozen_path.exists()
    assert frozen_all_path.exists()
    assert payload["freezeType"] == "live"
    assert payload["predictionHash"]
    assert payload["races"][0]["bets"] == [] or isinstance(payload["races"][0]["bets"], list)
    for bet in payload["races"][0]["bets"]:
        assert bet["candidateId"]
        assert bet["modelVersion"] == "baseline_rule_v1"
        assert bet["policyVersion"] == "paper_shadow_policy_v1"
        assert bet["predictionHash"]
        assert bet["snapshotHash"]
        assert bet["featureVersion"] == "baseline_score_features_v1"
        assert bet["rawProbability"] != "legacy_unknown"
        assert bet["calibratedProbability"] != "legacy_unknown"
        assert bet["deadlineAt"] == "2026-04-24T12:00"
        assert bet["frozenAt"] != "legacy_unknown"
    assert all_payload["freezeType"] == "live"
    assert all_payload["totalBetCount"] >= 0
