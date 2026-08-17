from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from .collector import (
    FORBIDDEN,
    GROUP_FIELDS,
    JCD,
    LIVE_GROUP_FIELDS,
    LIVE_SCHEMA_SHA256,
    RACE_DATE,
    SCHEMA_SHA256,
)
from .store import FeatureStore, stable_hash


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(token in str(key).lower() for token in FORBIDDEN)
            or _contains_forbidden(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        return any(token in value.lower() for token in FORBIDDEN)
    return False


def _source_location_allowed(location: object, prefixes: Iterable[object]) -> bool:
    try:
        actual = urlsplit(str(location))
        actual_key = (actual.scheme, actual.hostname, actual.port)
    except (TypeError, ValueError):
        return False
    for prefix in prefixes:
        try:
            expected = urlsplit(str(prefix))
            expected_key = (expected.scheme, expected.hostname, expected.port)
        except (TypeError, ValueError):
            continue
        if actual_key != expected_key:
            continue
        base = expected.path.rstrip("/")
        if actual.path == base or actual.path.startswith(base + "/"):
            return True
    return False


def _feature_values_valid(group: str, payload: object) -> bool:
    if not isinstance(payload, dict):
        return False

    def finite(name: str, low: float, high: float, *, integer: bool = False) -> bool:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        return (not integer or isinstance(value, int)) and low <= value <= high

    if group == "A":
        return (
            finite("courseEntry", 1, 6, integer=True)
            and finite("startExhibition", -1, 1)
            and finite("exhibitionTime", 5, 10)
            and finite("tilt", -1, 3)
            and finite("bodyWeight", 30, 100)
        )
    if group == "B":
        return (
            isinstance(payload.get("weather"), str)
            and 0 < len(payload["weather"]) <= 32
            and isinstance(payload.get("windDirection"), str)
            and 0 < len(payload["windDirection"]) <= 16
            and finite("airTemp", -20, 60)
            and finite("waterTemp", -5, 45)
            and finite("windSpeed", 0, 60)
            and finite("waveHeight", 0, 500)
        )
    if group == "course_and_start_exhibition":
        return (
            finite("courseEntry", 1, 6, integer=True)
            and finite("startExhibition", -1, 1)
            and finite("tilt", -1, 3)
            and finite("bodyWeight", 30, 100)
        )
    if group == "exhibition_time":
        return finite("exhibitionTime", 5, 10)
    if group == "weather_and_water":
        return (
            isinstance(payload.get("weather"), str)
            and 0 < len(payload["weather"]) <= 32
            and isinstance(payload.get("windDirection"), str)
            and 0 < len(payload["windDirection"]) <= 16
            and finite("airTemp", -20, 60)
            and finite("waterTemp", -5, 45)
            and finite("windSpeed", 0, 60)
            and finite("waveHeight", 0, 500)
        )
    return (
        finite("racerRecentStarts", 0, 10000, integer=True)
        and finite("racerRecentAvgSt", -1, 1)
        and finite("motorRecentRate", 0, 1)
        and finite("boatRecentRate", 0, 1)
        and finite("sampleCount", 0, 10000, integer=True)
    )


def verify_feature_store_integrity_read_only(store_root: Path) -> dict[str, Any]:
    """Verify an existing append-only feature store without creating it."""
    store_root = Path(store_root)
    database = store_root / "feature_forward.sqlite3"
    if not database.is_file():
        return {
            "valid": True,
            "checked": False,
            "recordCount": 0,
            "tailHash": "0" * 64,
        }
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        snapshots = connection.execute("SELECT * FROM snapshots ORDER BY snapshot_id").fetchall()
        feature_records = connection.execute("SELECT * FROM feature_records ORDER BY id").fetchall()
        chain = connection.execute("SELECT * FROM ledger_chain ORDER BY sequence").fetchall()
    except sqlite3.Error as exc:
        return {"valid": False, "checked": True, "reason": f"sqlite:{type(exc).__name__}"}
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    source_hashes: dict[tuple[str, str], str] = {}
    try:
        for row in snapshots:
            expected = stable_hash({key: row[key] for key in FeatureStore.F})
            if row["record_hash"] != expected:
                return {"valid": False, "checked": True, "reason": "snapshot_payload"}
            raw = store_root / "raw" / row["race_date"] / row["jcd"] / str(row["race_no"])
            raw = raw / f"{row['snapshot_id']}.json"
            if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest() != row["raw_sha256"]:
                return {"valid": False, "checked": True, "reason": "raw_payload"}
            source_hashes[("snapshot", row["snapshot_id"])] = row["record_hash"]
        for row in feature_records:
            payload = json.loads(row["payload_json"])
            expected = stable_hash(
                {
                    "boat_no": row["boat_no"],
                    "feature_group": row["feature_group"],
                    "payload": payload,
                    "parse_status": row["parse_status"],
                    "missing_reason": row["missing_reason"],
                }
            )
            if row["record_hash"] != expected:
                return {"valid": False, "checked": True, "reason": "feature_payload"}
            source_hashes[("feature_record", row["id"])] = row["record_hash"]

        previous = "0" * 64
        seen: set[tuple[str, str]] = set()
        for sequence, row in enumerate(chain, start=1):
            key = (str(row["record_type"]), str(row["record_id"]))
            if row["sequence"] != sequence or row["previous_hash"] != previous or key in seen:
                return {"valid": False, "checked": True, "reason": "ledger_sequence"}
            payload_hash = source_hashes.get(key)
            expected = stable_hash(
                {
                    "type": key[0],
                    "id": key[1],
                    "payloadHash": payload_hash,
                    "previousHash": previous,
                }
            )
            if payload_hash is None or row["record_hash"] != expected:
                return {"valid": False, "checked": True, "reason": "ledger_hash"}
            seen.add(key)
            previous = row["record_hash"]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError):
        return {"valid": False, "checked": True, "reason": "store_payload_unreadable"}
    if len(chain) != len(source_hashes):
        return {"valid": False, "checked": True, "reason": "ledger_count"}
    return {"valid": True, "checked": True, "recordCount": len(chain), "tailHash": previous}


