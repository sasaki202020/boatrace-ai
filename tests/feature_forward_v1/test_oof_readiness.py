from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.feature_forward_v1.oof_readiness import (
    build_fold_preflight,
    build_oof_preflight,
    load_oof_spec,
    oof_execution_allowed,
    plan_chronological_fold_groups,
)


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"


def _races(count: int = 30) -> list[dict[str, str]]:
    return [
        {
            "raceKey": f"2026-01-{index + 1:02d}-01-01",
            "raceDate": f"2026-01-{index + 1:02d}",
        }
        for index in range(count)
    ]


def test_fixed_spec_is_chronological_and_fail_closed():
    spec = load_oof_spec(SPEC)

    assert spec["mode"] == "PRE_FLIGHT_ONLY"
    assert spec["split"]["randomSplit"] is False
    assert spec["split"]["sameRaceSingleFold"] is True
    assert spec["split"]["preprocessingFit"] == "train_only"
    assert spec["comparison"]["primaryMetric"] == "log_loss"
    assert spec["comparison"]["secondaryMetrics"] == ["brier", "top1", "ece"]
    assert spec["diagnosticGate"]["minimumFeatureSettledRaces"] == 500
    assert spec["diagnosticGate"]["minimumValidationRacesPerFold"] == 75
    assert spec["decisionGate"]["minimumFeatureSettledRaces"] == 1500
    assert spec["decisionGate"]["minimumValidationRacesPerFold"] == 250
    assert spec["adoption"]["productionAdoptionAllowed"] is False
    assert spec["adoption"]["personalAdoptionAllowed"] is False


def test_fold_preflight_is_chronological_and_does_not_use_labels():
    result = build_fold_preflight(
        _races(),
        minimum_validation_races_per_fold=250,
    )

    assert result["method"] == "chronological_5_fold"
    assert result["randomSplit"] is False
    assert result["foldCount"] == 5
    assert all(fold["raceOverlap"] == 0 for fold in result["folds"])
    assert all(
        fold["trainEnd"] < fold["validationStart"]
        for fold in result["folds"]
    )
    assert "minimum_validation_races_per_fold_not_met" in result["blockedReasons"]
    assert result["accounting"]["accountingPass"] is True
    assert result["accounting"]["totalEligibleRaceCount"] == 30
    assert result["accounting"]["initialTrainRaceCount"] == 5
    assert result["accounting"]["validationRaceCount"] == 25
    assert result["accounting"]["otherExcludedRaceCount"] == 0


def test_preflight_blocks_before_minimums_and_explicit_approval():
    spec = load_oof_spec(SPEC)
    fold = build_fold_preflight(_races(), minimum_validation_races_per_fold=250)
    result = build_oof_preflight(
        spec=spec,
        forward_days=11,
        coverage=0.742,
        feature_settled_races=145,
        new_unknown_count=0,
        terminal_conflict_count=0,
        leakage_count=0,
        hash_chain_valid=True,
        fold_preflight=fold,
        snapshot={"snapshotId": "snapshot-1"},
    )

    assert result["status"] == "BLOCKED_WAITING_FOR_EXTERNAL_DATA"
    assert result["dataGateEligible"] is False
    assert result["executionAllowed"] is False
    assert "minimum_feature_settled_races_not_met" in result["blockedReasons"]
    assert "explicit_oof_execution_approval_required" in result["blockedReasons"]


def test_preflight_never_allows_execution_without_explicit_flag():
    spec = load_oof_spec(SPEC)
    fold = build_fold_preflight(_races(1500), minimum_validation_races_per_fold=250)
    result = build_oof_preflight(
        spec=spec,
        forward_days=30,
        coverage=0.8,
        feature_settled_races=500,
        new_unknown_count=0,
        terminal_conflict_count=0,
        leakage_count=0,
        hash_chain_valid=True,
        fold_preflight=fold,
        snapshot={"snapshotId": "snapshot-1"},
    )

    assert result["dataGateEligible"] is True
    assert result["executionAllowed"] is False
    assert result["requiresExplicitApproval"] is True


def test_diagnostic_gate_can_pass_before_decision_gate():
    spec = load_oof_spec(SPEC)
    fold = build_fold_preflight(_races(500), minimum_validation_races_per_fold=75)
    result = build_oof_preflight(
        spec=spec,
        forward_days=30,
        coverage=0.8,
        feature_settled_races=500,
        new_unknown_count=0,
        terminal_conflict_count=0,
        leakage_count=0,
        hash_chain_valid=True,
        production_relevant_failure_count=0,
        fold_preflight=fold,
        snapshot={"snapshotId": "snapshot-1"},
    )

    assert result["diagnosticGateEligible"] is True
    assert result["decisionGateEligible"] is False
    assert result["status"] == "DIAGNOSTIC_OOF_READY_AWAITING_DECISION_SAMPLE"


def test_explicit_oof_flag_cannot_bypass_decision_gate():
    assert oof_execution_allowed(
        True,
        {
            "decisionGateEligible": False,
            "requiresExplicitApproval": True,
            "productionAdoptionAllowed": False,
        },
    ) is False
    assert oof_execution_allowed(
        True,
        {
            "decisionGateEligible": True,
            "requiresExplicitApproval": True,
            "productionAdoptionAllowed": False,
        },
    ) is True
    assert oof_execution_allowed(
        True,
        {
            "decisionGateEligible": True,
            "requiresExplicitApproval": True,
            "productionAdoptionAllowed": True,
        },
    ) is False


def test_spec_rejects_random_split(tmp_path):
    payload = json.loads(SPEC.read_text(encoding="utf-8"))
    payload["split"]["randomSplit"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="oof_spec_split_not_fail_closed"):
        load_oof_spec(path)


def test_spec_rejects_missing_required_secondary_metric(tmp_path):
    payload = json.loads(SPEC.read_text(encoding="utf-8"))
    payload["comparison"]["secondaryMetrics"] = ["brier", "top1"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="oof_spec_secondary_metrics_invalid"):
        load_oof_spec(path)


def test_spec_rejects_personal_auto_adoption(tmp_path):
    payload = json.loads(SPEC.read_text(encoding="utf-8"))
    payload["adoption"]["personalAdoptionAllowed"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="oof_spec_adoption_not_fail_closed"):
        load_oof_spec(path)


def test_fold_balancing_keeps_date_groups_intact():
    races = []
    counts = [1, 10, 1, 10, 1, 10, 1, 10, 1, 10, 1]
    for date_index, count in enumerate(counts, start=1):
        date = f"2026-02-{date_index:02d}"
        for race_index in range(count):
            races.append({
                "raceKey": f"{date}-{race_index:02d}",
                "raceDate": date,
            })

    plan = plan_chronological_fold_groups(races)
    fold_counts = [
        sum(1 for race in races if race["raceDate"] in fold_dates)
        for fold_dates in plan["foldDates"]
    ]

    assert plan["blockedReasons"] == []
    assert max(fold_counts) - min(fold_counts) <= 1
    assert len(set(plan["initialTrainDates"]).intersection(*map(set, plan["foldDates"]))) == 0
