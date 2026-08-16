from __future__ import annotations

import copy
import json
import math
import sqlite3

import pandas as pd
import pytest

from src.feature_forward_v1.course_start_challenger import (
    CHAMPION_MODEL_SHA256,
    FEATURE_GROUP,
    brier_adoption_failures,
    build_course_start_race_rows,
    build_readiness_report,
    candidate_prediction_digest,
    evaluate_course_start_challenger,
    segment_stability_failures,
)
from src.feature_forward_v1.store import stable_hash
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
    assert report["cohortStart"] is None
    assert report["cohortEnd"] is None
    assert report["cohortDigest"] is None
    assert report["evaluationLocked"] is False
    assert report["oofValidationRaceCount"] == 0
    assert report["oofValidationDateCount"] == 0


def test_runner_readiness_uses_consecutive_verified_days() -> None:
    assert cli._readiness_forward_days({"consecutiveCollectionDays": 9}) == 9
    assert cli._readiness_forward_days({"consecutiveCollectionDays": 0}) == 0
    assert cli._readiness_forward_days({}) == 0


def test_race_rows_reject_target_like_feature_keys():
    race = _race(0)
    race["features"][0]["winner"] = 1
    with pytest.raises(ValueError, match="target_column_prohibited"):
        build_course_start_race_rows([race])


def _write_prediction_fixture(tmp_path, *, generated_at="2026-07-21T13:11:25+09:00", deadline="2026-07-21T13:25:00+09:00"):
    payload = {
        "raceDate": "2026-07-21",
        "raceId": "20260721-04-04",
        "venue": "04",
        "raceNo": 4,
        "deadlineJst": deadline,
        "generatedAtJst": generated_at,
        "modelVersion": "tree_15",
        "modelSha256": CHAMPION_MODEL_SHA256,
        "featureSchemaVersion": "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd",
        "inputSha256": "f" * 64,
        "probabilities": [
            {"boatNo": boat_no, "probability": 1 / 6, "rank": boat_no}
            for boat_no in range(1, 7)
        ],
    }
    payload["predictionSha256"] = stable_hash(payload)
    path = tmp_path / "prediction.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_prediction_verifier_rejects_missing_timing_fields(tmp_path):
    path = _write_prediction_fixture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("generatedAtJst")
    payload.pop("predictionSha256", None)
    payload["predictionSha256"] = stable_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="prediction_timing_invalid"):
        cli._verify_prediction(path)


def test_prediction_verifier_rejects_late_prediction(tmp_path):
    path = _write_prediction_fixture(
        tmp_path,
        generated_at="2026-07-21T13:25:00+09:00",
    )

    with pytest.raises(ValueError, match="prediction_timing_invalid"):
        cli._verify_prediction(path)


def test_prediction_verifier_rejects_timezone_naive_timestamps(tmp_path):
    path = _write_prediction_fixture(
        tmp_path,
        generated_at="2026-07-21T13:11:25",
        deadline="2026-07-21T13:25:00",
    )

    with pytest.raises(ValueError, match="prediction_timing_invalid"):
        cli._verify_prediction(path)


def test_course_start_rows_require_explicit_true_eligibility_flags():
    race = _race(0)
    race["researchEligible"] = None

    with pytest.raises(ValueError, match="feature_provenance_invalid"):
        build_course_start_race_rows([race])


def test_joined_races_skip_rejected_feature_snapshots():
    race = _race(0)
    prediction = {
        "raceDate": race["raceDate"],
        "venue": race["venue"],
        "raceNo": race["raceNo"],
        "probabilities": race["baselineProbabilities"],
    }
    records = [
        {
            "raceDate": race["raceDate"],
            "jcd": race["venue"],
            "raceNo": race["raceNo"],
            "boatNo": feature["boatNo"],
            "featureGroup": FEATURE_GROUP,
            "values": feature,
            "secondsBeforeDeadline": 420.0,
            "captureTimestampVerified": True,
            "provenanceVerified": True,
            "schemaVerified": True,
            "researchEligible": False if feature["boatNo"] == 1 else True,
        }
        for feature in race["features"]
    ]

    joined = cli.build_joined_race_rows(
        {race["raceKey"]: prediction},
        {race["raceKey"]: {"winnerBoat": 1, "void": False}},
        records,
    )

    assert joined == []


