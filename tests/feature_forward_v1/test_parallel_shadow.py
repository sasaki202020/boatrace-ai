from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.feature_forward_v1.parallel_shadow import (
    CHAMPION_MODEL_SHA256,
    FEATURE_GROUP,
    FEATURE_SCHEMA_SHA256,
    ParallelShadowError,
    ShadowLedger,
    build_shadow_record,
    challenger_probabilities,
    load_shadow_candidates,
    load_fixed_config,
    sha256_json,
)

JST = ZoneInfo("Asia/Tokyo")


def _config() -> dict:
    return json.loads(
        (Path(__file__).parents[2] / "config" / "feature_forward_v1" / "parallel_shadow_config.json").read_text(
            encoding="utf-8"
        )
    )


def _prediction() -> dict:
    return {
        "raceId": "20260805-01-01",
        "raceDate": "2026-08-05",
        "venue": "01",
        "raceNo": 1,
        "predictedAt": "2026-08-05T08:00:00+09:00",
        "deadlineJst": "2026-08-05T08:10:00+09:00",
        "baselineProbabilities": [0.45, 0.2, 0.12, 0.09, 0.08, 0.06],
        "predictionSha256": "p" * 64,
        "inputSha256": "i" * 64,
    }


def _prediction_payload() -> dict:
    payload = {
        "raceDate": "2026-08-05",
        "raceId": "20260805-01-01",
        "venue": "01",
        "raceNo": 1,
        "generatedAtJst": "2026-08-05T08:00:00+09:00",
        "deadlineJst": "2026-08-05T08:10:00+09:00",
        "modelVersion": "tree_15",
        "modelSha256": CHAMPION_MODEL_SHA256,
        "featureSchemaVersion": FEATURE_SCHEMA_SHA256,
        "probabilities": [
            {"boatNo": boat, "probability": probability}
            for boat, probability in enumerate([0.45, 0.2, 0.12, 0.09, 0.08, 0.06], 1)
        ],
        "inputSha256": "i" * 64,
    }
    payload["predictionSha256"] = sha256_json(payload)
    return payload


def test_shadow_waits_until_capture_window_has_closed(tmp_path: Path) -> None:
    (tmp_path / "prediction.json").write_text(
        json.dumps(_prediction_payload()), encoding="utf-8"
    )
    early, skipped = load_shadow_candidates(
        tmp_path, datetime.fromisoformat("2026-08-05T07:55:00+09:00")
    )
    assert early == []
    assert skipped == 1
    ready, skipped = load_shadow_candidates(
        tmp_path, datetime.fromisoformat("2026-08-05T08:05:00+09:00")
    )
    assert len(ready) == 1
    assert skipped == 0


def _snapshot() -> object:
    from src.feature_forward_v1.parallel_shadow import FeatureSnapshot

    return FeatureSnapshot(
        snapshot_id="snapshot-1",
        captured_at_jst="2026-08-05T08:02:00+09:00",
        deadline_jst="2026-08-05T08:10:00+09:00",
        raw_sha256="r" * 64,
        schema_sha256="s" * 64,
        provenance_sha256="v" * 64,
        values={
            boat: {
                "courseEntry": boat,
                "startExhibition": 0.10 + boat / 1000,
                "tilt": 0.0,
                "bodyWeight": 52.0,
            }
            for boat in range(1, 7)
        },
    )


def test_fixed_config_is_valid_and_hashable() -> None:
    config, digest = load_fixed_config(
        Path(__file__).parents[2] / "config" / "feature_forward_v1" / "parallel_shadow_config.json"
    )
    assert config["challengerVersion"] == "course_start_residual_shadow_v1"
    assert len(digest) == 64
    assert config["productionAdoptionAllowed"] is False
    assert config["prospectiveConnectionAllowed"] is False


def test_config_change_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "parallel_shadow_config.json"
    path.write_text(json.dumps({**_config(), "seed": 1}), encoding="utf-8")
    with pytest.raises(ParallelShadowError, match="config_hash_mismatch"):
        load_fixed_config(path)


