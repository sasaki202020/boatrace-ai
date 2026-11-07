from __future__ import annotations

from src.pipeline.candidate_metadata import LEGACY_UNKNOWN, enrich_candidate_metadata


def test_enrich_candidate_metadata_persists_forward_only_fields() -> None:
    row = {
        "combo": "1-2-3",
        "decision": "WATCH",
        "prob": 0.1234,
        "odds": 8.5,
        "reason": "paper_only",
    }

    enriched = enrich_candidate_metadata(
        row,
        race_date="20260710",
        jcd="24",
        race_no=1,
        race_id="20260710-24-01",
        model_version="baseline_rule_v1",
        policy_version="paper_shadow_policy_v1",
        feature_version="baseline_score_features_v1",
        odds_captured_at="2026-07-10T11:55:00",
        deadline_at="2026-07-10T12:00:00",
        frozen_at="2026-07-10T11:56:00",
        snapshot_payload={"date": "20260710", "jcd": "24", "raceNo": 1},
    )

    assert enriched["candidateId"]
    assert enriched["snapshotHash"]
    assert enriched["modelVersion"] == "baseline_rule_v1"
    assert enriched["calibratorVersion"] == "uncalibrated_identity_v1"
    assert enriched["policyVersion"] == "paper_shadow_policy_v1"
    assert enriched["predictionHash"]
    assert enriched["rawProbability"] == 0.1234
    assert enriched["calibratedProbability"] == 0.1234
    assert enriched["odds"] == 8.5
    assert enriched["oddsCapturedAt"] == "2026-07-10T11:55:00"
    assert enriched["deadlineAt"] == "2026-07-10T12:00:00"
    assert enriched["policyDecision"] == "WATCH"
    assert enriched["guardDecision"] == "PASS"
    assert enriched["guardReason"] == "paper_only"
    assert enriched["frozenAt"] == "2026-07-10T11:56:00"


def test_enrich_candidate_metadata_does_not_guess_missing_timestamps() -> None:
    enriched = enrich_candidate_metadata(
        {"combo": "1-2-3", "decision": "SKIP", "prob": None},
        race_date="20260710",
        jcd="24",
        race_no=1,
        race_id="20260710-24-01",
        model_version="baseline_rule_v1",
        policy_version="paper_shadow_policy_v1",
        feature_version="baseline_score_features_v1",
        odds_captured_at="",
        deadline_at="",
        frozen_at="2026-07-10T11:56:00",
        snapshot_payload={},
    )

    assert enriched["oddsCapturedAt"] == LEGACY_UNKNOWN
    assert enriched["deadlineAt"] == LEGACY_UNKNOWN
    assert enriched["rawProbability"] == LEGACY_UNKNOWN
    assert enriched["calibratedProbability"] == LEGACY_UNKNOWN