def test_cli_parser_requires_model_artifact():
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as error:
        parser.parse_args([
            "--prediction-root", "predictions",
            "--settlement-root", "settlements",
            "--feature-store", "store",
            "--report-root", "reports/feature_forward",
            "--b-root", "entries",
            "--request-ledger", "request.sqlite3",
        ])

    assert error.value.code == 2


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


def test_oof_evaluation_requires_minimum_validation_sample():
    races = [_race(index, winner=1 if index % 3 else 2) for index in range(36)]

    result = evaluate_course_start_challenger(races, bootstrap_repetitions=10)

    assert "insufficient_oof_validation_sample" in result["adoptionReasons"]
    assert result["oofValidationRaceCount"] == 30
    assert result["oofValidationDateCount"] == 30


def test_segment_stability_rejects_material_log_loss_degradation():
    segments = {
        "venue": {
            "01": {
                "raceCount": 100,
                "baseline": {"logLoss": 1.0},
                "candidate": {"logLoss": 1.003},
            },
        },
    }

    failures = segment_stability_failures(segments)

    assert failures == ["segment_stability_failed:venue:01"]


def test_brier_gate_requires_four_non_degraded_folds_and_non_degraded_aggregate():
    folds = [
        {"deltaBrier": -0.01},
        {"deltaBrier": -0.01},
        {"deltaBrier": 0.0},
        {"deltaBrier": 0.01},
        {"deltaBrier": 0.01},
    ]

    assert brier_adoption_failures(folds, aggregate_difference=-0.001) == [
        "brier_not_non_degraded_in_4_of_5_folds",
    ]
    assert brier_adoption_failures(
        [
            {"deltaBrier": -0.01},
            {"deltaBrier": -0.01},
            {"deltaBrier": -0.01},
            {"deltaBrier": -0.01},
            {"deltaBrier": 0.01},
        ],
        aggregate_difference=0.0001,
    ) == ["brier_aggregate_degraded"]


def test_oof_does_not_split_a_calendar_day_across_folds():
    races = []
    for day in range(7):
        date = f"2026-02-{day + 1:02d}"
        for race_no in range(1, 4):
            race = _race(day * 3 + race_no - 1, winner=1 if race_no != 3 else 2)
            race["raceDate"] = date
            race["raceKey"] = f"{date}-01-{race_no:02d}"
            race["raceNo"] = race_no
            races.append(race)

    result = evaluate_course_start_challenger(races, bootstrap_repetitions=10)

    assert all(
        fold["trainEnd"] < fold["validationStart"]
        for fold in result["folds"]
    )


def test_evaluation_is_deterministic_and_does_not_mutate_inputs():
    races = [_race(index, winner=1 if index % 2 else 3) for index in range(36)]
    original = copy.deepcopy(races)
    first = evaluate_course_start_challenger(races, bootstrap_repetitions=100)
    second = evaluate_course_start_challenger(races, bootstrap_repetitions=100)

    assert first == second
    assert races == original


def test_candidate_prediction_digest_covers_prediction_rows():
    result = {
        "candidate": {
            "predictions": [
                {"raceKey": "r1", "probabilities": [1 / 6] * 6},
            ],
        },
    }
    changed = copy.deepcopy(result)
    changed["candidate"]["predictions"][0]["probabilities"][0] = 0.2

    assert candidate_prediction_digest(result) != candidate_prediction_digest(changed)


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
    assert schedule == [{
        "raceDate": "2026-07-31",
        "jcd": "10",
        "raceNo": 1,
        "deadlineJst": "2026-07-31T12:00:00+09:00",
        "timeBand": "unknown",
    }]
    assert len(metadata["sourceFiles"][0]["sha256"]) == 64


