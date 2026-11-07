from __future__ import annotations

import json

from src.pipeline import run_today


def test_run_today_smoke_creates_ui_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_today, "ROOT", tmp_path)
    monkeypatch.setattr(run_today, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(run_today, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(run_today, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(run_today, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(run_today, "discover_today", lambda **kwargs: {"venues": [{"jcd": "24"}], "dataStatus": "available"})

    def fake_fetch_day(**kwargs):
        stage = kwargs["stage"]
        boats = [
            {
                "boat_no": i,
                "racer_name": f"{i}号艇",
                "racer_class": "B1",
                "national_win_rate": 30 + i,
                "local_win_rate": 25 + i,
                "motor_no": 10 + i,
                "motor_2ren_rate": 40 + i,
                "boat_no_equipment": 20 + i,
                "boat_2ren_rate": 35 + i,
                "avg_st": 0.15 + i * 0.01,
                "data_status": "available",
            }
            for i in range(1, 7)
        ]
        beforeinfo = {
            "dataStatus": "available" if stage == "beforeinfo" else "pending",
            "missingReason": [] if stage == "beforeinfo" else ["beforeinfo_unavailable"],
            "parsed": {
                "dataStatus": "available" if stage == "beforeinfo" else "missing",
                "missingReason": [] if stage == "beforeinfo" else ["beforeinfo_unavailable"],
                "start_exhibition": [{"no": 1, "type": "S", "time": 6.62}],
                "weather": {"windSpeed": 3, "sky": "晴"},
                "boats": [{"boat_no": 1, "exhibition_time": 6.62, "exhibition_st": 0.12, "tilt": -0.5, "data_status": "available"}],
            },
        }
        odds3t = {
            "dataStatus": "available" if stage == "odds" else "pending",
            "missingReason": [] if stage == "odds" else ["odds3t_unavailable"],
            "parsed": [{"combo": "1-2-3", "odds": 12.0}],
        }
        return [
            {
                "date": "2026-04-24",
                "jcd": "24",
                "venue_name": "大村",
                "race_no": 1,
                "race_id": "20260424-24-01",
                "race_title": "1R",
                "deadline": "12:00",
                "racelist": {
                    "dataStatus": "available",
                    "missingReason": [],
                    "parsed": {"boats": boats, "dataStatus": "available", "raceTitle": "1R", "deadline": "12:00"},
                    "url": "https://example.invalid/racelist",
                    "fetchedAt": "2026-04-24T00:00:00",
                },
                "beforeinfo": beforeinfo,
                "odds3t": odds3t,
                "result": {"dataStatus": "pending", "parsed": {}},
                "stage": stage,
            }
        ]

    monkeypatch.setattr(run_today, "fetch_day", fake_fetch_day)
    result = run_today.run_today(target_date="2026-04-24", jcd="all", races=[1], stage="beforeinfo")
    out_path = tmp_path / "data" / "ui" / "20260424" / "raceyosou_24.json"
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["races"][0]["beforeInfo"]
    assert isinstance(payload["races"][0]["dataStatusReason"], dict)
    assert payload["races"][0]["boats"][0]["exhibition_time"] == 6.62
    assert result["venues"] == ["24"]


def test_run_today_odds_stage_with_missing_odds_never_buys(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_today, "ROOT", tmp_path)
    monkeypatch.setattr(run_today, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(run_today, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(run_today, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(run_today, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(run_today, "discover_today", lambda **kwargs: {"venues": [{"jcd": "24"}], "dataStatus": "available"})

    def fake_fetch_day(**kwargs):
        return [
            {
                "date": "2026-04-24",
                "jcd": "24",
                "venue_name": "大村",
                "race_no": 1,
                "race_id": "20260424-24-01",
                "race_title": "1R",
                "deadline": "12:00",
                "racelist": {
                    "dataStatus": "available",
                    "missingReason": [],
                    "parsed": {
                        "boats": [
                            {
                                "boat_no": i,
                                "racer_name": f"{i}号艇",
                                "racer_class": "B1",
                                "national_win_rate": 30 + i,
                                "local_win_rate": 25 + i,
                                "motor_no": 10 + i,
                                "motor_2ren_rate": 40 + i,
                                "boat_no_equipment": 20 + i,
                                "boat_2ren_rate": 35 + i,
                                "avg_st": 0.15 + i * 0.01,
                                "data_status": "available",
                            }
                            for i in range(1, 7)
                        ],
                        "dataStatus": "available",
                        "raceTitle": "1R",
                        "deadline": "12:00",
                    },
                    "url": "https://example.invalid/racelist",
                    "fetchedAt": "2026-04-24T00:00:00",
                },
                "beforeinfo": {"dataStatus": "pending", "missingReason": ["beforeinfo_unavailable"], "parsed": {}, "html": ""},
                "odds3t": {
                    "dataStatus": "unavailable",
                    "missingReason": ["odds_fetch_http_error"],
                    "errorType": "odds_fetch_http_error",
                    "errorMessage": "timeout",
                    "parsed": {},
                    "html": "",
                    "url": "https://example.invalid/odds3t",
                    "fetchedAt": "2026-04-24T00:00:00",
                },
                "result": {"dataStatus": "pending", "parsed": {}},
                "stage": "odds",
            }
        ]

    monkeypatch.setattr(run_today, "fetch_day", fake_fetch_day)
    result = run_today.run_today(target_date="2026-04-24", jcd="all", races=[1], stage="odds")
    out_path = tmp_path / "data" / "ui" / "20260424" / "raceyosou_24.json"
    frozen_path = tmp_path / "data" / "predictions" / "20260424" / "frozen_bets_24.json"
    frozen_all_path = tmp_path / "data" / "predictions" / "20260424" / "frozen_bets_all.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert result["buyCount"] == 0
    assert all(pred["decision"] != "BUY" for pred in payload["races"][0]["aiPredictions"])
    assert frozen_path.exists()
    assert frozen_all_path.exists()
    frozen_payload = json.loads(frozen_path.read_text(encoding="utf-8"))
    assert frozen_payload["races"][0]["bets"]
    first_bet = frozen_payload["races"][0]["bets"][0]
    assert first_bet["candidateId"]
    assert first_bet["modelVersion"] == "baseline_rule_v1"
    assert first_bet["policyVersion"] == "paper_shadow_policy_v1"
    assert first_bet["oddsCapturedAt"] == "2026-04-24T00:00:00"
    assert first_bet["deadlineAt"] == "2026-04-24T12:00"
    assert first_bet["guardDecision"] in {"PASS", "REJECT"}


def test_run_today_result_stage_keeps_frozen_predictions_unchanged(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_today, "ROOT", tmp_path)
    monkeypatch.setattr(run_today, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(run_today, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(run_today, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(run_today, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(run_today, "discover_today", lambda **kwargs: {"venues": [{"jcd": "24"}], "dataStatus": "available"})

    frozen_payload = {
        "date": "20260424",
        "generatedAt": "2026-04-24T10:00:00",
        "venues": [
            {
                "jcd": "24",
                "venue": "大村",
                "modelVersion": "baseline_rule_v1",
                "predictionHash": "hash-before",
                "races": [
                    {"rno": 1, "bets": [{"combo": "1-2-3", "decision": "BUY", "predictionHash": "hash-before"}]}
                ],
            }
        ],
        "predictionHash": "hash-before",
    }
    frozen_path = tmp_path / "data" / "predictions" / "20260424" / "frozen_bets_all.json"
    frozen_path.parent.mkdir(parents=True, exist_ok=True)
    frozen_path.write_text(json.dumps(frozen_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    before_text = frozen_path.read_text(encoding="utf-8")

    def fake_fetch_day(**kwargs):
        return [
            {
                "date": "2026-04-24",
                "jcd": "24",
                "venue_name": "大村",
                "race_no": 1,
                "race_id": "20260424-24-01",
                "race_title": "1R",
                "deadline": "12:00",
                "racelist": {"dataStatus": "available", "missingReason": [], "parsed": {"boats": []}, "url": "", "fetchedAt": ""},
                "beforeinfo": {"dataStatus": "pending", "missingReason": []},
                "odds3t": {"dataStatus": "pending", "missingReason": []},
                "result": {"dataStatus": "ok", "parsed": {"raceStatus": "ok", "trifectaCombo": "1-2-3", "trifectaPayout": 590}},
                "stage": "result",
            }
        ]

    monkeypatch.setattr(run_today, "fetch_day", fake_fetch_day)
    monkeypatch.setattr(run_today, "_score_and_predict", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stage=result must not regenerate predictions")))

    result = run_today.run_today(target_date="2026-04-24", jcd="all", races=[1], stage="result")
    after_text = frozen_path.read_text(encoding="utf-8")
    assert before_text == after_text
    assert result["stage"] == "result"
    out_path = tmp_path / "data" / "ui" / "20260424" / "raceyosou_24.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["races"][0]["result"]["raceStatus"] == "ok"
