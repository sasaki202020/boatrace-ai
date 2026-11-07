from __future__ import annotations

import json
from pathlib import Path

from src.pipeline import run_today as run_today_module


def _boat(no: int) -> dict[str, object]:
    return {
        "boat_no": no,
        "racer_name": f"選手{no}",
        "racer_id": f"r{no:04d}",
        "branch": "東京",
        "class": "B1",
        "age": 30 + no,
        "weight": 50.0 + no,
        "avg_st": 0.16 + no * 0.01,
        "national_win_rate": 18.0 + no,
        "national_2rate": 35.0 + no,
        "national_3rate": 50.0 + no,
        "local_win_rate": 17.0 + no,
        "local_2rate": 30.0 + no,
        "local_3rate": 45.0 + no,
        "motor_no": 10 + no,
        "motor_2rate": 25.0 + no,
        "boat_no_equipment": 20 + no,
        "boat_2rate": 22.0 + no,
        "f_count": 0,
        "l_count": 0,
        "data_status": "available",
    }


def test_run_today_beforeinfo_pipeline_writes_ui_and_marks_beforeinfo_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(run_today_module, "ROOT", tmp_path)
    monkeypatch.setattr(run_today_module, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(run_today_module, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(run_today_module, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(run_today_module, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(run_today_module, "_load_today_venues", lambda date8: {"date": date8, "venues": [{"jcd": "01", "venueName": "桐生", "isOpen": True}]})

    def fake_fetch_day(**kwargs):
        return [
            {
                "date": "20260425",
                "jcd": "01",
                "venue_name": "桐生",
                "race_no": 1,
                "race_id": "20260425-01-01",
                "race_title": "テスト",
                "deadline": "12:00",
                "racelist": {
                    "url": "https://example.invalid/racelist",
                    "fetchedAt": "2026-04-25T00:00:00",
                    "fetchStatus": "ok",
                    "dataStatus": "ok",
                    "missingReason": [],
                    "html": "<html></html>",
                    "parsed": {
                        "raceTitle": "テスト",
                        "deadline": "12:00",
                        "boats": [_boat(i) for i in range(1, 7)],
                    },
                },
                "beforeinfo": {
                    "url": "https://example.invalid/beforeinfo",
                    "fetchedAt": "2026-04-25T00:00:00",
                    "fetchStatus": "ok",
                    "dataStatus": "ok",
                    "dataStatusReason": [],
                    "missingReason": [],
                    "parseWarnings": [],
                    "html": "<html></html>",
                    "parsed": {
                        "beforeInfo": {
                            "weather": {
                                "sky": "晴れ",
                                "temperature": 24.5,
                                "windDirection": "北",
                                "windSpeed": 3.2,
                                "waveHeight": 1.0,
                                "water": {"temperature": 20.1, "condition": "良好"},
                                "beforeInfoUpdatedAt": "12:34",
                            },
                            "startExhibition": [
                                {"no": 1, "course": 1, "st": "0.12", "time": 6.78, "tilt": -0.5},
                            ],
                        },
                        "weather": {
                            "sky": "晴れ",
                            "temperature": 24.5,
                            "windDirection": "北",
                            "windSpeed": 3.2,
                            "waveHeight": 1.0,
                            "water": {"temperature": 20.1, "condition": "良好"},
                            "beforeInfoUpdatedAt": "12:34",
                        },
                        "startExhibition": [
                            {"no": 1, "course": 1, "st": "0.12", "time": 6.78, "tilt": -0.5},
                        ],
                        "boats": [
                            {
                                "boat_no": 1,
                                "racer_name": "選手1",
                                "exhibition_time": 6.78,
                                "start_exhibition_course": 1,
                                "start_exhibition_st": "0.12",
                                "tilt": -0.5,
                                "data_status": "available",
                            }
                        ],
                    },
                },
                "odds3t": {"dataStatus": "pending", "missingReason": ["odds3t_unavailable"], "parsed": {}, "html": ""},
                "result": {"dataStatus": "pending", "missingReason": ["result_unavailable"], "parsed": {}, "html": ""},
            }
        ]

    monkeypatch.setattr(run_today_module, "fetch_day", fake_fetch_day)

    result = run_today_module.run_today(target_date="20260425", jcd="01", races=[1], stage="beforeinfo")

    assert result["stage"] == "beforeinfo"
    assert result["race_count"] == 1
    assert result["buyCount"] == 0
    assert result["written"]["ui"]

    ui_path = Path(result["written"]["ui"][0])
    payload = json.loads(ui_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "beforeinfo"
    assert payload["races"][0]["dataStatus"]["beforeinfo"] == "ok"
    assert payload["races"][0]["weather"]["sky"] == "晴れ"
    assert payload["races"][0]["startExhibition"]
    assert payload["races"][0]["boats"][0]["boat_score"] is not None
    assert all(pred["decision"] in {"WATCH", "SKIP"} for pred in json.loads(Path(result["written"]["predictions"][0]).read_text(encoding="utf-8")))