def test_mature_schedule_excludes_races_before_capture_window_end():
    schedule = [
        {
            "raceDate": "2026-08-08",
            "jcd": "10",
            "raceNo": 1,
            "deadlineJst": "2026-08-08T07:45:00+09:00",
        },
        {
            "raceDate": "2026-08-08",
            "jcd": "10",
            "raceNo": 2,
            "deadlineJst": "2026-08-08T08:00:00+09:00",
        },
        {
            "raceDate": "2026-08-08",
            "jcd": "10",
            "raceNo": 3,
            "deadlineJst": "2026-08-08T07:47:00+09:00",
        },
    ]

    mature, not_due = cli.mature_selected_schedule(
        schedule,
        as_of=pd.Timestamp("2026-08-08T07:41:00+09:00").to_pydatetime(),
    )

    assert [(row["jcd"], row["raceNo"]) for row in mature] == [("10", 1)]
    assert [(row["jcd"], row["raceNo"]) for row in not_due] == [("10", 2), ("10", 3)]


def test_mature_schedule_fails_closed_for_naive_deadline():
    with pytest.raises(ValueError, match="schedule_deadline_timezone_required"):
        cli.mature_selected_schedule(
            [{
                "raceDate": "2026-08-08",
                "jcd": "10",
                "raceNo": 1,
                "deadlineJst": "2026-08-08T07:45:00",
            }],
            as_of=pd.Timestamp("2026-08-08T07:41:00+09:00").to_pydatetime(),
        )


def test_capture_coverage_views_keep_raw_diagnostic_and_use_mature_gate_value():
    raw = {FEATURE_GROUP: {"coverage": 484 / 636, "scheduledRaceCount": 636}}
    mature = {FEATURE_GROUP: {"coverage": 484 / 576, "scheduledRaceCount": 576}}

    quality = cli.attach_capture_coverage_views(
        raw,
        mature,
        raw_selected_race_count=636,
        mature_selected_race_count=576,
        capture_window_not_due_race_count=60,
    )

    entry = quality[FEATURE_GROUP]
    assert entry["coverage"] == pytest.approx(484 / 576)
    assert entry["rawCaptureCoverage"] == pytest.approx(484 / 636)
    assert entry["matureCaptureCoverage"] == pytest.approx(484 / 576)
    assert entry["matureSelectedRaceCount"] == 576
    assert entry["captureWindowNotDueRaceCount"] == 60
    assert entry["coverageDefinition"] == "mature_selected_capture_window_passed"


def test_assessment_dates_include_scope_date_without_feature_snapshot():
    feature_keys = [("2026-07-30", "10", 1)]

    assert cli.derive_assessment_dates(
        feature_keys,
        {"2026-07-30", "2026-07-31"},
    ) == ["2026-07-30", "2026-07-31"]


def test_assessment_dates_reset_after_calendar_gap():
    feature_keys = [("2026-08-12", "10", 1)]

    assert cli.derive_assessment_dates(
        feature_keys,
        {"2026-08-12", "2026-08-15"},
    ) == ["2026-08-15"]


def test_evaluation_cohort_digest_changes_when_joined_race_is_added():
    base = cli.build_evaluation_cohort_manifest(
        assessment_dates=["2026-07-30", "2026-07-31"],
        joined_race_keys=["2026-07-30-10-01"],
        source_files=[],
        model_sha256=CHAMPION_MODEL_SHA256,
        feature_schema_sha256=cli.FEATURE_SCHEMA_SHA256,
    )
    changed = cli.build_evaluation_cohort_manifest(
        assessment_dates=["2026-07-30", "2026-07-31"],
        joined_race_keys=["2026-07-30-10-01", "2026-07-31-10-01"],
        source_files=[],
        model_sha256=CHAMPION_MODEL_SHA256,
        feature_schema_sha256=cli.FEATURE_SCHEMA_SHA256,
    )

    assert base["cohortDigest"] != changed["cohortDigest"]
    assert cli.cohort_manifest_matches(base, base) is True
    assert cli.cohort_manifest_matches(base, changed) is False


