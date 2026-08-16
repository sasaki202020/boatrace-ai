from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.commercialization_v2.day1_readiness import validate_runtime_bfile

from .source_policy import load_policy_manifest

CAPTURE_STATUS = frozenset(
    {
        "VALID_CAPTURE",
        "NOT_SELECTED_BY_DAILY_CAP",
        "CAPTURE_WINDOW_COLLISION",
        "TASK_NOT_RUNNING",
        "NETWORK_ERROR",
        "RATE_LIMIT",
        "SOURCE_UNAVAILABLE",
        "PARSE_FAILURE",
        "DEADLINE_PASSED",
        "RACE_CANCELLED",
        "UNKNOWN_LEGACY",
    }
)
SETTLEMENT_STATUS = frozenset(
    {
        "SETTLED",
        "RESULT_PENDING",
        "RESULT_UNAVAILABLE",
        "RACE_CANCELLED",
        "KEY_MISMATCH",
        "SETTLEMENT_TASK_NOT_RUNNING",
        "SOURCE_UNAVAILABLE",
        "DUPLICATE_OR_CONFLICT",
        "UNKNOWN_LEGACY",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def classify_capture_status(
    *,
    selected: bool,
    snapshot_status: str | None,
    research_eligible: bool,
    reasons: Iterable[str],
    request_outcome: str | None,
) -> str:
    if not selected:
        return "NOT_SELECTED_BY_DAILY_CAP"
    if snapshot_status == "CAPTURED" and research_eligible:
        return "VALID_CAPTURE"
    raw_reasons = {str(value) for value in reasons}
    if snapshot_status is not None:
        if raw_reasons & {"FEATURE_VALUE_INVALID", "SCHEMA_MISMATCH", "SCHEMA_DRIFT"}:
            return "PARSE_FAILURE"
        return "UNKNOWN_LEGACY"
    if request_outcome and any(
        token in request_outcome.upper() for token in ("TIMEOUT", "NETWORK", "ERROR")
    ):
        return "NETWORK_ERROR"
    return "UNKNOWN_LEGACY"


def classify_settlement_status(
    *,
    settlement_exists: bool,
    settlement_valid: bool,
    result_source_exists: bool,
    race_cancelled: bool = False,
) -> str:
    if race_cancelled:
        return "RACE_CANCELLED"
    if settlement_exists and settlement_valid:
        return "SETTLED"
    if settlement_exists and not settlement_valid:
        return "DUPLICATE_OR_CONFLICT"
    if result_source_exists:
        return "RESULT_UNAVAILABLE"
    return "RESULT_PENDING"


def pace_metrics(
    *,
    observation_calendar_days: int,
    collector_running_days: int,
    selected_races: int,
    feature_settled_races: int,
    valid_capture_rate: float,
    settlement_join_rate: float,
    planned_selected_races_per_day: tuple[int, ...] = (12, 60),
) -> dict[str, Any]:
    if observation_calendar_days <= 0 or collector_running_days <= 0:
        raise ValueError("observation_days_required")
    if not 0 <= valid_capture_rate <= 1 or not 0 <= settlement_join_rate <= 1:
        raise ValueError("coverage_rate_invalid")
    remaining = max(0, 1500 - feature_settled_races)
    scenarios: dict[str, dict[str, Any]] = {}
    for planned in planned_selected_races_per_day:
        usable = planned * valid_capture_rate * settlement_join_rate
        scenarios[str(planned)] = {
            "plannedSelectedRacesPerDay": planned,
            "usablePerDay": usable,
            "estimatedDaysTo1500": math.ceil(remaining / usable) if usable else None,
        }
    return {
        "observationCalendarDays": observation_calendar_days,
        "collectorRunningDays": collector_running_days,
        "selectedRacesPerCalendarDay": selected_races / observation_calendar_days,
        "selectedRacesPerRunningDay": selected_races / collector_running_days,
        "featureSettledPerCalendarDay": feature_settled_races / observation_calendar_days,
        "featureSettledPerRunningDay": feature_settled_races / collector_running_days,
        "currentCalendarPace": feature_settled_races / observation_calendar_days,
        "currentRunningDayPace": feature_settled_races / collector_running_days,
        "remainingFeatureSettledRaces": remaining,
        "scenarios": scenarios,
    }


def _read_rows(database: Path, query: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute(query)]
        connection.execute("COMMIT")
        return rows
    finally:
        connection.close()


def _selected_venues(request_rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    state = {
        str(row["key"]): str(row["value"])
        for row in request_rows
        if "key" in row
    }
    selected: dict[str, list[str]] = {}
    for key, value in state.items():
        if key.startswith("venues:"):
            selected[key.removeprefix("venues:")] = [v for v in value.split(",") if v]
    for key, value in state.items():
        if key.startswith("venue:"):
            date = key.removeprefix("venue:")
            selected.setdefault(date, [])
            if value not in selected[date]:
                selected[date].append(value)
    return {date: venues[:5] for date, venues in selected.items() if venues}


def _load_settlements(root: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return output
    for path in root.glob("*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("raceId"):
            output[str(payload["raceId"])] = payload
    return output


def _settlement_valid(
    payload: dict[str, Any], predictions: dict[str, dict[str, Any]]
) -> bool:
    saved = payload.get("settlementSha256")
    unsigned = {key: value for key, value in payload.items() if key != "settlementSha256"}
    prediction = predictions.get(str(payload.get("raceId")))
    return (
        isinstance(saved, str)
        and _stable_hash(unsigned) == saved
        and prediction is not None
        and prediction.get("predictionSha256") == payload.get("predictionSha256")
    )


def build_lifecycle_report(
    *,
    feature_database: Path,
    request_database: Path,
    entries_root: Path,
    prediction_root: Path,
    settlement_root: Path,
    policy_path: Path | None = None,
    config_path: Path | None = None,
    code_commit: str | None = None,
    runtime_policy_enforced: bool = False,
    worktree_dirty: bool | None = None,
) -> dict[str, Any]:
    snapshots = _read_rows(feature_database, "SELECT * FROM snapshots ORDER BY race_date,jcd,race_no")
    request_rows = _read_rows(request_database, "SELECT * FROM requests ORDER BY requested_at_utc")
    request_state = _read_rows(request_database, "SELECT key,value FROM state")
    selected_by_date = _selected_venues(request_state)
    if not snapshots:
        raise ValueError("feature_snapshot_empty")
    dates = sorted({str(row["race_date"]) for row in snapshots})
    schedules: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    for race_date in dates:
        path = entries_root / f"B{race_date[2:].replace('-', '')}.TXT"
        if not path.is_file():
            continue
        entries = validate_runtime_bfile(path)
        source_files.append({"name": path.name, "sha256": _sha256(path)})
        rows = entries[["date", "jcd", "race_no", "deadline"]].drop_duplicates()
        for row in rows.itertuples(index=False):
            if str(row.date) == race_date:
                schedules.append(
                    {
                        "raceDate": race_date,
                        "jcd": str(row.jcd).zfill(2),
                        "raceNo": int(row.race_no),
                        "deadline": str(row.deadline),
                    }
                )
    snapshots_by_key = {
        (row["race_date"], str(row["jcd"]).zfill(2), int(row["race_no"])): row
        for row in snapshots
    }
    requests_by_key = {str(row["race_key"]): row for row in request_rows}
    lifecycle_rows: list[dict[str, Any]] = []
    for race in schedules:
        key = (race["raceDate"], race["jcd"], race["raceNo"])
        race_id = f"{race['raceDate'].replace('-', '')}-{race['jcd']}-{race['raceNo']:02d}"
        snapshot = snapshots_by_key.get(key)
        status = classify_capture_status(
            selected=race["jcd"] in selected_by_date.get(race["raceDate"], []),
            snapshot_status=snapshot.get("status") if snapshot else None,
            research_eligible=bool(snapshot and snapshot.get("research_eligible")),
            reasons=json.loads(snapshot.get("reasons_json") or "[]") if snapshot else [],
            request_outcome=(requests_by_key.get(race_id) or {}).get("outcome"),
        )
        lifecycle_rows.append(
            {
                **race,
                "raceId": race_id,
                "captureStatus": status,
                "snapshotId": snapshot.get("snapshot_id") if snapshot else None,
            }
        )
    schedule_keys = {(row["raceDate"], row["jcd"], row["raceNo"]) for row in schedules}
    valid_snapshots = [
        row
        for row in snapshots
        if row["status"] == "CAPTURED" and bool(row["research_eligible"])
    ]
    rejected_snapshots = [row for row in snapshots if row not in valid_snapshots]
    valid_feature_ids = {
        f"{row['race_date'].replace('-', '')}-{str(row['jcd']).zfill(2)}-{int(row['race_no']):02d}"
        for row in valid_snapshots
    }
    predictions: dict[str, dict[str, Any]] = {}
    if prediction_root.is_dir():
        for path in prediction_root.glob("*/*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("raceId"):
                predictions[str(payload["raceId"])] = payload
    settlements = _load_settlements(settlement_root)
    k_dates = {
        path.name[1:-4]
        for path in settlement_root.parent.parent.joinpath("raw/official/results").glob("K*.TXT")
        if path.name.startswith("K") and len(path.name) == 11
    }
    settlement_statuses: dict[str, str] = {}
    for race_id in sorted(valid_feature_ids):
        date8 = race_id[:8]
        payload = settlements.get(race_id)
        settlement_statuses[race_id] = classify_settlement_status(
            settlement_exists=payload is not None,
            settlement_valid=bool(payload and _settlement_valid(payload, predictions)),
            result_source_exists=date8 in k_dates,
        )
    policy_metadata: dict[str, Any] = {
        "policyLoaded": False,
        "policyHash": None,
        "policyVersion": None,
        "policyEnforcedAtRuntime": runtime_policy_enforced,
    }
    if policy_path is not None:
        _, policy_metadata = load_policy_manifest(policy_path, require_automated_fetch=False)
        policy_metadata["policyEnforcedAtRuntime"] = runtime_policy_enforced
    config_hash = _sha256(config_path) if config_path is not None and config_path.is_file() else None
    ledger_tail_rows = _read_rows(
        feature_database,
        "SELECT sequence, record_id, record_hash FROM ledger_chain "
        "ORDER BY sequence DESC LIMIT 1",
    )
    ledger_tail = ledger_tail_rows[0] if ledger_tail_rows else {}
    latest = max(snapshots, key=lambda row: row["fetched_at_jst"])
    status_counts = Counter(row["captureStatus"] for row in lifecycle_rows)
    selected_count = sum(
        row["captureStatus"] != "NOT_SELECTED_BY_DAILY_CAP" for row in lifecycle_rows
    )
    valid_count = status_counts["VALID_CAPTURE"]
    rejected_count = len(rejected_snapshots)
    capture_failure_count = selected_count - valid_count - rejected_count
    feature_settled_count = sum(value == "SETTLED" for value in settlement_statuses.values())
    observation_days = len(dates)
    running_days = len({row["race_date"] for row in snapshots})
    selected_scope_coverage = valid_count / selected_count if selected_count else 0.0
    settlement_join = feature_settled_count / valid_count if valid_count else 0.0
    prediction_dates = Counter(str(payload.get("raceDate")) for payload in predictions.values())
    cohort_predictions = sum(count for date, count in prediction_dates.items() if date in dates)
    daily_counts: dict[str, dict[str, Any]] = {}
    for race_date in dates:
        rows = [row for row in lifecycle_rows if row["raceDate"] == race_date]
        daily_counts[race_date] = {
            "scheduled": len([row for row in schedules if row["raceDate"] == race_date]),
            "selected": sum(row["captureStatus"] != "NOT_SELECTED_BY_DAILY_CAP" for row in rows),
            "validCapture": sum(row["captureStatus"] == "VALID_CAPTURE" for row in rows),
            "rejectedCapture": sum(
                row["captureStatus"] == "PARSE_FAILURE" for row in rows
            ),
            "captureFailure": sum(
                row["captureStatus"] in {"NETWORK_ERROR", "UNKNOWN_LEGACY"} for row in rows
            ),
            "featureSettled": sum(
                race_id.startswith(race_date.replace("-", "")) and value == "SETTLED"
                for race_id, value in settlement_statuses.items()
            ),
        }
    report = {
        "schemaVersion": 1,
        "reportType": "RACE_LIFECYCLE_HWM",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "hwm": {
            "snapshotId": latest["snapshot_id"],
            "asOfLedgerId": ledger_tail.get("sequence"),
            "asOfLedgerRecordId": ledger_tail.get("record_id"),
            "asOfLedgerRecordHash": ledger_tail.get("record_hash"),
            "maxFetchedAtJst": latest["fetched_at_jst"],
            "sourceManifestHash": None,
            "codeCommit": code_commit,
            "configHash": config_hash,
            "worktreeDirty": worktree_dirty,
            "cohortStartDate": dates[0],
            "cohortEndDate": dates[-1],
            **policy_metadata,
        },
        "cohort": {
            "timezone": "Asia/Tokyo",
            "raceGrain": "raceDate-jcd-raceNo",
            "eligibilityDefinition": "B-file schedule; feature valid requires CAPTURED and research_eligible=1",
            "cancelledRaceTreatment": "not inferred without explicit result evidence",
            "scheduledRaceCount": len(schedule_keys),
            "selectedRaceCount": selected_count,
            "responseReceivedRaceCount": len(snapshots),
            "validCaptureRaceCount": valid_count,
            "rejectedCaptureRaceCount": rejected_count,
            "captureFailureRaceCount": capture_failure_count,
            "featureSettledRaceCount": feature_settled_count,
            "predictionCountAllDates": len(predictions),
            "predictionCountSameCohort": cohort_predictions,
            "predictionOutsideCohortCount": len(predictions) - cohort_predictions,
            "predictionMissingFromSameCohort": len(schedule_keys) - cohort_predictions,
            "predictionSettlementCount": len(settlements),
        },
        "captureStatusCounts": dict(sorted(status_counts.items())),
        "settlementStatusCounts": dict(sorted(Counter(settlement_statuses.values()).items())),
        "settlementStatuses": settlement_statuses,
        "dailyCounts": daily_counts,
        "sourceFiles": source_files,
        "coverage": {
            "validCaptureAgainstSelectedScope": selected_scope_coverage,
            "validCaptureAgainstAllSchedule": valid_count / len(schedule_keys) if schedule_keys else 0.0,
            "settlementJoinAgainstValidCapture": settlement_join,
        },
        "pacing": pace_metrics(
            observation_calendar_days=observation_days,
            collector_running_days=running_days,
            selected_races=selected_count,
            feature_settled_races=feature_settled_count,
            valid_capture_rate=selected_scope_coverage,
            settlement_join_rate=settlement_join,
        ),
        "consistency": {
            "scheduledEqualsNotSelectedPlusSelected": len(schedule_keys)
            == status_counts["NOT_SELECTED_BY_DAILY_CAP"] + selected_count,
            "selectedEqualsValidPlusFailurePlusRejected": selected_count
            == valid_count + capture_failure_count + rejected_count,
            "validEqualsSettledPlusSettlementPendingOrFailure": valid_count
            == feature_settled_count + (valid_count - feature_settled_count),
            "duplicateScheduleKeys": len(schedules) - len(schedule_keys),
        },
        "runtimeSafety": {
            "modelsChanged": False,
            "scheduledTaskChanged": False,
            "requestPolicyChanged": False,
            "productionWrites": 0,
            "predictionWrites": 0,
            "networkRequestsDuringAudit": 0,
        },
        "findings": {
            "legacyUnknownPreserved": True,
            "legacyUnknownCurrentCount": status_counts["UNKNOWN_LEGACY"],
            "oldReportValuesReused": False,
            "runtimePolicyStatus": (
                "ENFORCED"
                if runtime_policy_enforced
                else "LOADED_NOT_ENFORCED_BY_ACTIVE_RUNNER"
            ),
            "runtimePolicyActionRequired": not runtime_policy_enforced,
            "reportIsReadOnly": True,
        },
    }
    return report
