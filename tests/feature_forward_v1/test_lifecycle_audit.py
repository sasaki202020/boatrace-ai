from __future__ import annotations

from src.feature_forward_v1.lifecycle_audit import (
    CAPTURE_STATUS,
    SETTLEMENT_STATUS,
    classify_capture_status,
    classify_settlement_status,
    pace_metrics,
)


def test_capture_classification_separates_received_valid_rejected_and_unknown():
    assert classify_capture_status(
        selected=True,
        snapshot_status="CAPTURED",
        research_eligible=True,
        reasons=[],
        request_outcome="HTTP_OK",
    ) == "VALID_CAPTURE"
    assert classify_capture_status(
        selected=True,
        snapshot_status="REJECTED",
        research_eligible=False,
        reasons=["FEATURE_VALUE_INVALID"],
        request_outcome="HTTP_OK",
    ) == "PARSE_FAILURE"
    assert classify_capture_status(
        selected=True,
        snapshot_status=None,
        research_eligible=False,
        reasons=[],
        request_outcome="READTIMEOUT",
    ) == "NETWORK_ERROR"
    assert classify_capture_status(
        selected=True,
        snapshot_status=None,
        research_eligible=False,
        reasons=[],
        request_outcome=None,
    ) == "UNKNOWN_LEGACY"
    assert classify_capture_status(
        selected=False,
        snapshot_status=None,
        research_eligible=False,
        reasons=[],
        request_outcome=None,
    ) == "NOT_SELECTED_BY_DAILY_CAP"
    assert CAPTURE_STATUS >= {
        "VALID_CAPTURE",
        "UNKNOWN_LEGACY",
        "NETWORK_ERROR",
    }


def test_settlement_classification_does_not_infer_missing_result():
    assert classify_settlement_status(
        settlement_exists=True,
        settlement_valid=True,
        result_source_exists=True,
    ) == "SETTLED"
    assert classify_settlement_status(
        settlement_exists=False,
        settlement_valid=False,
        result_source_exists=False,
    ) == "RESULT_PENDING"
    assert classify_settlement_status(
        settlement_exists=True,
        settlement_valid=False,
        result_source_exists=True,
    ) == "DUPLICATE_OR_CONFLICT"
    assert classify_settlement_status(
        settlement_exists=False,
        settlement_valid=False,
        result_source_exists=True,
    ) == "RESULT_UNAVAILABLE"
    assert SETTLEMENT_STATUS >= {"SETTLED", "RESULT_PENDING", "UNKNOWN_LEGACY"}


def test_pace_metrics_are_explicit_and_do_not_use_mixed_denominators():
    result = pace_metrics(
        observation_calendar_days=11,
        collector_running_days=11,
        selected_races=276,
        feature_settled_races=145,
        valid_capture_rate=204 / 276,
        settlement_join_rate=145 / 204,
        planned_selected_races_per_day=(12, 60),
    )

    assert result["currentCalendarPace"] == 145 / 11
    assert result["currentRunningDayPace"] == 145 / 11
    assert result["scenarios"]["12"]["usablePerDay"] == 12 * (204 / 276) * (145 / 204)
    assert result["scenarios"]["60"]["usablePerDay"] == 60 * (204 / 276) * (145 / 204)
    assert result["scenarios"]["60"]["estimatedDaysTo1500"] == 43
