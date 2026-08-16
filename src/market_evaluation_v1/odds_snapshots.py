from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .market_baseline import normalize_trifecta


SNAPSHOT_STAGES = (
    "T_MINUS_20",
    "T_MINUS_10",
    "T_MINUS_5",
    "T_MINUS_2",
    "DECISION_TIME",
    "CLOSING_TIME",
    "FINAL_PAYOUT",
)
PREDICTION_INPUT_STAGES = frozenset(
    {"T_MINUS_20", "T_MINUS_10", "T_MINUS_5", "T_MINUS_2", "DECISION_TIME"}
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class OddsSnapshotError(ValueError):
    """Raised when a snapshot violates the research-only odds contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise OddsSnapshotError(f"{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OddsSnapshotError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise OddsSnapshotError(f"{field}_timezone_missing")
    return parsed


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    required = {
        "targetDate",
        "venue",
        "raceNo",
        "trifecta",
        "odds",
        "stage",
        "capturedAtUtc",
        "raceDeadline",
        "secondsToDeadline",
        "source",
        "sourceHash",
        "collectorRunId",
        "lifecycleEventId",
    }
    missing = required - set(snapshot)
    if missing:
        raise OddsSnapshotError(f"snapshot_missing:{','.join(sorted(missing))}")
    if not _DATE_RE.fullmatch(str(snapshot["targetDate"])):
        raise OddsSnapshotError("target_date_invalid")
    if not str(snapshot["venue"]).strip() or int(snapshot["raceNo"]) <= 0:
        raise OddsSnapshotError("race_identity_invalid")
    combo = normalize_trifecta(snapshot["trifecta"])
    stage = str(snapshot["stage"])
    if stage not in SNAPSHOT_STAGES:
        raise OddsSnapshotError("snapshot_stage_invalid")
    try:
        odds = float(snapshot["odds"])
    except (TypeError, ValueError) as exc:
        raise OddsSnapshotError("odds_invalid") from exc
    if not math.isfinite(odds) or odds <= 0:
        raise OddsSnapshotError("odds_not_positive_finite")
    source_hash = str(snapshot["sourceHash"])
    if not _HASH_RE.fullmatch(source_hash):
        raise OddsSnapshotError("source_hash_invalid")
    captured = _parse_datetime(snapshot["capturedAtUtc"], "captured_at")
    deadline = _parse_datetime(snapshot["raceDeadline"], "race_deadline")
    computed_seconds = (deadline - captured).total_seconds()
    try:
        supplied_seconds = float(snapshot["secondsToDeadline"])
    except (TypeError, ValueError) as exc:
        raise OddsSnapshotError("seconds_to_deadline_invalid") from exc
    if not math.isfinite(supplied_seconds) or not math.isclose(
        supplied_seconds, computed_seconds, rel_tol=0.0, abs_tol=0.5
    ):
        raise OddsSnapshotError("seconds_to_deadline_mismatch")
    if stage in PREDICTION_INPUT_STAGES and computed_seconds <= 0:
        raise OddsSnapshotError("post_deadline_prediction_input")
    if not str(snapshot["source"]).strip():
        raise OddsSnapshotError("source_missing")
    if not str(snapshot["collectorRunId"]).strip() or not str(snapshot["lifecycleEventId"]).strip():
        raise OddsSnapshotError("provenance_id_missing")
    normalized = dict(snapshot)
    normalized.update(
        {
            "targetDate": str(snapshot["targetDate"]),
            "venue": str(snapshot["venue"]),
            "raceNo": int(snapshot["raceNo"]),
            "trifecta": combo,
            "odds": odds,
            "stage": stage,
            "secondsToDeadline": computed_seconds,
            "predictionInputAllowed": stage in PREDICTION_INPUT_STAGES,
            "researchOnly": True,
            "productionAdoptionAllowed": False,
        }
    )
    return normalized


def _identity(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    return (
        snapshot["targetDate"],
        snapshot["venue"],
        int(snapshot["raceNo"]),
        snapshot["trifecta"],
        snapshot["stage"],
    )


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_snapshot_store(path: Path) -> None:
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                previous_hash TEXT,
                record_hash TEXT NOT NULL UNIQUE
            );
            CREATE TRIGGER IF NOT EXISTS odds_snapshots_no_update
            BEFORE UPDATE ON odds_snapshots BEGIN
                SELECT RAISE(ABORT, 'market_snapshot_append_only');
            END;
            CREATE TRIGGER IF NOT EXISTS odds_snapshots_no_delete
            BEFORE DELETE ON odds_snapshots BEGIN
                SELECT RAISE(ABORT, 'market_snapshot_append_only');
            END;
            """
        )


