from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.feature_forward_v1.lifecycle_ledger import (
    LifecycleConflictError,
    LifecycleLedger,
)


def event(**overrides):
    value = {
        "snapshot_id": "snapshot-1",
        "target_date": "2026-08-02",
        "venue": "01",
        "race_no": 1,
        "stage": "CAPTURE",
        "status_code": "VALID_CAPTURE",
        "occurred_at_utc": "2026-08-02T00:00:00+00:00",
        "collector_run_id": "collector-1",
        "task_run_id": "task-1",
        "attempt_no": 0,
        "source_policy_hash": "a" * 64,
        "config_hash": "b" * 64,
        "code_commit": "c" * 40,
        "reason_detail": "captured",
        "evidence_ref": "feature:snapshot-1",
    }
    value.update(overrides)
    return value


def test_lifecycle_event_is_idempotent_and_append_only(tmp_path):
    ledger = LifecycleLedger(tmp_path / "feature.sqlite3")
    first = ledger.append_event(**event())
    second = ledger.append_event(**event())

    assert first.created is True
    assert second.created is False
    assert first.event_id == second.event_id
    assert ledger.count() == 1
    assert ledger.verify_integrity()["valid"] is True

    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute(
            "UPDATE race_lifecycle_events SET status_code='PARSE_FAILURE'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute("DELETE FROM race_lifecycle_events")


def test_terminal_status_conflict_is_fail_closed(tmp_path):
    ledger = LifecycleLedger(tmp_path / "feature.sqlite3")
    ledger.append_event(**event())

    with pytest.raises(LifecycleConflictError, match="terminal_status_conflict"):
        ledger.append_event(
            **event(
                status_code="PARSE_FAILURE",
                reason_detail="different terminal result",
                evidence_ref="feature:rejected-1",
            )
        )


def test_pending_can_transition_to_settled_but_cannot_be_rewritten(tmp_path):
    ledger = LifecycleLedger(tmp_path / "feature.sqlite3")
    pending = ledger.append_event(
        **event(
            stage="SETTLEMENT",
            status_code="RESULT_PENDING",
            terminal=False,
            evidence_ref="result:pending",
        )
    )
    settled = ledger.append_event(
        **event(
            stage="SETTLEMENT",
            status_code="SETTLED",
            terminal=True,
            evidence_ref="result:k-1",
        )
    )

    assert pending.created is True
    assert settled.created is True
    assert ledger.count() == 2
    assert ledger.verify_integrity()["valid"] is True

    with pytest.raises(LifecycleConflictError, match="terminal_status_conflict"):
        ledger.append_event(
            **event(
                stage="SETTLEMENT",
                status_code="RESULT_UNAVAILABLE",
                terminal=True,
                evidence_ref="result:missing",
            )
        )


def test_event_hash_is_deterministic_for_same_history(tmp_path):
    first = LifecycleLedger(tmp_path / "a.sqlite3")
    second = LifecycleLedger(tmp_path / "b.sqlite3")
    a = first.append_event(**event())
    b = second.append_event(**event())

    assert a.event_id == b.event_id
    assert a.event_hash == b.event_hash
    assert a.ledger_sequence == b.ledger_sequence == 1


def test_same_observed_state_is_idempotent_across_runs(tmp_path):
    ledger = LifecycleLedger(tmp_path / "feature.sqlite3")
    first = ledger.append_event(**event())
    second = ledger.append_event(
        **event(
            collector_run_id="collector-2",
            task_run_id="task-2",
            occurred_at_utc="2026-08-02T00:01:00+00:00",
        )
    )

    assert first.created is True
    assert second.created is False
    assert second.event_id == first.event_id
    assert ledger.count() == 1


def test_same_terminal_observation_is_idempotent_after_code_commit_change(tmp_path):
    ledger = LifecycleLedger(tmp_path / "feature.sqlite3")
    first = ledger.append_event(**event(code_commit="a" * 40))

    second = ledger.append_event(
        **event(
            code_commit="d" * 40,
            collector_run_id="collector-2",
            task_run_id="task-2",
            occurred_at_utc="2026-08-02T00:01:00+00:00",
        )
    )

    assert first.created is True
    assert second.created is False
    assert second.event_id == first.event_id
    assert ledger.count() == 1
