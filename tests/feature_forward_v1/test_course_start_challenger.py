from __future__ import annotations

import copy
import math
import sqlite3

import pandas as pd
import pytest

from src.feature_forward_v1.course_start_challenger import (
    CHAMPION_MODEL_SHA256,
    FEATURE_GROUP,
    build_course_start_race_rows,
    build_readiness_report,
    evaluate_course_start_challenger,
)
from scripts import run_course_start_challenger_v1 as cli


def _race(index: int, *, winner: int = 1) -> dict:
    date = f"2026-01-{index + 1:02d}"
    baseline = [0.45, 0.20, 0.12, 0.09, 0.08, 0.06]
    if winner != 1:
        baseline = [0.16, 0.22, 0.18, 0.17, 0.15, 0.12]
    features = [
        {
            "boatNo": boat,
            "courseEntry": boat,
            "startExhibition": round(0.02 * (boat - winner), 4),
            "tilt": 0.0,
            "bodyWeight": 50.0 + boat,
        }
        for boat in range(1, 7)
    ]
    return {
        "raceKey": f"{date}-01-{(index % 12) + 1:02d}",
        "raceDate": date,
        "venue": "01",
        "raceNo": (index % 12) + 1,
        "winnerBoat": winner,
        "baselineProbabilities": baseline,
        "features": features,
        "featureGroup": FEATURE_GROUP,
        "researchEligible": True,
        "captureTimestampVerified": True,
        "secondsBeforeDeadline": 420.0,
        "provenanceVerified": True,
        "schemaVerified": True,
    }


def test_readiness_is_blocked_without_schedule_denominator_or_thresholds():
    quality = {
        "capturedRaceCount": 98,
        "verifiedPreDeadlineCount": 98,
        "coverage": None,
        "consecutiveCollectionDays": 9,
        "captureTimestampVerifiedRate": 1.0,
        "provenanceVerifiedRate": 1.0,
        "schemaConsistencyRate": 1.0,
        "deterministicParsing": True,
        "postDeadlineCount": 0,
        "resultLeakageCount": 0,
        "duplicateCount": 0,
    }
    report = build_readiness_report(
        settled_races=1175,
        feature_races=98,
        quality=quality,
        model_sha256=CHAMPION_MODEL_SHA256,
    )

    assert report["status"] == "CHALLENGER_EVALUATION_BLOCKED"
    assert report["evaluationExecuted"] is False
    assert report["remainingSettledRaces"] == 325
    assert report["remainingForwardDays"] == 21
    assert "coverage_denominator_unavailable" in report["blockedReasons"]


def test_runner_readiness_uses_consecutive_verified_days() -> None:
    assert cli._readiness_forward_days({"consecutiveCollectionDays": 9}) == 9
    assert cli._readiness_forward_days({"consecutiveCollectionDays": 0}) == 0
    assert cli._readiness_forward_days({}) == 0


def test_race_rows_reject_target_like_feature_keys():
    race = _race(0)
    race["features"][0]["winner"] = 1
    with pytest.raises(ValueError, match="target_column_prohibited"):
        build_course_start_race_rows([race])


def test_oof_evaluation_has_five_chronological_folds_and_normalized_probabilities():
    races = [_race(index, winner=1 if index % 3 else 2) for index in range(36)]
    result = evaluate_course_start_challenger(races, bootstrap_repetitions=100)

    assert result["status"] in {"PERSONAL_OFFLINE_CHALLENGER", "NO_CHALLENGER_FOUND"}
    assert len(result["folds"]) == 5
    assert all(fold["trainRaceCount"] > 0 for fold in result["folds"])
    assert all(fold["validationRaceCount"] > 0 for fold in result["folds"])
    assert result["candidate"]["probabilityContractPassed"] is True
    assert result["candidate"]["predictionCount"] == 30
    for row in result["candidate"]["predictions"]:
        assert math.isclose(sum(row["probabilities"]), 1.0, abs_tol=1e-9)


def test_evaluation_is_deterministic_and_does_not_mutate_inputs():
    races = [_race(index, winner=1 if index % 2 else 3) for index in range(36)]
    original = copy.deepcopy(races)
    first = evaluate_course_start_challenger(races, bootstrap_repetitions=100)
    second = evaluate_course_start_challenger(races, bootstrap_repetitions=100)

    assert first == second
    assert races == original


def test_schedule_denominator_uses_selected_scope_and_source_hash(tmp_path, monkeypatch):
    b_root = tmp_path / "entries"
    b_root.mkdir()
    b_file = b_root / "B260731.TXT"
    b_file.write_bytes(b"local-b-source")
    ledger = tmp_path / "request_ledger.sqlite3"
    connection = sqlite3.connect(ledger)
    connection.execute("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    connection.execute("INSERT INTO state VALUES(?,?)", ("venue:2026-07-31", "10"))
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        cli,
        "validate_runtime_bfile",
        lambda path: pd.DataFrame([
            {"date": "2026-07-31", "jcd": "10", "race_no": 1, "deadline": "12:00"},
            {"date": "2026-07-31", "jcd": "11", "race_no": 1, "deadline": "12:00"},
        ]),
    )

    schedule, metadata = cli.load_selected_scope_schedule(
        b_root, ledger, {"2026-07-31"}
    )

    assert metadata["status"] == "VERIFIED_LOCAL_SELECTED_SCOPE"
    assert metadata["scope"] == "collector_selected_venues"
    assert metadata["scheduledRaceCount"] == 1
    assert schedule == [{"raceDate": "2026-07-31", "jcd": "10", "raceNo": 1, "timeBand": "unknown"}]
    assert len(metadata["sourceFiles"][0]["sha256"]) == 64
