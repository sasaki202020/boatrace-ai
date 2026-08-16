from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.commercialization_v2.day1_readiness import validate_runtime_bfile

from .lifecycle_ledger import LifecycleConflictError, LifecycleLedger
from .source_policy import PolicyGateError, load_policy_manifest

JST = ZoneInfo("Asia/Tokyo")
HEX64 = set("0123456789abcdef")
SETTLEMENT_GRACE_MINUTES = 30


class RuntimeGateError(RuntimeError):
    """Raised before network or runtime-store work when attestation fails."""


@dataclass(frozen=True)
class RuntimeGateContext:
    root: Path
    policy_path: Path
    policy_hash: str
    policy_version: int
    config_path: Path
    config_hash: str
    code_commit: str
    settlement_grace_minutes: int
    requests_per_day: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourcePolicyStatus": "ENFORCED",
            "policyPath": str(self.policy_path.resolve()),
            "policyHash": self.policy_hash,
            "policyVersion": self.policy_version,
            "configPath": str(self.config_path.resolve()),
            "configHash": self.config_hash,
            "codeCommit": self.code_commit,
            "settlementGraceMinutes": self.settlement_grace_minutes,
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeGateError("code_commit_unavailable") from exc
    value = result.stdout.strip()
    if len(value) != 40 or any(char not in HEX64 for char in value.lower()):
        raise RuntimeGateError("code_commit_invalid")
    return value