def verify_lifecycle_ledger_integrity_read_only(store_root: Path) -> dict[str, Any]:
    """Verify the lifecycle chain without creating a database or changing it."""
    store_root = Path(store_root)
    database = store_root / "race_lifecycle.sqlite3"
    feature_database = store_root / "feature_forward.sqlite3"
    if not database.is_file():
        return {
            "valid": True,
            "checked": False,
            "recordCount": 0,
            "tailHash": "0" * 64,
            "reason": "lifecycle_ledger_missing" if feature_database.is_file() else None,
        }
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM race_lifecycle_events ORDER BY ledger_sequence"
        ).fetchall()
    except sqlite3.Error as exc:
        return {
            "valid": False,
            "checked": True,
            "recordCount": 0,
            "tailHash": None,
            "reason": f"sqlite:{type(exc).__name__}",
        }
    finally:
        if connection is not None:
            connection.close()

    previous = "0" * 64
    try:
        for sequence, row in enumerate(rows, start=1):
            base = {
                "snapshotId": row["snapshot_id"],
                "targetDate": row["target_date"],
                "venue": row["venue"],
                "raceNo": row["race_no"],
                "stage": row["stage"],
                "statusCode": row["status_code"],
                "terminal": bool(row["terminal"]),
                "collectorRunId": row["collector_run_id"],
                "taskRunId": row["task_run_id"],
                "attemptNo": row["attempt_no"],
                "sourcePolicyHash": row["source_policy_hash"],
                "configHash": row["config_hash"],
                "codeCommit": row["code_commit"],
                "reasonDetail": row["reason_detail"],
                "evidenceRef": row["evidence_ref"],
            }
            dedupe_payload = {
                key: base[key]
                for key in (
                    "snapshotId",
                    "targetDate",
                    "venue",
                    "raceNo",
                    "stage",
                    "statusCode",
                    "attemptNo",
                    "sourcePolicyHash",
                    "configHash",
                    "codeCommit",
                    "reasonDetail",
                    "evidenceRef",
                )
            }
            expected_hash = stable_hash(
                {**base, "eventId": row["event_id"], "previousEventHash": previous}
            )
            if (
                row["ledger_sequence"] != sequence
                or row["previous_event_hash"] != previous
                or row["dedupe_key"] != stable_hash(dedupe_payload)
                or row["event_hash"] != expected_hash
            ):
                return {
                    "valid": False,
                    "checked": True,
                    "recordCount": len(rows),
                    "tailHash": None,
                    "reason": "lifecycle_hash_chain_invalid",
                }
            previous = row["event_hash"]
    except (KeyError, TypeError, ValueError, IndexError):
        return {
            "valid": False,
            "checked": True,
            "recordCount": len(rows),
            "tailHash": None,
            "reason": "lifecycle_payload_unreadable",
        }
    return {
        "valid": True,
        "checked": True,
        "recordCount": len(rows),
        "tailHash": previous,
        "reason": None,
    }


