from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.market_evaluation_v1.odds_snapshots import (
    OddsSnapshotError,
    append_snapshot,
    evaluate_odds_movement,
    read_snapshots,
    verify_snapshot_store,
)


UTC = timezone.utc
DEADLINE = datetime(2026, 8, 2, 3, 0, tzinfo=UTC)


def _snapshot(*, stage: str = "DECISION_TIME", odds: float = 10.0) -> dict[str, object]:
    captured = DEADLINE - timedelta(minutes=5)
    if stage == "CLOSING_TIME":
        captured = DEADLINE + timedelta(seconds=1)
    if stage == "FINAL_PAYOUT":
        captured = DEADLINE + timedelta(minutes=10)
    return {
        "targetDate": "2026-08-02",
        "venue": "01",
        "raceNo": 1,
        "trifecta": "1-2-3",
        "odds": odds,
        "stage": stage,
        "capturedAtUtc": captured.isoformat(),
        "raceDeadline": DEADLINE.isoformat(),
        "secondsToDeadline": (DEADLINE - captured).total_seconds(),
        "source": "local_fixture",
        "sourceHash": "a" * 64,
        "collectorRunId": "run-1",
        "lifecycleEventId": f"event-{stage}",
    }


def test_post_deadline_prediction_input_is_rejected() -> None:
    snapshot = _snapshot()
    snapshot["capturedAtUtc"] = (DEADLINE + timedelta(seconds=1)).isoformat()
    snapshot["secondsToDeadline"] = -1.0
    with pytest.raises(OddsSnapshotError, match="post_deadline_prediction_input"):
        append_snapshot(__import__("pathlib").Path("unused.sqlite"), snapshot)


def test_snapshot_store_is_idempotent_append_only_and_chain_valid(tmp_path) -> None:
    path = tmp_path / "snapshots.sqlite3"
    first = _snapshot()
    created = append_snapshot(path, first)
    replay = append_snapshot(path, first)
    assert created["status"] == "CREATED"
    assert replay["status"] == "IDEMPOTENT"
    assert verify_snapshot_store(path)["snapshotCount"] == 1

    changed = dict(first, odds=11.0)
    with pytest.raises(OddsSnapshotError, match="snapshot_identity_conflict"):
        append_snapshot(path, changed)

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="append_only"):
            connection.execute("UPDATE odds_snapshots SET payload_json = '{}' WHERE sequence = 1")
        with pytest.raises(sqlite3.DatabaseError, match="append_only"):
            connection.execute("DELETE FROM odds_snapshots WHERE sequence = 1")


def test_movement_uses_decision_and_closing_only(tmp_path) -> None:
    path = tmp_path / "snapshots.sqlite3"
    decision = _snapshot(stage="DECISION_TIME", odds=10.0)
    closing = _snapshot(stage="CLOSING_TIME", odds=8.0)
    append_snapshot(path, decision)
    append_snapshot(path, closing)
    rows = [row["payload"] for row in read_snapshots(path)]
    result = evaluate_odds_movement(rows)
    assert result["pairCount"] == 1
    assert result["pairs"][0]["finalDecisionRatio"] == pytest.approx(0.8)
    assert result["pairs"][0]["movementRate"] == pytest.approx(-0.2)
    assert result["byVenue"][0]["pairCount"] == 1
    assert result["byOddsBand"][0]["meanMovementRate"] == pytest.approx(-0.2)


def test_final_payout_is_not_prediction_input() -> None:
    from src.market_evaluation_v1.odds_snapshots import validate_snapshot

    validated = validate_snapshot(_snapshot(stage="FINAL_PAYOUT"))
    assert validated["predictionInputAllowed"] is False
