from __future__ import annotations

import json

from src.normalize.race_snapshot import build_race_snapshot
from src.normalize.schema import Boat, Prediction
from src.predict.make_ui_json import build_ui_payload, write_ui_payload
from src.web import app as web_app


def test_make_ui_json_writes_raceyosou(tmp_path) -> None:
    boats = [Boat(boat_no=i, racer_name=f"{i}号艇", data_status="available") for i in range(1, 7)]
    snapshot = build_race_snapshot(
        date="2026-04-24",
        jcd="24",
        venue_name="大村",
        rno=1,
        deadline="12:00",
        race_title="1R",
        boats=boats,
        weather={"wind": 3},
        start_exhibition=[{"no": 1, "time": 6.62}],
        odds3t=[],
        result={},
        source={"data_status_reason": []},
        data_status="available",
        stage="beforeinfo",
        model_version="baseline_rule_v1",
    )
    payload = build_ui_payload([(snapshot, [Prediction(combo="1-2-3", prob=0.2, odds=12.0, expected_value=2.4, edge=1.4, rank=1, grade="A", decision="BUY", reason="ok")])])
    path = write_ui_payload(payload, output_dir=tmp_path)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["races"][0]["aiPredictions"][0]["expectedValue"] == 2.4
    assert data["races"][0]["startExhibition"][0]["time"] == 6.62


def test_build_raceyosou_viewmodel_missing_official_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "_load_official_raceyosou_payload", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(web_app, "_is_official_raceyosou_payload_valid", lambda payload: False)

    view = web_app.buildRaceYosouViewModel("2026-04-24", "24")

    assert view["races"] == []
    assert view["source"] == "missing"
    assert view["venue"] == "大村"


def test_build_raceyosou_viewmodel_prefers_official_ui_payload(monkeypatch, tmp_path) -> None:
    payload = {
        "date": "2026-04-24",
        "venue": "大村",
        "races": [{"raceNo": 1, "boats": [], "aiPredictions": []}],
    }
    source_path = tmp_path / "data" / "ui" / "20260424" / "raceyosou_24.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def fail_legacy(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("legacy fallback should not be used when official UI payload is valid")

    monkeypatch.setattr(web_app, "_load_official_raceyosou_payload", lambda *args, **kwargs: (payload, source_path))
    monkeypatch.setattr(web_app, "_is_official_raceyosou_payload_valid", lambda value: True)
    monkeypatch.setattr(web_app, "_build_raceyosou_view_from_official", lambda *args, **kwargs: {"source": "official", "races": [{"raceNo": 1}]})
    monkeypatch.setattr(web_app, "buildLegacyRaceYosouViewModel", fail_legacy)

    view = web_app.buildRaceYosouViewModel("2026-04-24", "24")

    assert view["source"] == "official"
    assert view["races"][0]["raceNo"] == 1