def test_fixed_challenger_is_deterministic_and_normalized() -> None:
    config = _config()
    baseline = _prediction()["baselineProbabilities"]
    features = _snapshot().values
    first = challenger_probabilities(baseline, features, config)
    second = challenger_probabilities(baseline, features, config)
    assert first == second
    assert abs(sum(first) - 1.0) < 1e-9
    assert first != baseline


def test_shadow_record_contains_paired_predictions_and_no_result() -> None:
    config = _config()
    record = build_shadow_record(
        _prediction(), _snapshot(), config=config, config_sha256="c" * 64, code_commit="d" * 40
    )
    assert record["shadowMode"] == "PARALLEL_SHADOW_ONLY"
    assert record["baselineProbabilities"]
    assert record["challengerProbabilities"]
    assert record["fallbackReason"] == ""
    assert record["leakageCheck"] == {"status": "PASS", "fields": 0}
    assert "winner" not in record
    unsigned = dict(record)
    saved = unsigned.pop("recordHash")
    assert sha256_json(unsigned) == saved


def test_missing_feature_falls_back_without_changing_baseline() -> None:
    record = build_shadow_record(
        _prediction(), None, config=_config(), config_sha256="c" * 64, code_commit="d" * 40
    )
    assert record["fallbackReason"] == "FEATURE_SNAPSHOT_UNAVAILABLE"
    assert record["challengerProbabilities"] == record["baselineProbabilities"]
    assert record["challengerTop1"] == record["baselineTop1"]


def test_result_like_feature_field_is_rejected() -> None:
    snapshot = _snapshot()
    snapshot.values[1]["winner"] = 1
    with pytest.raises(ParallelShadowError):
        challenger_probabilities(_prediction()["baselineProbabilities"], snapshot.values, _config())


def test_append_only_ledger_is_idempotent_and_detects_conflict(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "parallel_shadow.sqlite3")
    try:
        record = build_shadow_record(
            _prediction(), _snapshot(), config=_config(), config_sha256="c" * 64, code_commit="d" * 40
        )
        assert ledger.append_many([record]) == {"created": 1, "idempotent": 0}
        assert ledger.append_many([record]) == {"created": 0, "idempotent": 1}
        changed = copy.deepcopy(record)
        changed["challengerTop1"] = 6
        changed["recordHash"] = sha256_json({key: value for key, value in changed.items() if key != "recordHash"})
        with pytest.raises(ParallelShadowError, match="shadow_record_conflict"):
            ledger.append_many([changed])
        assert ledger.verify_integrity()["valid"] is True
    finally:
        ledger.close()


def test_legacy_record_without_source_hash_is_not_rewritten(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "parallel_shadow.sqlite3")
    try:
        record = build_shadow_record(
            _prediction(), None, config=_config(), config_sha256="c" * 64, code_commit="d" * 40
        )
        legacy = copy.deepcopy(record)
        legacy.pop("codeSourceSha256")
        legacy.pop("recordHash")
        legacy["recordHash"] = sha256_json(legacy)
        assert ledger.append_many([legacy]) == {"created": 1, "idempotent": 0}
        assert ledger.append_many([record]) == {"created": 0, "idempotent": 1}
        saved = ledger.connection.execute(
            "SELECT payload_json FROM shadow_predictions WHERE race_id=?", (record["raceId"],)
        ).fetchone()[0]
        assert "codeSourceSha256" not in json.loads(saved)
    finally:
        ledger.close()


def test_ledger_update_and_delete_are_rejected(tmp_path: Path) -> None:
    ledger = ShadowLedger(tmp_path / "parallel_shadow.sqlite3")
    try:
        record = build_shadow_record(
            _prediction(), None, config=_config(), config_sha256="c" * 64, code_commit="d" * 40
        )
        ledger.append_many([record])
        with pytest.raises(sqlite3.DatabaseError):
            ledger.connection.execute(
                "UPDATE shadow_predictions SET payload_json=? WHERE race_id=?",
                ("{}", record["raceId"]),
            )
        with pytest.raises(sqlite3.DatabaseError):
            ledger.connection.execute("DELETE FROM shadow_predictions WHERE race_id=?", (record["raceId"],))
    finally:
        ledger.close()