def test_evaluation_artifact_binds_to_cohort_spec_and_deterministic_result():
    cohort_digest = "c" * 64
    spec_hash = "d" * 64
    reproducibility_manifest = {
        "schemaVersion": 1,
        "artifactType": "OOF_REPRODUCIBILITY_MANIFEST",
        "gitHead": "e" * 40,
        "gitStatusPorcelain": [" M src/feature_forward_v1/course_start_challenger.py"],
        "dirtyWorktree": True,
        "trackedDiffPath": "reports/feature_forward/oof_reproducibility.patch",
        "trackedDiffSha256": "a" * 64,
        "untrackedFiles": ["src/feature_forward_v1/oof_readiness.py"],
        "untrackedManifestSha256": "b" * 64,
        "configPath": "config/feature_forward_v1/oof_evaluation_spec.json",
        "configSha256": spec_hash,
        "oofSpecPath": "config/feature_forward_v1/oof_evaluation_spec.json",
        "oofSpecSha256": spec_hash,
        "productionAdoptionAllowed": False,
        "oofExecuted": False,
        "note": "tracked patch and untracked names only",
    }
    evaluation = {
        "status": "NO_CHALLENGER_FOUND",
        "modelSha256": CHAMPION_MODEL_SHA256,
        "deterministicRerunPassed": True,
        "productionAdoptionAllowed": False,
        "oofValidationRaceCount": 1250,
        "oofValidationDateCount": 25,
        "candidate": {"predictions": [{"raceKey": "r1", "probabilities": [1 / 6] * 6}]},
    }
    artifact = cli.build_evaluation_artifact(
        evaluation,
        cohort_digest=cohort_digest,
        spec_hash=spec_hash,
        reproducibility_manifest=reproducibility_manifest,
    )

    assert artifact["personalAdoptionAllowed"] is False
    assert artifact["reproducibilityManifest"] == reproducibility_manifest
    assert cli._evaluation_artifact_is_current(
        artifact,
        cohort_digest=cohort_digest,
        spec_hash=spec_hash,
    ) is True
    assert cli._evaluation_artifact_is_current(
        artifact,
        cohort_digest="f" * 64,
        spec_hash=spec_hash,
    ) is False
    assert cli._evaluation_artifact_is_current(
        artifact,
        cohort_digest=cohort_digest,
        spec_hash="f" * 64,
    ) is False

    tampered = copy.deepcopy(artifact)
    tampered["oofValidationRaceCount"] = 1251
    assert cli._evaluation_artifact_is_current(
        tampered,
        cohort_digest=cohort_digest,
        spec_hash=spec_hash,
    ) is False

    nondeterministic = cli.build_evaluation_artifact(
        {**evaluation, "deterministicRerunPassed": False},
        cohort_digest=cohort_digest,
        spec_hash=spec_hash,
        reproducibility_manifest=reproducibility_manifest,
    )
    assert cli._evaluation_artifact_is_current(
        nondeterministic,
        cohort_digest=cohort_digest,
        spec_hash=spec_hash,
    ) is False

    with pytest.raises(ValueError, match="evaluation_reproducibility_manifest_invalid"):
        cli.build_evaluation_artifact(
            evaluation,
            cohort_digest=cohort_digest,
            spec_hash=spec_hash,
            reproducibility_manifest={
                **reproducibility_manifest,
                "oofSpecSha256": "f" * 64,
            },
        )


def test_unverified_schedule_denominator_blocks_evaluation():
    report = build_readiness_report(
        settled_races=1500,
        feature_races=1500,
        quality={
            "coverage": 1.0,
            "captureTimestampVerifiedRate": 1.0,
            "provenanceVerifiedRate": 1.0,
            "schemaConsistencyRate": 1.0,
            "deterministicParsing": True,
            "postDeadlineCount": 0,
            "resultLeakageCount": 0,
            "duplicateCount": 0,
        },
        model_sha256=CHAMPION_MODEL_SHA256,
        observed_forward_days=30,
    )

    assert report["status"] == "COURSE_START_CHALLENGER_READY"
    cli._apply_coverage_gate(report, {"status": "UNAVAILABLE"})

    assert report["status"] == "CHALLENGER_EVALUATION_BLOCKED"
    assert report["evaluationExecuted"] is False
    assert "coverage_denominator_unavailable" in report["blockedReasons"]
