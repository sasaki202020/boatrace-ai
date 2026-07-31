from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROHIBITED_KEY_TOKENS = (
    "buy",
    "bet",
    "ev",
    "odds",
    "profit",
    "roi",
    "stake",
    "投票",
    "払戻",
)
PROHIBITED_ASCII_KEYS = frozenset(
    ("buy", "bet", "ev", "odds", "profit", "roi", "stake", "betting")
)
PROHIBITED_NON_ASCII_TOKENS = ("投票", "払戻")
REQUIRED_STATE_KEYS = {
    "schemaVersion",
    "usageMode",
    "productionConnected",
    "prospectiveConnected",
    "productionAdoptionAllowed",
    "currentModelVersion",
    "activeFeatures",
    "knownProblems",
    "nextHypotheses",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_prohibited_key(key: Any) -> bool:
    text = str(key)
    lowered = text.lower()
    if lowered in PROHIBITED_ASCII_KEYS:
        return True
    if any(token in lowered for token in PROHIBITED_NON_ASCII_TOKENS):
        return True
    for token in PROHIBITED_ASCII_KEYS:
        if (
            lowered.startswith(token + "_")
            or lowered.endswith("_" + token)
            or f"_{token}_" in lowered
            or lowered.startswith(token + "-")
            or lowered.endswith("-" + token)
            or f"-{token}-" in lowered
        ):
            return True
        # Catch explicit camelCase fields such as evScore without rejecting evaluation.
        if text.startswith(token) and len(text) > len(token) and text[len(token)].isupper():
            return True
    return False


def _contains_prohibited_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _is_prohibited_key(key):
                return str(key)
            found = _contains_prohibited_key(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _contains_prohibited_key(nested)
            if found:
                return found
    return None


def validate_research_state(state: dict[str, Any]) -> None:
    missing = REQUIRED_STATE_KEYS - set(state)
    if missing:
        raise ValueError(f"research_state_missing:{','.join(sorted(missing))}")
    if state.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("research_state_schema_unsupported")
    if state.get("usageMode") != "RESEARCH_ONLY":
        raise ValueError("research_state_usage_mode_invalid")
    for field in ("productionConnected", "prospectiveConnected", "productionAdoptionAllowed"):
        if state.get(field) is not False:
            raise ValueError(f"research_state_{field}_must_be_false")
    if not isinstance(state.get("currentModelVersion"), dict):
        raise ValueError("research_state_model_invalid")
    if not isinstance(state.get("activeFeatures"), list):
        raise ValueError("research_state_features_invalid")
    if not isinstance(state.get("knownProblems"), list):
        raise ValueError("research_state_problems_invalid")
    if not isinstance(state.get("nextHypotheses"), list):
        raise ValueError("research_state_hypotheses_invalid")
    prohibited = _contains_prohibited_key(state)
    if prohibited:
        raise ValueError(f"research_state_prohibited_key:{prohibited}")


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    path = path.resolve()
    if read_only:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_registry(path: Path) -> None:
    connection = _connect(path, read_only=False)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT,
                record_hash TEXT NOT NULL UNIQUE
            );
            CREATE TRIGGER IF NOT EXISTS experiments_no_update
            BEFORE UPDATE ON experiments
            BEGIN
                SELECT RAISE(ABORT, 'research_memory_append_only');
            END;
            CREATE TRIGGER IF NOT EXISTS experiments_no_delete
            BEFORE DELETE ON experiments
            BEGIN
                SELECT RAISE(ABORT, 'research_memory_append_only');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()


def _validate_experiment(payload: dict[str, Any]) -> None:
    required = {
        "experimentId",
        "hypothesis",
        "datasetPeriod",
        "modelVersion",
        "baseline",
        "result",
        "decision",
        "reason",
        "sourceReports",
        "createdAt",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"experiment_missing:{','.join(sorted(missing))}")
    if not isinstance(payload["experimentId"], str) or not payload["experimentId"]:
        raise ValueError("experiment_id_invalid")
    if payload["decision"] not in {"planned", "completed", "rejected", "blocked"}:
        raise ValueError("experiment_decision_invalid")
    if not isinstance(payload["sourceReports"], list):
        raise ValueError("experiment_source_reports_invalid")
    prohibited = _contains_prohibited_key(payload)
    if prohibited:
        raise ValueError(f"experiment_prohibited_key:{prohibited}")


def append_experiment(path: Path, payload: dict[str, Any]) -> str:
    """Append one experiment; identical replays are idempotent."""
    _validate_experiment(payload)
    initialize_registry(path)
    connection = _connect(path, read_only=False)
    try:
        payload_json = canonical_json(payload)
        existing = connection.execute(
            "SELECT payload_json, record_hash FROM experiments WHERE experiment_id = ?",
            (payload["experimentId"],),
        ).fetchone()
        if existing:
            if existing[0] != payload_json:
                raise ValueError("experiment_id_conflict")
            return str(existing[1])
        previous = connection.execute(
            "SELECT record_hash FROM experiments ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous[0]) if previous else None
        envelope = {"payload": payload, "previousHash": previous_hash}
        record_hash = sha256_json(envelope)
        connection.execute(
            """
            INSERT INTO experiments
                (experiment_id, created_at, payload_json, previous_hash, record_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload["experimentId"],
                payload["createdAt"],
                payload_json,
                previous_hash,
                record_hash,
            ),
        )
        connection.commit()
        return record_hash
    finally:
        connection.close()


def read_experiments(path: Path) -> list[dict[str, Any]]:
    connection = _connect(path, read_only=True)
    try:
        rows = connection.execute(
            "SELECT sequence, payload_json, previous_hash, record_hash FROM experiments ORDER BY sequence"
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "sequence": int(sequence),
            "payload": json.loads(payload_json),
            "previousHash": previous_hash,
            "recordHash": record_hash,
        }
        for sequence, payload_json, previous_hash, record_hash in rows
    ]


def verify_registry(path: Path) -> dict[str, Any]:
    rows = read_experiments(path)
    previous_hash: str | None = None
    for expected_sequence, row in enumerate(rows, start=1):
        if row["sequence"] != expected_sequence or row["previousHash"] != previous_hash:
            raise ValueError("research_memory_chain_invalid")
        expected_hash = sha256_json({"payload": row["payload"], "previousHash": previous_hash})
        if expected_hash != row["recordHash"]:
            raise ValueError("research_memory_record_hash_invalid")
        _validate_experiment(row["payload"])
        previous_hash = row["recordHash"]
    return {
        "valid": True,
        "experimentCount": len(rows),
        "tailHash": previous_hash,
    }