def append_snapshot(path: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_snapshot(snapshot)
    initialize_snapshot_store(path)
    identity_key = canonical_json(_identity(normalized))
    payload_json = canonical_json(normalized)
    with _connect(path) as connection:
        existing = connection.execute(
            "SELECT payload_json, record_hash FROM odds_snapshots WHERE identity_key = ?",
            (identity_key,),
        ).fetchone()
        if existing:
            if existing[0] != payload_json:
                raise OddsSnapshotError("snapshot_identity_conflict")
            return {"status": "IDEMPOTENT", "recordHash": str(existing[1])}
        previous = connection.execute(
            "SELECT record_hash FROM odds_snapshots ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous[0]) if previous else None
        record_hash = sha256_json({"payload": normalized, "previousHash": previous_hash})
        connection.execute(
            "INSERT INTO odds_snapshots(identity_key, payload_json, previous_hash, record_hash) VALUES (?, ?, ?, ?)",
            (identity_key, payload_json, previous_hash, record_hash),
        )
        return {"status": "CREATED", "recordHash": record_hash}


def read_snapshots(path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT sequence, payload_json, previous_hash, record_hash FROM odds_snapshots ORDER BY sequence"
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


def verify_snapshot_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"valid": True, "snapshotCount": 0, "tailHash": None}
    rows = read_snapshots(path)
    previous_hash: str | None = None
    identities: set[tuple[Any, ...]] = set()
    for expected_sequence, row in enumerate(rows, start=1):
        payload = validate_snapshot(row["payload"])
        if row["sequence"] != expected_sequence or row["previousHash"] != previous_hash:
            raise OddsSnapshotError("snapshot_chain_invalid")
        if _identity(payload) in identities:
            raise OddsSnapshotError("snapshot_duplicate_identity")
        identities.add(_identity(payload))
        expected_hash = sha256_json({"payload": payload, "previousHash": previous_hash})
        if expected_hash != row["recordHash"]:
            raise OddsSnapshotError("snapshot_record_hash_invalid")
        previous_hash = row["recordHash"]
    return {"valid": True, "snapshotCount": len(rows), "tailHash": previous_hash}


def _odds_band(odds: float) -> str:
    if odds < 2:
        return "<2"
    if odds < 5:
        return "2-5"
    if odds < 10:
        return "5-10"
    if odds < 30:
        return "10-30"
    if odds < 100:
        return "30-100"
    return "100+"


def _time_band(seconds: float) -> str:
    if seconds < 0:
        return "post_deadline"
    if seconds < 120:
        return "0-2m"
    if seconds < 300:
        return "2-5m"
    if seconds < 600:
        return "5-10m"
    return "10m+"


def evaluate_odds_movement(snapshots: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [validate_snapshot(item) for item in snapshots]
    indexed: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    decision_by_race: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        race_key = (row["targetDate"], row["venue"], row["raceNo"])
        indexed.setdefault(race_key + (row["trifecta"],), {})[row["stage"]] = row
        if row["stage"] == "DECISION_TIME":
            decision_by_race.setdefault(race_key, []).append(row)
    ranks: dict[tuple[Any, ...], dict[str, int]] = {}
    for race_key, decisions in decision_by_race.items():
        ranks[race_key] = {
            row["trifecta"]: rank
            for rank, row in enumerate(sorted(decisions, key=lambda item: item["odds"]), start=1)
        }
    pairs: list[dict[str, Any]] = []
    for identity, stages in indexed.items():
        decision = stages.get("DECISION_TIME")
        closing = stages.get("CLOSING_TIME")
        if not decision or not closing:
            continue
        ratio = float(closing["odds"]) / float(decision["odds"])
        movement = ratio - 1.0
        race_key = identity[:3]
        pairs.append(
            {
                "targetDate": identity[0],
                "venue": identity[1],
                "raceNo": identity[2],
                "trifecta": identity[3],
                "decisionOdds": decision["odds"],
                "closingOdds": closing["odds"],
                "finalDecisionRatio": ratio,
                "movementRate": movement,
                "oddsBand": _odds_band(float(decision["odds"])),
                "timeToCloseBand": _time_band(float(decision["secondsToDeadline"])),
                "decisionOddsRank": ranks.get(race_key, {}).get(identity[3]),
            }
        )
    def summarize(field: str) -> list[dict[str, Any]]:
        grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for pair in pairs:
            grouped[pair.get(field)].append(pair)
        output: list[dict[str, Any]] = []
        for value, selected in sorted(grouped.items(), key=lambda item: str(item[0])):
            movements = sorted(float(item["movementRate"]) for item in selected)
            middle = len(movements) // 2
            median = (
                movements[middle]
                if len(movements) % 2
                else (movements[middle - 1] + movements[middle]) / 2.0
            )
            output.append(
                {
                    field: value,
                    "pairCount": len(selected),
                    "meanMovementRate": sum(movements) / len(movements),
                    "medianMovementRate": median,
                    "meanFinalDecisionRatio": sum(
                        float(item["finalDecisionRatio"]) for item in selected
                    )
                    / len(selected),
                }
            )
        return output

    return {
        "status": "OK" if pairs else "INSUFFICIENT_SNAPSHOTS",
        "pairCount": len(pairs),
        "pairs": pairs,
        "byVenue": summarize("venue"),
        "byOddsBand": summarize("oddsBand"),
        "byTimeToCloseBand": summarize("timeToCloseBand"),
        "byOddsRank": summarize("decisionOddsRank"),
    }


def compute_ev_band_metrics(
    rows: Iterable[dict[str, Any]], *, require_payout_unit_verified: bool = True
) -> dict[str, Any]:
    """Compute research-only EV bands; never feeds production BUY logic."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if require_payout_unit_verified and row.get("payoutUnitVerified") is not True:
            return {"status": "BLOCKED_PAYOUT_UNIT_UNVERIFIED", "bands": {}}
        try:
            probability = float(row["modelProbability"])
            decision_odds = float(row["decisionOdds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OddsSnapshotError("ev_input_missing") from exc
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise OddsSnapshotError("model_probability_invalid")
        if not math.isfinite(decision_odds) or decision_odds <= 0:
            raise OddsSnapshotError("decision_odds_invalid")
        if "realizedReturn" in row:
            try:
                realized_return = float(row["realizedReturn"])
            except (TypeError, ValueError) as exc:
                raise OddsSnapshotError("realized_return_invalid") from exc
            if not math.isfinite(realized_return) or realized_return < 0:
                raise OddsSnapshotError("realized_return_invalid")
        if "stake" in row:
            try:
                stake = float(row["stake"])
            except (TypeError, ValueError) as exc:
                raise OddsSnapshotError("stake_invalid") from exc
            if not math.isfinite(stake) or stake <= 0:
                raise OddsSnapshotError("stake_invalid")
        item = dict(row)
        item["rawEV"] = probability * decision_odds - 1.0
        if item["rawEV"] < 0:
            band = "NEGATIVE"
        elif item["rawEV"] < 0.05:
            band = "0-5%"
        elif item["rawEV"] < 0.10:
            band = "5-10%"
        elif item["rawEV"] < 0.20:
            band = "10-20%"
        else:
            band = "20%+"
        item["predictedEVBand"] = band
        normalized.append(item)
    def _roi(selected: list[dict[str, Any]]) -> float | None:
        if not selected:
            return None
        total_stake = sum(float(item["stake"]) for item in selected)
        total_return = sum(float(item["realizedReturn"]) for item in selected)
        return (total_return - total_stake) / total_stake if total_stake else None

    settled_rows = [
        item for item in normalized if "realizedReturn" in item and "stake" in item
    ]
    top_payout_excluded = {}
    for excluded_count in (1, 3, 5):
        ranked = sorted(
            settled_rows,
            key=lambda item: float(item["realizedReturn"]),
            reverse=True,
        )
        top_payout_excluded[str(excluded_count)] = {
            "settledCount": max(0, len(settled_rows) - min(excluded_count, len(settled_rows))),
            "roi": _roi(ranked[excluded_count:]),
        }

    bands: dict[str, dict[str, Any]] = {}
    for band in ("NEGATIVE", "0-5%", "5-10%", "10-20%", "20%+"):
        selected = [item for item in normalized if item["predictedEVBand"] == band]
        settled = [item for item in selected if "realizedReturn" in item and "stake" in item]
        total_stake = sum(float(item["stake"]) for item in settled)
        total_return = sum(float(item["realizedReturn"]) for item in settled)
        bands[band] = {
            "count": len(selected),
            "settledCount": len(settled),
            "roi": ((total_return - total_stake) / total_stake) if total_stake else None,
            "totalStake": total_stake if settled else None,
            "totalReturn": total_return if settled else None,
        }
    return {
        "status": "OK",
        "rowCount": len(normalized),
        "settledCount": len(settled_rows),
        "overallRoi": _roi(settled_rows),
        "topPayoutExcluded": top_payout_excluded,
        "bands": bands,
        "rows": normalized,
    }
