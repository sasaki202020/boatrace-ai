from __future__ import annotations

import json

from src.pipeline import run_today


def _build_boats() -> list[dict]:
    return [
        {
            "boat_no": lane,
            "racer_name": None,
            "racer_id": None,
            "branch": None,
            "class": None,
            "age": None,
            "weight": None,
            "avg_st": None,
            "national_win_rate": None,
            "national_2rate": None,
            "national_3rate": None,
            "local_win_rate": None,
            "local_2rate": None,
            "local_3rate": None,
            "motor_no": None,
            "motor_2rate": None,
            "boat_no_equipment": lane,
            "boat_2rate": None,
            "f_count": None,
            "l_count": None,
            "data_status": "missing",
            "source": {"kind": "racelist_html"},
        }
        for lane in range(1, 7)
    ]


def test_stage_odds_pipeline_generates_buy_and_odds_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_today, "ROOT", tmp_path)
    monkeypatch.setattr(run_today, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(run_today, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(run_today, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(run_today, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(run_today, "discover_today", lambda **kwargs: {"venues": [{"jcd": "24", "venueName": "大村", "isOpen": True, "sourceUrl": "https://example.invalid", "fetchedAt": "2026-04-25T00:00:00"}]})

    def fake_fetch_day(**kwargs):
        stage = kwargs["stage"]
        boats = _build_boats()
        row = {
            "date": "20260425",
            "jcd": "24",
            "venue_name": "大村",
            "race_no": 1,
            "race_id": "20260425-24-01",
            "race_title": "1R",
            "deadline": "",
            "racelist": {
                "url": "https://example.invalid/racelist",
                "fetchedAt": "2026-04-25T00:00:00",
                "fetchStatus": "ok",
                "dataStatus": "ok",
                "missingReason": [],
                "html": "<html>racelist</html>",
                "parsed": {"dataStatus": "ok", "boats": boats, "raceTitle": "", "deadline": ""},
            },
            "beforeinfo": {"dataStatus": "pending", "parsed": {}},
            "odds3t": {
                "url": "https://example.invalid/odds3t",
                "fetchedAt": "2026-04-25T00:01:00",
                "fetchStatus": "ok",
                "dataStatus": "ok",
                "missingReason": [],
                "parseWarnings": [],
                "html": "<html>odds</html>",
                "parsed": {
                    "1-2-3": 40.0,
                    "1-2-4": 8.0,
                    "1-3-2": 7.5,
                }
                if stage == "odds"
                else {},
            },
            "result": {"dataStatus": "pending", "parsed": {}},
            "stage": stage,
            "source": {
                "racelistUrl": "https://example.invalid/racelist",
                "racelistFetchedAt": "2026-04-25T00:00:00",
                "odds3tUrl": "https://example.invalid/odds3t",
                "odds3tFetchedAt": "2026-04-25T00:01:00",
                "odds3tHttpStatus": "ok",
                "odds3tFallbackUsed": False,
                "stage": stage,
                "modelVersion": "baseline_rule_v1",
            },
        }
        return [row]

    monkeypatch.setattr(run_today, "fetch_day", fake_fetch_day)

    result = run_today.run_today(target_date="20260425", jcd="all", races=[1], stage="odds")

    ui_path = tmp_path / "data" / "ui" / "20260425" / "raceyosou_24.json"
    assert ui_path.exists()
    payload = json.loads(ui_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "odds"
    assert payload["races"][0]["dataStatus"]["odds3t"] == "ok"
    preds = payload["races"][0]["aiPredictions"]
    assert preds
    assert any(pred["decision"] == "BUY" for pred in preds)
    assert any(pred["expectedValue"] is not None for pred in preds)
    assert any(pred["edge"] is not None for pred in preds)
    assert result["odds3tOkCount"] >= 1
