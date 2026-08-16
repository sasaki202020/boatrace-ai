from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_feature_value_evaluation_v1 import load_records_read_only
from scripts.run_course_start_challenger_v1 import (
    _selected_scope_by_date,
    attach_capture_coverage_views,
    build_joined_race_rows,
    derive_assessment_dates,
    load_prediction_settlement_records,
    load_selected_scope_schedule,
    mature_selected_schedule,
)
from src.feature_forward_v1.course_start_contract import build_course_start_contract_audit
from src.feature_forward_v1.manual_ingest_preflight import (
    preflight_manual_inbox,
    verify_feature_store_integrity_read_only,
)
from src.feature_forward_v1.oof_data_readiness import (
    build_oof_data_readiness,
    render_oof_data_readiness_markdown,
)
from src.feature_forward_v1.oof_readiness import build_fold_preflight, load_oof_spec
from src.feature_forward_v1.value_evaluation import build_collection_quality, complete_verified_race_keys


JST = timezone(timedelta(hours=9))
FEATURE_STORE_RELATIVE = Path("data/research/feature_forward_v1/store")
INBOX_RELATIVE = Path("data/research/feature_forward_v1/inbox")
REPORT_RELATIVE = Path("reports/feature_forward_v1")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if len(value) == 40 else None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def verify_lifecycle_ledger_read_only(database: Path) -> dict[str, Any]:
    """Recompute lifecycle ledger integrity and failure counts without opening it for write."""
    database = Path(database)
    if not database.is_file():
        return {
            "valid": False,
            "recordCount": 0,
            "tailHash": None,
            "newUnknownCount": 0,
            "terminalConflictCount": 0,
            "leakageCount": 0,
            "timeOrderViolationCount": 0,
            "reason": "lifecycle_ledger_missing",
        }
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM race_lifecycle_events ORDER BY ledger_sequence"
        ).fetchall()
    except sqlite3.Error as exc:
        return {
            "valid": False,
            "recordCount": 0,
            "tailHash": None,
            "newUnknownCount": 0,
            "terminalConflictCount": 0,
            "leakageCount": 0,
            "timeOrderViolationCount": 0,
            "reason": f"sqlite:{type(exc).__name__}",
        }
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    previous = "0" * 64
    valid = True
    reason: str | None = None
    terminal_statuses: dict[tuple[str, str, int, str], set[str]] = {}
    unknown = 0
    leakage = 0
    time_order = 0
    for expected_sequence, row in enumerate(rows, start=1):
        # Ledger order serializes independent task writes; only an unusable UTC
        # timestamp makes the race-local time contract impossible to verify.
        try:
            occurred = datetime.fromisoformat(row["occurred_at_utc"])
            if occurred.tzinfo is None:
                time_order += 1
        except (TypeError, ValueError):
            time_order += 1
        if row["status_code"] == "UNKNOWN_LEGACY":
            unknown += 1
        if "leak" in str(row["reason_detail"]).lower():
            leakage += 1
        if bool(row["terminal"]):
            key = (row["target_date"], row["venue"], int(row["race_no"]), row["stage"])
            terminal_statuses.setdefault(key, set()).add(str(row["status_code"]))
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
        expected_dedupe = _stable_hash(dedupe_payload)
        expected_hash = _stable_hash(
            {**base, "eventId": row["event_id"], "previousEventHash": previous}
        )
        if (
            row["ledger_sequence"] != expected_sequence
            or row["previous_event_hash"] != previous
            or row["dedupe_key"] != expected_dedupe
            or row["event_hash"] != expected_hash
        ):
            valid = False
            reason = "lifecycle_hash_chain_invalid"
            break
        previous = row["event_hash"]
    terminal_conflicts = sum(len(statuses) > 1 for statuses in terminal_statuses.values())
    return {
        "valid": valid,
        "recordCount": len(rows),
        "tailHash": previous if valid else None,
        "newUnknownCount": unknown,
        "terminalConflictCount": terminal_conflicts,
        "leakageCount": leakage,
        "timeOrderViolationCount": time_order,
        "reason": reason,
    }


def _manifest_failure_summary(data_root: Path) -> dict[str, Any]:
    directories = (
        data_root / "data/research/feature_forward_v1/manifests",
        data_root / "reports/feature_forward_v1/run_manifests",
    )
    checked = 0
    production_relevant = 0
    external_or_network_writes = 0
    errors: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name.endswith(".provenance.json"):
                continue
            payload = _load_json(path)
            if payload is None:
                errors.append("run_manifest_unreadable")
                continue
            checked += 1
            result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            production_writes = int(result.get("productionWrites") or 0)
            prospective_writes = int(result.get("prospectiveWrites") or 0)
            network_writes = int(result.get("networkRequests") or 0)
            if production_writes or prospective_writes or network_writes:
                external_or_network_writes += production_writes + prospective_writes + network_writes
            status = str(result.get("status") or "")
            if (
                production_writes
                or prospective_writes
                or status in {"LATE_COMMIT_REJECTED", "EXTERNAL_WRITE_UNVERIFIED"}
            ):
                production_relevant += 1
    return {
        "checkedManifestCount": checked,
        "productionRelevantFailureCount": production_relevant,
        "externalOrNetworkWriteCount": external_or_network_writes,
        "errors": sorted(set(errors)),
    }


