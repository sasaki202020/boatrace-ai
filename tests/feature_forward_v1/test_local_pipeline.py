import json

import pytest

from src.feature_forward_v1.local_pipeline import (
    _existing_settlement_matches,
    settle_available_predictions,
    stable_hash,
    write_new_json,
)


def test_prediction_and_settlement_outputs_are_append_only(tmp_path):
    path = tmp_path / "record.json"
    value = {"raceId": "20260723-01-01", "value": 1}
    assert write_new_json(path, value) is True
    assert write_new_json(path, value) is False
    with pytest.raises(ValueError, match="append_only_conflict"):
        write_new_json(path, {**value, "value": 2})


def test_stable_hash_is_deterministic():
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})


def test_existing_settlement_detects_metric_tamper():
    value = {
        "raceId": "20260723-01-01", "raceDate": "2026-07-23",
        "predictionSha256": "a" * 64, "resultSourceSha256": "b" * 64,
        "winnerBoat": 1, "winnerRank": 1, "top1Correct": True,
        "winnerInTop2": True, "winnerInTop3": True,
        "settledAtJst": "2026-07-24T06:00:00+09:00",
        "resultSource": "official_txt_k",
    }
    value["settlementSha256"] = stable_hash(value)
    assert _existing_settlement_matches(value, value)
    tampered = {**value, "top1Correct": False}
    assert not _existing_settlement_matches(tampered, value)


def test_not_held_race_is_terminal_void_settlement(tmp_path):
    prediction_root = tmp_path / "predictions"
    settlement_root = tmp_path / "settlements"
    k_root = tmp_path / "results"
    prediction = {
        "raceId": "20260101-09-01",
        "raceDate": "2026-01-01",
        "venue": "09",
        "raceNo": 1,
        "probabilities": [
            {"boatNo": boat_no, "probability": 1 / 6, "rank": boat_no}
            for boat_no in range(1, 7)
        ],
    }
    prediction["predictionSha256"] = stable_hash(prediction)
    prediction_path = prediction_root / "20260101" / "20260101-09-01.json"
    write_new_json(prediction_path, prediction)
    k_root.mkdir()
    (k_root / "K260101.TXT").write_bytes(b"09KBGN\r\n09KEND")

    result = settle_available_predictions(
        prediction_root=prediction_root,
        settlement_root=settlement_root,
        k_root=k_root,
    )

    assert result["created"] == 1
    assert result["pending"] == 0
    saved = json.loads(
        (settlement_root / "20260101" / "20260101-09-01.json").read_text()
    )
    assert saved["settlementStatus"] == "void"
    assert saved["resultStatus"] == "not_held"
    assert saved["winnerBoat"] is None


def test_result_without_winner_remains_pending(tmp_path, monkeypatch):
    prediction_root = tmp_path / "predictions"
    settlement_root = tmp_path / "settlements"
    k_root = tmp_path / "results"
    prediction = {
        "raceId": "20260101-09-01",
        "raceDate": "2026-01-01",
        "venue": "09",
        "raceNo": 1,
        "probabilities": [
            {"boatNo": boat_no, "probability": 1 / 6, "rank": boat_no}
            for boat_no in range(1, 7)
        ],
    }
    prediction["predictionSha256"] = stable_hash(prediction)
    write_new_json(
        prediction_root / "20260101" / "20260101-09-01.json", prediction
    )
    k_root.mkdir()
    (k_root / "K260101.TXT").write_bytes(b"09KBGN\r\n09KEND")
    monkeypatch.setattr(
        "src.feature_forward_v1.local_pipeline.parse_official_k_result_file",
        lambda _path, **_kwargs: {
            "races": [{"jcd": "09", "raceNo": 1, "raceStatus": "ok"}]
        },
    )

    result = settle_available_predictions(
        prediction_root=prediction_root,
        settlement_root=settlement_root,
        k_root=k_root,
    )

    assert result["created"] == 0
    assert result["pending"] == 1
