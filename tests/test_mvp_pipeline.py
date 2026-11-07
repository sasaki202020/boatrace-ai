from __future__ import annotations

from src.normalize.schema import Boat, RaceSnapshot
from src.predict.baseline_score_model import score_boat
from src.predict.make_ui_json import build_ui_payload
from src.predict.trifecta_builder import build_trifecta_candidates


def test_trifecta_builder_generates_120_combos() -> None:
    boats = [
        Boat(
            boat_no=i,
            racer_name=f"R{i}",
            national_win_rate=12 + i,
            local_win_rate=11 + i,
            motor_2rate=20 + i,
            boat_2rate=18 + i,
            avg_st=0.15 + i * 0.01,
            data_status="available",
            source={"url": "u", "fetchedAt": "t"},
        )
        for i in range(1, 7)
    ]
    scores = [score_boat(boat.__dict__, lane=boat.boat_no) for boat in boats]
    preds = build_trifecta_candidates(scores, odds3t=[{"combo": "1-2-3", "odds": 12.3}])
    assert len(preds) == 120
    assert preds[0].combo


def test_build_ui_payload_contains_expected_fields() -> None:
    boats = [Boat(boat_no=i, racer_name=f"R{i}", data_status="available") for i in range(1, 7)]
    snapshot = RaceSnapshot(
        date="2026-04-24",
        jcd="01",
        venue_name="桐生",
        rno=1,
        deadline="15:18",
        race_title="テスト",
        boats=boats,
        weather={"sky": "晴れ"},
        start_exhibition=[],
        odds3t={},
        result={},
        source={"racelistUrl": "https://example.com", "racelistFetchedAt": "2026-04-24T00:00:00", "stage": "pre_race", "modelVersion": "baseline_rule_v1"},
        data_status={"racelist": "pending", "odds3t": "pending", "beforeinfo": "pending", "result": "pending"},
        stage="pre_race",
        model_version="baseline_rule_v1",
    )
    payload = build_ui_payload([(snapshot, [])])
    assert payload["date"] == "2026-04-24"
    assert payload["stage"] == "pre_race"
    assert payload["modelVersion"] == "baseline_rule_v1"
    assert payload["races"][0]["dataStatus"]["racelist"] == "pending"
    assert payload["races"][0]["dataStatusText"] == "pending"