def _empty_quality() -> dict[str, Any]:
    return {
        "consecutiveCollectionDays": 0,
        "coverage": None,
        "resultLeakageCount": 0,
        "rawCaptureCoverage": None,
        "matureCaptureCoverage": None,
        "rawSelectedRaceCount": None,
        "matureSelectedRaceCount": None,
        "captureWindowNotDueRaceCount": 0,
        "coverageDefinition": "mature_selected_capture_window_passed",
    }


def collect_current_readiness(
    *, data_root: Path, spec: dict[str, Any], approval: dict[str, Any], now: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[str]]:
    """Read canonical local evidence only; never fit or evaluate a model."""
    data_root = Path(data_root)
    store_root = data_root / FEATURE_STORE_RELATIVE
    inbox = data_root / INBOX_RELATIVE
    request_ledger = store_root / "request_ledger.sqlite3"
    lifecycle_database = store_root / "race_lifecycle.sqlite3"
    entries_root = data_root / "data/raw/official/entries"
    prediction_root = data_root / "data/prospective/predictions"
    settlement_root = data_root / "data/prospective/settlements"
    errors: list[str] = []

    manual_ingest = preflight_manual_inbox(
        inbox=inbox,
        approval=approval,
        store_root=store_root,
        now=now,
    )
    feature_integrity = verify_feature_store_integrity_read_only(store_root)
    lifecycle_integrity = verify_lifecycle_ledger_read_only(lifecycle_database)
    if feature_integrity.get("checked") is not True:
        errors.append("feature_store_evidence_unavailable")
    if feature_integrity.get("valid") is not True:
        errors.append("feature_store_hash_chain_invalid")
    if lifecycle_integrity.get("valid") is not True:
        errors.append("lifecycle_hash_chain_invalid")

    try:
        records = load_records_read_only(store_root)
    except (OSError, ValueError, sqlite3.Error):
        records = []
        errors.append("feature_store_records_unreadable")
    feature_group = str(spec["featureGroup"])
    feature_keys = complete_verified_race_keys(records, feature_group)
    try:
        course_start_contract = build_course_start_contract_audit(records)
    except (TypeError, ValueError):
        course_start_contract = {"resultLeakageCount": 0, "contractPass": False}
        errors.append("feature_contract_audit_unavailable")

    try:
        predictions, settlements = load_prediction_settlement_records(prediction_root, settlement_root)
        joined = build_joined_race_rows(predictions, settlements, records)
    except (OSError, ValueError, sqlite3.Error):
        predictions, settlements, joined = {}, {}, []
        errors.append("prediction_settlement_evidence_unreadable")

    schedule: list[dict[str, Any]] | None = None
    coverage_metadata: dict[str, Any] = {"status": "UNAVAILABLE"}
    if feature_keys and request_ledger.is_file() and entries_root.is_dir():
        try:
            scope_dates = set(_selected_scope_by_date(request_ledger))
            assessment_dates = derive_assessment_dates(feature_keys, scope_dates)
            schedule, coverage_metadata = load_selected_scope_schedule(
                entries_root, request_ledger, set(assessment_dates)
            )
        except (OSError, ValueError, sqlite3.Error):
            errors.append("coverage_schedule_unreadable")
    else:
        errors.append("coverage_denominator_unavailable")

    try:
        raw_quality = build_collection_quality(records, scheduled_races=schedule)
        if schedule is None:
            quality = raw_quality
        else:
            mature_schedule, not_due_schedule = mature_selected_schedule(schedule, as_of=now)
            mature_quality = build_collection_quality(records, scheduled_races=mature_schedule)
            quality = attach_capture_coverage_views(
                raw_quality,
                mature_quality,
                raw_selected_race_count=len(schedule),
                mature_selected_race_count=len(mature_schedule),
                capture_window_not_due_race_count=len(not_due_schedule),
            )
        feature_quality = dict(quality.get(feature_group, _empty_quality()))
    except (TypeError, ValueError):
        feature_quality = _empty_quality()
        errors.append("collection_quality_unavailable")
    if coverage_metadata.get("status") != "VERIFIED_LOCAL_SELECTED_SCOPE":
        errors.append("coverage_denominator_unavailable")
    coverage = feature_quality.get("matureCaptureCoverage")
    if coverage is None:
        coverage = feature_quality.get("coverage")

    fold_preflight = build_fold_preflight(
        joined,
        minimum_validation_races_per_fold=int(
            spec["diagnosticGate"]["minimumValidationRacesPerFold"]
        ),
    )
    accounting = fold_preflight.get("accounting", {})
    folds = list(fold_preflight.get("folds", []))
    manifest_summary = _manifest_failure_summary(data_root)
    errors.extend(manifest_summary["errors"])
    current = {
        "forwardCollectionDays": int(feature_quality.get("consecutiveCollectionDays") or 0),
        "validCaptureCount": len(feature_keys),
        "featureSettledRaceCount": len(joined),
        "matureCaptureCoverage": coverage,
        "totalEligibleRaceCount": int(accounting.get("totalEligibleRaceCount") or 0),
        "initialTrainRaceCount": int(accounting.get("initialTrainRaceCount") or 0),
        "validationRaceCount": int(accounting.get("validationRaceCount") or 0),
        "oofDateCount": sum(int(fold.get("validationDateCount") or 0) for fold in folds),
        "oofRaceCount": int(accounting.get("validationRaceCount") or 0),
        "newUnknownCount": int(lifecycle_integrity.get("newUnknownCount") or 0),
        "terminalConflictCount": int(lifecycle_integrity.get("terminalConflictCount") or 0),
        "leakageCount": int(feature_quality.get("resultLeakageCount") or 0)
        + int(course_start_contract.get("resultLeakageCount") or 0)
        + int(lifecycle_integrity.get("leakageCount") or 0),
        "timeOrderViolationCount": int(lifecycle_integrity.get("timeOrderViolationCount") or 0),
        "hashChainValid": bool(
            feature_integrity.get("checked") is True
            and feature_integrity.get("valid") is True
            and lifecycle_integrity.get("valid") is True
        ),
        "productionRelevantFailureCount": int(
            manifest_summary["productionRelevantFailureCount"]
        ),
    }
    if current["timeOrderViolationCount"]:
        errors.append("time_order_violation_detected")
    evidence = {
        "readOnly": True,
        "networkRequests": 0,
        "featureStore": feature_integrity,
        "lifecycle": lifecycle_integrity,
        "runManifests": manifest_summary,
        "coverage": coverage_metadata,
        "predictionRecordCount": len(predictions),
        "settlementRecordCount": len(settlements),
        "courseStartContractPass": course_start_contract.get("contractPass"),
    }
    return current, folds, {"manualIngest": manual_ingest, "evidence": evidence}, sorted(set(errors))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only OOF data-readiness reporting. It never evaluates a model."
    )
    parser.add_argument("--data-root", type=Path)
    parser.add_argument(
        "--oof-spec",
        type=Path,
        default=ROOT / "config/feature_forward_v1/oof_evaluation_spec.json",
    )
    parser.add_argument(
        "--approval",
        type=Path,
        default=ROOT / "config/feature_forward_v1/source_approval.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_root = Path(args.data_root or os.environ.get("BOATRACE_MVP_ROOT") or ROOT).resolve()
    spec = load_oof_spec(args.oof_spec.resolve())
    approval = _load_json(args.approval.resolve())
    if approval is None:
        raise ValueError("source_approval_unreadable")
    now = datetime.now(JST)
    current, folds, context, evidence_errors = collect_current_readiness(
        data_root=data_root,
        spec=spec,
        approval=approval,
        now=now,
    )
    source_policy_ok = (
        approval.get("manualIngestAllowed") is True
        and approval.get("automatedNetworkFetchAllowed") is False
        and approval.get("automatedCollectionAllowed") is False
    )
    if not source_policy_ok:
        evidence_errors.append("source_policy_not_manual_only")
    report = build_oof_data_readiness(
        spec=spec,
        current=current,
        folds=folds,
        manual_ingest=context["manualIngest"],
        evidence_errors=evidence_errors,
    )
    report.update(
        {
            "gitSha": _git_sha(),
            "dataSnapshot": _stable_hash(
                {
                    "featureStoreTailHash": context["evidence"]["featureStore"].get("tailHash"),
                    "lifecycleTailHash": context["evidence"]["lifecycle"].get("tailHash"),
                    "coverage": context["evidence"]["coverage"],
                }
            ),
            "runtimeHash": _stable_hash(
                {"oofSpec": spec, "sourceApproval": approval}
            ),
            "generatedAtUtc": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "command": "scripts/build_oof_data_readiness_v1.py --mode readiness-only",
            "exitCode": 0,
            "mode": "READINESS_ONLY",
            "networkUsed": False,
            "productionWrites": 0,
            "prospectiveWrites": 0,
            "sourcePolicy": {
                "manualIngestAllowed": approval.get("manualIngestAllowed"),
                "automatedNetworkFetchAllowed": approval.get("automatedNetworkFetchAllowed"),
                "automatedCollectionAllowed": approval.get("automatedCollectionAllowed"),
            },
        }
    )
    report["integrity"].update(context["evidence"])
    output_root = data_root / REPORT_RELATIVE
    _write_json(output_root / "oof_readiness_latest.json", report)
    (output_root / "oof_readiness_latest.md").write_text(
        render_oof_data_readiness_markdown(report), encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "diagnosticReady": report["diagnosticReady"],
                "decisionReady": report["decisionReady"],
                "oofExecuted": False,
                "networkUsed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
