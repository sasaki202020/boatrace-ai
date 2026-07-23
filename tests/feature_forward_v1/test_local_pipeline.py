import json

import pytest

from src.feature_forward_v1.local_pipeline import (
    _existing_settlement_matches, stable_hash, write_new_json,
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