def _resolve(root: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RuntimeGateError(f"{field}_missing")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def load_runtime_gate(
    root: Path,
    *,
    gate_config_path: Path | None = None,
    policy_path_override: Path | None = None,
) -> RuntimeGateContext:
    root = Path(root).resolve()
    gate_path = gate_config_path or root / "config/feature_forward_v1/runtime_gate.json"
    try:
        gate = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeGateError("runtime_gate_config_invalid") from exc
    if not isinstance(gate, dict) or gate.get("schemaVersion") != 1:
        raise RuntimeGateError("runtime_gate_schema_unsupported")

    policy_path = _resolve(root, gate.get("policyPath"), "policy_path")
    if policy_path_override is not None:
        override = Path(policy_path_override).resolve()
        if override != policy_path:
            raise RuntimeGateError("policy_path_config_mismatch")
    config_path = _resolve(root, gate.get("configPath"), "config_path")
    expected_policy = gate.get("expectedPolicySha256")
    expected_config = gate.get("expectedConfigSha256")
    if (
        not isinstance(expected_policy, str)
        or len(expected_policy) != 64
        or any(char not in HEX64 for char in expected_policy)
        or not isinstance(expected_config, str)
        or len(expected_config) != 64
        or any(char not in HEX64 for char in expected_config)
    ):
        raise RuntimeGateError("runtime_gate_hash_pin_invalid")

    try:
        policy, metadata = load_policy_manifest(
            policy_path,
            require_automated_fetch=True,
        )
    except PolicyGateError as exc:
        raise RuntimeGateError(f"source_policy_{exc}") from exc
    if metadata["policyHash"] != expected_policy:
        raise RuntimeGateError("source_policy_hash_mismatch")
    if not config_path.is_file() or _sha256(config_path) != expected_config:
        raise RuntimeGateError("feature_config_hash_mismatch")
    try:
        grace = int(gate.get("settlementGraceMinutes", SETTLEMENT_GRACE_MINUTES))
    except (TypeError, ValueError) as exc:
        raise RuntimeGateError("settlement_grace_invalid") from exc
    if grace < 0:
        raise RuntimeGateError("settlement_grace_invalid")
    return RuntimeGateContext(
        root=root,
        policy_path=policy_path,
        policy_hash=str(metadata["policyHash"]),
        policy_version=int(metadata["policyVersion"]),
        config_path=config_path,
        config_hash=expected_config,
        code_commit=_git_commit(root),
        settlement_grace_minutes=grace,
        requests_per_day=policy.requests_per_day,
    )


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"


def write_append_only_json(path: Path, payload: dict[str, Any]) -> bool:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != raw:
            raise RuntimeGateError(f"manifest_conflict:{path.name}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(raw)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != raw:
            raise RuntimeGateError(f"manifest_conflict:{path.name}") from None
        return False
    return True


def _read_rows(database: Path, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not database.is_file():
        return []
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params)]
    finally:
        connection.close()


def _schedule_rows(b_file: Path) -> list[dict[str, Any]]:
    entries = validate_runtime_bfile(b_file)
    rows = entries[["date", "jcd", "race_no", "deadline"]].drop_duplicates()
    return [
        {
            "raceDate": str(row.date),
            "venue": str(row.jcd).zfill(2),
            "raceNo": int(row.race_no),
            "deadlineJst": datetime.fromisoformat(
                f"{row.date}T{row.deadline}:00"
            ).replace(tzinfo=JST),
        }
        for row in rows.itertuples(index=False)
    ]


def _race_id(race: dict[str, Any]) -> str:
    return (
        f"{race['raceDate'].replace('-', '')}-{race['venue']}-{int(race['raceNo']):02d}"
    )


def _selected_venues(request_database: Path) -> dict[str, set[str]]:
    rows = _read_rows(request_database, "SELECT key,value FROM state")
    selected: dict[str, set[str]] = {}
    for row in rows:
        key = str(row["key"])
        value = str(row["value"])
        if key.startswith("venues:"):
            selected.setdefault(key.removeprefix("venues:"), set()).update(
                item for item in value.split(",") if item
            )
        elif key.startswith("venue:"):
            selected.setdefault(key.removeprefix("venue:"), set()).add(value)
    return selected


def _status_from_evidence(
    *,
    race: dict[str, Any],
    snapshot: dict[str, Any] | None,
    request: dict[str, Any] | None,
    now_jst: datetime,
) -> str:
    if snapshot is not None:
        reasons = set(json.loads(snapshot.get("reasons_json") or "[]"))
        if snapshot.get("status") == "CAPTURED" and bool(snapshot.get("research_eligible")):
            return "VALID_CAPTURE"
        if reasons & {"FEATURE_VALUE_INVALID", "SCHEMA_MISMATCH", "SCHEMA_DRIFT"}:
            return "PARSE_FAILURE"
        return "PENDING_VALIDATION"
    if request is not None:
        outcome = str(request.get("outcome") or "").upper()
        status_code = int(request.get("status_code") or 0)
        if status_code == 429 or "429" in outcome:
            return "RATE_LIMIT"
        if status_code == 0 or any(token in outcome for token in ("TIMEOUT", "NETWORK", "ERROR")):
            return "NETWORK_ERROR"
        if status_code != 200 or "CAPTCHA" in outcome or "REDIRECT" in outcome:
            return "SOURCE_UNAVAILABLE"
        return "PENDING_VALIDATION"
    if race["deadlineJst"] <= now_jst:
        return "DEADLINE_PASSED"
    return "PENDING_CAPTURE"


def _validation_status(capture_status: str) -> str:
    if capture_status == "PENDING_CAPTURE":
        return "PENDING_VALIDATION"
    return capture_status


def append_capture_lifecycle(
    *,
    b_file: Path,
    store_root: Path,
    gate: RuntimeGateContext,
    collector_run_id: str,
    task_run_id: str,
    now_utc: datetime,
) -> dict[str, Any]:
    now_jst = now_utc.astimezone(JST)
    store_root = Path(store_root)
    request_database = store_root / "request_ledger.sqlite3"
    feature_database = store_root / "feature_forward.sqlite3"
    requests = {
        str(row["race_key"]): row
        for row in _read_rows(request_database, "SELECT * FROM requests")
    }
    snapshots = {
        (
            str(row["race_date"]),
            str(row["jcd"]).zfill(2),
            int(row["race_no"]),
        ): row
        for row in _read_rows(feature_database, "SELECT * FROM snapshots")
    }
    selected = _selected_venues(request_database)
    ledger = LifecycleLedger(store_root / "race_lifecycle.sqlite3")
    created = 0
    statuses: dict[str, int] = {}
    try:
        for race in _schedule_rows(b_file):
            race_id = _race_id(race)
            is_selected = race["venue"] in selected.get(race["raceDate"], set())
            selection_status = "SELECTED" if is_selected else "NOT_SELECTED_BY_DAILY_CAP"
            selection = ledger.append_event(
                snapshot_id=None,
                target_date=race["raceDate"],
                venue=race["venue"],
                race_no=race["raceNo"],
                stage="SELECTION",
                status_code=selection_status,
                occurred_at_utc=now_utc.isoformat(),
                collector_run_id=collector_run_id,
                task_run_id=task_run_id,
                attempt_no=0,
                source_policy_hash=gate.policy_hash,
                config_hash=gate.config_hash,
                code_commit=gate.code_commit,
                reason_detail=("selected_by_daily_cap" if is_selected else "outside_selected_scope"),
                evidence_ref=f"schedule:{race_id}",
            )
            created += int(selection.created)
            statuses[selection_status] = statuses.get(selection_status, 0) + 1
            if not is_selected:
                continue
            snapshot = snapshots.get((race["raceDate"], race["venue"], race["raceNo"]))
            request = requests.get(race_id)
            capture_status = _status_from_evidence(
                race=race,
                snapshot=snapshot,
                request=request,
                now_jst=now_jst,
            )
            evidence_ref = (
                f"snapshot:{snapshot['snapshot_id']}"
                if snapshot is not None
                else f"request:{race_id}"
                if request is not None
                else f"schedule:{race_id}"
            )
            attempt_no = 1 if request is not None else 0
            for stage, status_code in (
                ("CAPTURE", capture_status),
                ("VALIDATION", _validation_status(capture_status)),
            ):
                event = ledger.append_event(
                    snapshot_id=snapshot.get("snapshot_id") if snapshot else None,
                    target_date=race["raceDate"],
                    venue=race["venue"],
                    race_no=race["raceNo"],
                    stage=stage,
                    status_code=status_code,
                    occurred_at_utc=now_utc.isoformat(),
                    collector_run_id=collector_run_id,
                    task_run_id=task_run_id,
                    attempt_no=attempt_no,
                    source_policy_hash=gate.policy_hash,
                    config_hash=gate.config_hash,
                    code_commit=gate.code_commit,
                    reason_detail=status_code.lower(),
                    evidence_ref=evidence_ref,
                )
                created += int(event.created)
                statuses[status_code] = statuses.get(status_code, 0) + 1
        integrity = ledger.verify_integrity()
    finally:
        ledger.close()
    unknown = sum(count for status, count in statuses.items() if status == "UNKNOWN_LEGACY")
    return {
        "createdEvents": created,
        "statusCounts": dict(sorted(statuses.items())),
        "newUnknownCount": unknown,
        "terminalStatusConflictCount": 0,
        "timeOrderViolationCount": 0,
        "integrity": integrity,
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _settlement_valid(payload: dict[str, Any], prediction: dict[str, Any]) -> bool:
    saved = payload.get("settlementSha256")
    unsigned = {key: value for key, value in payload.items() if key != "settlementSha256"}
    return (
        isinstance(saved, str)
        and hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == saved
        and payload.get("predictionSha256") == prediction.get("predictionSha256")
    )


def append_settlement_lifecycle(
    *,
    store_root: Path,
    prediction_root: Path,
    settlement_root: Path,
    result_root: Path,
    gate: RuntimeGateContext,
    collector_run_id: str,
    task_run_id: str,
    now_utc: datetime,
    result_not_due_dates: set[str] | None = None,
) -> dict[str, Any]:
    now_jst = now_utc.astimezone(JST)
    result_not_due_dates = {str(value) for value in (result_not_due_dates or set())}
    lifecycle_database = Path(store_root) / "race_lifecycle.sqlite3"
    selected_rows = _read_rows(
        lifecycle_database,
        "SELECT target_date,venue,race_no FROM race_lifecycle_events "
        "WHERE stage='SELECTION' AND status_code='SELECTED'",
    )
    predictions: dict[str, dict[str, Any]] = {}
    if Path(prediction_root).is_dir():
        for path in Path(prediction_root).glob("*/*.json"):
            payload = _load_json(path)
            if payload and payload.get("raceId"):
                predictions[str(payload["raceId"])] = payload
    settlements: dict[str, dict[str, Any]] = {}
    if Path(settlement_root).is_dir():
        for path in Path(settlement_root).glob("*/*.json"):
            payload = _load_json(path)
            if payload and payload.get("raceId"):
                settlements[str(payload["raceId"])] = payload

    ledger = LifecycleLedger(lifecycle_database)
    created = 0
    status_counts: dict[str, int] = {}
    overdue = 0
    mature_total = 0
    mature_settled = 0
    conflicts = 0
    try:
        for row in sorted(selected_rows, key=lambda item: (item["target_date"], item["venue"], item["race_no"])):
            target_date = str(row["target_date"])
            result_not_due = target_date in result_not_due_dates
            race_id = f"{target_date.replace('-', '')}-{str(row['venue']).zfill(2)}-{int(row['race_no']):02d}"
            prediction = predictions.get(race_id)
            if prediction is None:
                status = "KEY_MISMATCH"
                reason = "prediction_missing_for_selected_race"
                evidence = f"schedule:{race_id}"
                terminal = True
                deadline = None
            else:
                deadline = datetime.fromisoformat(str(prediction["deadlineJst"]))
                settlement = settlements.get(race_id)
                k_file = Path(result_root) / f"K{race_id[:8][2:]}.TXT"
                if settlement is not None:
                    if not _settlement_valid(settlement, prediction):
                        status = "DUPLICATE_OR_CONFLICT"
                        reason = "settlement_hash_or_prediction_hash_mismatch"
                        terminal = True
                    elif str(settlement.get("settlementStatus", "")).lower() == "void" or str(settlement.get("resultStatus", "")).lower() in {"not_held", "canceled", "refund", "no_contest"}:
                        status = "RACE_CANCELLED"
                        reason = "terminal_void_result"
                        terminal = True
                    else:
                        status = "SETTLED"
                        reason = "settlement_hash_and_prediction_hash_match"
                        terminal = True
                    evidence = f"settlement:{race_id}"
                elif k_file.is_file():
                    status = "RESULT_UNAVAILABLE"
                    reason = "result_source_exists_without_valid_settlement"
                    evidence = f"result-source:{k_file.name}"
                    # A result source can be present while its parser has not
                    # yet produced a complete race result; allow a later
                    # verified settlement event without rewriting this record.
                    terminal = False
                elif result_not_due:
                    status = "PENDING_NOT_DUE"
                    reason = "result_source_not_due"
                    evidence = f"schedule:{race_id}"
                    terminal = False
                elif now_jst < deadline:
                    status = "PENDING_NOT_DUE"
                    reason = "before_prediction_deadline"
                    evidence = f"schedule:{race_id}"
                    terminal = False
                elif now_jst <= deadline + timedelta(minutes=gate.settlement_grace_minutes):
                    status = "PENDING_WITHIN_GRACE"
                    reason = "result_grace_window_open"
                    evidence = f"schedule:{race_id}"
                    terminal = False
                else:
                    status = "PENDING_OVERDUE"
                    reason = "result_overdue_after_grace"
                    evidence = f"schedule:{race_id}"
                    terminal = False
                    overdue += 1
            if (
                deadline is not None
                and not result_not_due
                and now_jst > deadline + timedelta(minutes=gate.settlement_grace_minutes)
            ):
                mature_total += 1
                if status in {"SETTLED", "RACE_CANCELLED"}:
                    mature_settled += 1
            try:
                event = ledger.append_event(
                    snapshot_id=None,
                    target_date=str(row["target_date"]),
                    venue=str(row["venue"]).zfill(2),
                    race_no=int(row["race_no"]),
                    stage="SETTLEMENT",
                    status_code=status,
                    occurred_at_utc=now_utc.isoformat(),
                    collector_run_id=collector_run_id,
                    task_run_id=task_run_id,
                    attempt_no=0,
                    source_policy_hash=gate.policy_hash,
                    config_hash=gate.config_hash,
                    code_commit=gate.code_commit,
                    reason_detail=reason,
                    evidence_ref=evidence,
                    terminal=terminal,
                )
            except LifecycleConflictError:
                conflicts += 1
                raise
            created += int(event.created)
            status_counts[status] = status_counts.get(status, 0) + 1
        integrity = ledger.verify_integrity()
    finally:
        ledger.close()
    return {
        "createdEvents": created,
        "statusCounts": dict(sorted(status_counts.items())),
        "newUnknownCount": status_counts.get("UNKNOWN_LEGACY", 0),
        "terminalStatusConflictCount": conflicts,
        "overdueSettlementPendingCount": overdue,
        "matureSettlementCoverage": (
            mature_settled / mature_total if mature_total else None
        ),
        "matureSettlementEligibleRaces": mature_total,
        "matureSettlementSettledRaces": mature_settled,
        "integrity": integrity,
    }