def _read_input_hash(path: Path) -> tuple[str | None, str | None]:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        return None, "INPUT_HASH_MISSING"
    try:
        digest = sidecar.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError):
        return None, "INPUT_HASH_UNREADABLE"
    if not HEX64.fullmatch(digest):
        return None, "INPUT_HASH_INVALID"
    return digest, None


def _existing_snapshot_reason(store_root: Path, snapshot_id: str, item: dict[str, Any]) -> str | None:
    database = Path(store_root) / "feature_forward.sqlite3"
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            if connection.execute(
                "SELECT 1 FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone():
                return "DUPLICATE_SNAPSHOT"
            if connection.execute(
                """
                SELECT 1 FROM snapshots
                WHERE race_date=? AND jcd=? AND race_no=? AND source_type=?
                """,
                (item.get("raceDate"), item.get("jcd"), item.get("raceNo"), item.get("sourceType")),
            ).fetchone():
                return "SNAPSHOT_CONFLICT"
        finally:
            connection.close()
    except sqlite3.Error:
        return "STORE_LOOKUP_FAILED"
    return None


def _validate_payload(
    *,
    raw: bytes,
    item: Any,
    approval: dict[str, Any],
    store_root: Path,
    now: datetime,
) -> tuple[str | None, list[str]]:
    raw_hash = hashlib.sha256(raw).hexdigest()
    if not isinstance(item, dict):
        return None, ["SCHEMA_MISMATCH"]
    schema_version = item.get("schemaVersion")
    if schema_version not in {1, 2}:
        return None, ["SCHEMA_DRIFT"]
    group_fields = GROUP_FIELDS if schema_version == 1 else LIVE_GROUP_FIELDS
    reasons: list[str] = []
    try:
        parsed_race_date = (
            date.fromisoformat(str(item.get("raceDate")))
            if isinstance(item.get("raceDate"), str) and RACE_DATE.fullmatch(item["raceDate"])
            else None
        )
    except ValueError:
        parsed_race_date = None
    jcd = item.get("jcd")
    race_no = item.get("raceNo")
    identity_valid = (
        parsed_race_date is not None
        and isinstance(jcd, str)
        and bool(JCD.fullmatch(jcd))
        and 1 <= int(jcd) <= 24
        and type(race_no) is int
        and 1 <= race_no <= 12
    )
    if not identity_valid:
        reasons.append("RACE_IDENTITY_INVALID")
    if item.get("sourceType") not in approval.get("allowedSourceTypes", []):
        reasons.append("SOURCE_NOT_APPROVED")
    if not _source_location_allowed(
        item.get("sourceLocation"), approval.get("allowedSourceLocationPrefixes", [])
    ):
        reasons.append("SOURCE_LOCATION_NOT_APPROVED")
    if _contains_forbidden(item):
        reasons.append("RESULT_LEAKAGE")
    boats = item.get("boats")
    boat_numbers = [boat.get("boatNo") for boat in boats] if isinstance(boats, list) and all(
        isinstance(boat, dict) for boat in boats
    ) else []
    if (
        not isinstance(boats, list)
        or len(boats) != 6
        or any(type(number) is not int for number in boat_numbers)
        or sorted(boat_numbers) != list(range(1, 7))
    ):
        reasons.append("SCHEMA_MISMATCH")
    elif all(isinstance(boat, dict) for boat in boats):
        for boat in boats:
            groups = boat.get("groups")
            if not isinstance(groups, dict) or set(groups) != set(group_fields):
                reasons.append("SCHEMA_MISMATCH")
                continue
            for group, fields in group_fields.items():
                payload = groups.get(group)
                if not isinstance(payload, dict) or set(payload) != set(fields):
                    reasons.append("SCHEMA_MISMATCH")
                elif not _feature_values_valid(group, payload):
                    reasons.append("FEATURE_VALUE_INVALID")
    try:
        utc = datetime.fromisoformat(item["fetchedAtUtc"])
        jst = datetime.fromisoformat(item["fetchedAtJst"])
        deadline = datetime.fromisoformat(item["raceDeadlineJst"])
        if utc.tzinfo is None or jst.tzinfo is None or deadline.tzinfo is None:
            raise ValueError
        if (
            utc.utcoffset() != timedelta(0)
            or jst.utcoffset() != timedelta(hours=9)
            or deadline.utcoffset() != timedelta(hours=9)
        ):
            raise ValueError
        declared_drift = float(item.get("clockDriftSeconds"))
        if (
            not math.isfinite(declared_drift)
            or declared_drift < 0
            or declared_drift > 5
            or abs((utc - jst).total_seconds()) > 5
        ):
            reasons.append("CLOCK_DRIFT")
        age = (now.astimezone(timezone.utc) - utc.astimezone(timezone.utc)).total_seconds()
        if age < -5:
            reasons.append("CAPTURE_TIME_UNVERIFIED")
        if parsed_race_date is None or deadline.astimezone(jst.tzinfo).date() != parsed_race_date:
            reasons.append("RACE_IDENTITY_INVALID")
        if (deadline - jst).total_seconds() <= 0:
            reasons.append("POST_DEADLINE")
    except (KeyError, TypeError, ValueError):
        reasons.append("TIMESTAMP_INVALID")
    snapshot_id = stable_hash(
        {
            "rawSha256": raw_hash,
            "raceDate": item.get("raceDate"),
            "jcd": item.get("jcd"),
            "raceNo": item.get("raceNo"),
        }
    )
    if not reasons:
        duplicate_reason = _existing_snapshot_reason(store_root, snapshot_id, item)
        if duplicate_reason:
            reasons.append(duplicate_reason)
    return snapshot_id, sorted(set(reasons))


def preflight_manual_inbox(
    *,
    inbox: Path,
    approval: dict[str, Any],
    store_root: Path,
    now: datetime,
    paths: Iterable[Path] | None = None,
    manual_enabled: bool | None = None,
) -> dict[str, Any]:
    """Validate manually supplied inputs without moving or appending any file."""
    if now.tzinfo is None:
        raise ValueError("preflight_now_timezone_required")
    inbox = Path(inbox)
    store_root = Path(store_root)
    enabled = bool(approval.get("manualIngestAllowed")) if manual_enabled is None else manual_enabled
    selected = list(paths) if paths is not None else sorted(inbox.glob("*.json")) if inbox.is_dir() else []
    selected = sorted((Path(path) for path in selected), key=lambda path: path.name)
    integrity = verify_feature_store_integrity_read_only(store_root)
    lifecycle_integrity = verify_lifecycle_ledger_integrity_read_only(store_root)
    base = {
        "fileCount": len(selected),
        "readyFileCount": 0,
        "rejectedFileCount": 0,
        "records": [],
        "readyPaths": [],
        "storeIntegrity": integrity,
        "lifecycleIntegrity": lifecycle_integrity,
    }
    if not enabled:
        return {**base, "status": "MANUAL_INGEST_DISABLED"}
    if not selected:
        return {**base, "status": "EMPTY_MANUAL_INBOX"}
    if integrity.get("valid") is not True:
        return {
            **base,
            "status": "MANUAL_INGEST_PREFLIGHT_BLOCKED",
            "rejectedFileCount": len(selected),
            "records": [
                {"path": str(path), "snapshotId": None, "reasons": ["FEATURE_STORE_INTEGRITY_INVALID"]}
                for path in selected
            ],
        }
    if lifecycle_integrity.get("valid") is not True:
        return {
            **base,
            "status": "MANUAL_INGEST_PREFLIGHT_BLOCKED",
            "rejectedFileCount": len(selected),
            "records": [
                {"path": str(path), "snapshotId": None, "reasons": ["LIFECYCLE_INTEGRITY_INVALID"]}
                for path in selected
            ],
        }

    records: list[dict[str, Any]] = []
    race_identities: dict[str, tuple[object, object, object, object]] = {}
    for path in selected:
        reasons: list[str] = []
        snapshot_id: str | None = None
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
            reasons.append("INPUT_UNREADABLE")
        expected_hash, hash_error = _read_input_hash(path)
        if hash_error:
            reasons.append(hash_error)
        elif expected_hash != hashlib.sha256(raw).hexdigest():
            reasons.append("INPUT_HASH_MISMATCH")
        if not reasons:
            try:
                payload = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError):
                payload = None
                reasons.append("INVALID_JSON")
            if not reasons:
                snapshot_id, payload_reasons = _validate_payload(
                    raw=raw,
                    item=payload,
                    approval=approval,
                    store_root=store_root,
                    now=now,
                )
                reasons.extend(payload_reasons)
                if not reasons and isinstance(payload, dict):
                    race_identities[str(path)] = (
                        payload.get("raceDate"),
                        payload.get("jcd"),
                        payload.get("raceNo"),
                        payload.get("sourceType"),
                    )
        record = {
            "path": str(path),
            "snapshotId": snapshot_id,
            "rawPayloadSha256": hashlib.sha256(raw).hexdigest() if raw else None,
            "reasons": sorted(set(reasons)),
        }
        records.append(record)
    seen_snapshots: set[str] = set()
    seen_races: set[tuple[object, object, object, object]] = set()
    for record in records:
        if record["reasons"] or not isinstance(record["snapshotId"], str):
            continue
        snapshot_id = record["snapshotId"]
        race_identity = race_identities.get(record["path"])
        if snapshot_id in seen_snapshots:
            record["reasons"].append("DUPLICATE_INBOX_SNAPSHOT")
            continue
        if race_identity is not None and race_identity in seen_races:
            record["reasons"].append("INBOX_SNAPSHOT_CONFLICT")
            continue
        seen_snapshots.add(snapshot_id)
        if race_identity is not None:
            seen_races.add(race_identity)
    for record in records:
        record["reasons"] = sorted(set(record["reasons"]))
    ready_paths = [
        path for path, record in zip(selected, records) if not record["reasons"]
    ]
    return {
        **base,
        "status": "MANUAL_INGEST_PREFLIGHT_READY" if len(ready_paths) == len(selected) else "MANUAL_INGEST_PREFLIGHT_BLOCKED",
        "readyFileCount": len(ready_paths),
        "rejectedFileCount": len(selected) - len(ready_paths),
        "records": records,
        "readyPaths": ready_paths,
    }
