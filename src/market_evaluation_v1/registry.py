from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .odds_snapshots import canonical_json, sha256_json


REQUIRED_FIELDS = {
    "experimentId",
    "featureSet",
    "modelVersion",
    "policyVersion",
    "codeCommit",
    "dataSnapshot",
    "trainPeriod",
    "validationPeriod",
    "holdoutPeriod",
    "trialNumber",
    "metrics",
    "decision",
    "reason",
    "createdAt",
    "researchOnly",
    "productionAdoptionAllowed",
}


def _validate(payload: dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        raise ValueError(f"market_experiment_missing:{','.join(sorted(missing))}")
    if payload["decision"] not in {"planned", "completed", "rejected", "blocked"}:
        raise ValueError("market_experiment_decision_invalid")
    if not isinstance(payload["trialNumber"], int) or payload["trialNumber"] < 1:
        raise ValueError("market_experiment_trial_invalid")
    if payload["researchOnly"] is not True or payload["productionAdoptionAllowed"] is not False:
        raise ValueError("market_experiment_isolation_invalid")
    if not isinstance(payload["metrics"], dict):
        raise ValueError("market_experiment_metrics_invalid")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def initialize_registry(path: Path) -> None:
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                previous_hash TEXT,
                record_hash TEXT NOT NULL UNIQUE
            );
            CREATE TRIGGER IF NOT EXISTS market_experiments_no_update
            BEFORE UPDATE ON experiments BEGIN
                SELECT RAISE(ABORT, 'market_experiment_append_only');
            END;
            CREATE TRIGGER IF NOT EXISTS market_experiments_no_delete
            BEFORE DELETE ON experiments BEGIN
                SELECT RAISE(ABORT, 'market_experiment_append_only');
            END;
            """
        )


def append_experiment(path: Path, payload: dict[str, Any]) -> str:
    _validate(payload)
    initialize_registry(path)
    payload_json = canonical_json(payload)
    with _connect(path) as connection:
        existing = connection.execute(
            "SELECT payload_json, record_hash FROM experiments WHERE experiment_id = ?",
            (payload["experimentId"],),
        ).fetchone()
        if existing:
            if existing[0] != payload_json:
                raise ValueError("market_experiment_id_conflict")
            return str(existing[1])
        previous = connection.execute(
            "SELECT record_hash FROM experiments ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous[0]) if previous else None
        record_hash = sha256_json({"payload": payload, "previousHash": previous_hash})
        connection.execute(
            "INSERT INTO experiments(experiment_id, payload_json, previous_hash, record_hash) VALUES (?, ?, ?, ?)",
            (payload["experimentId"], payload_json, previous_hash, record_hash),
        )
        return record_hash


def verify_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"valid": True, "experimentCount": 0, "tailHash": None}
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT sequence, payload_json, previous_hash, record_hash FROM experiments ORDER BY sequence"
        ).fetchall()
    finally:
        connection.close()
    previous_hash: str | None = None
    for expected_sequence, (sequence, payload_json, stored_previous, record_hash) in enumerate(rows, start=1):
        payload = json.loads(payload_json)
        _validate(payload)
        if sequence != expected_sequence or stored_previous != previous_hash:
            raise ValueError("market_experiment_chain_invalid")
        expected_hash = sha256_json({"payload": payload, "previousHash": previous_hash})
        if expected_hash != record_hash:
            raise ValueError("market_experiment_record_hash_invalid")
        previous_hash = record_hash
    return {"valid": True, "experimentCount": len(rows), "tailHash": previous_hash}
