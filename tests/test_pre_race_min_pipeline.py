from __future__ import annotations

from src.ingest.parsers.racelist_parser import parse_racelist_html
from src.normalize.race_snapshot import build_race_snapshot
from src.normalize.schema import Boat
from src.predict.baseline_score_model import score_boats
from src.predict.make_ui_json import build_ui_payload
from src.predict.trifecta_builder import build_trifecta_candidates


def test_racelist_parser_returns_six_boats_for_missing_html() -> None:
    parsed = parse_racelist_html("", "20260424", "24", 1)
    assert parsed["dataStatus"] == "missing"
    assert len(parsed["boats"]) == 6
    assert [boat["boat_no"] for boat in parsed["boats"]] == [1, 2, 3, 4, 5, 6]


def test_trifecta_builder_generates_120_combos() -> None:
    boats = [
        Boat(
            boat_no=i,
            racer_name=None,
            national_win_rate=None,
            local_win_rate=None,
            motor_2rate=None,
            boat_2rate=None,
            avg_st=None,
            data_status="missing",
        )
        for i in range(1, 7)
    ]
    scored = score_boats([boat.to_dict() for boat in boats])
    preds = build_trifecta_candidates(scored)
    assert len(preds) == 120
    assert len({pred.combo for pred in preds}) == 120


def test_make_ui_json_generates_react_payload() -> None:
    boats = [
        Boat(boat_no=i, racer_name=None, data_status="missing", boat_score=1.0 / i, score_rank=i)
        for i in range(1, 7)
    ]
    snapshot = build_race_snapshot(
        date="20260424",
        jcd="24",
        venue_name="大村",
        rno=1,
        stage="pre_race",
        boats=boats,
        weather=None,
        start_exhibition=[],
        odds3t={},
        result={},
        source={"racelistUrl": "https://example.invalid", "racelistFetchedAt": "2026-04-24T00:00:00"},
        data_status={"racelist": "missing", "odds3t": "pending", "beforeinfo": "pending", "result": "pending"},
        predictions=[],
    )
    payload = build_ui_payload([snapshot])
    assert payload["date"] == "20260424"
    assert payload["venue"] == "大村"
    assert payload["races"][0]["dataStatus"]["racelist"] == "missing"
    assert payload["races"][0]["status"] == "pre_race"
