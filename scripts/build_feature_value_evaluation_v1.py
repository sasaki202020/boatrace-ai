from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_REPORT_ROOT = (ROOT / "reports" / "feature_forward").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.value_evaluation import (
    CONTRACT,
    FEATURE_GROUPS,
    build_collection_quality,
    build_priority_markdown,
    contract_sha256,
    canonical_race_key,
    complete_verified_race_keys,
    predictive_value_gate,
    validate_schedule_manifest,
    verify_contract,
)
from src.feature_forward_v1.collector import LIVE_SCHEMA_SHA256, SCHEMA_SHA256
from src.feature_forward_v1.store import FeatureStore, stable_hash


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _write_contract(path: Path) -> None:
    digest = contract_sha256(CONTRACT)
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        stored_digest = existing.pop("contractSha256", None)
        if stored_digest != digest or contract_sha256(existing) != stored_digest:
            raise ValueError("feature_contract_hash_mismatch")
    verify_contract(CONTRACT, digest)
    _json(path, {**CONTRACT, "contractSha256": digest})


def _load_lifecycle_evidence(path: Path) -> dict[str, object]:
    """Load a freshly generated read-only lifecycle report as settlement evidence."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("lifecycle_evidence_invalid") from exc
    if report.get("reportType") != "RACE_LIFECYCLE_HWM":
        raise ValueError("lifecycle_evidence_type_invalid")
    cohort = report.get("cohort")
    coverage = report.get("coverage")
    consistency = report.get("consistency")
    statuses = report.get("settlementStatusCounts")
    if not isinstance(cohort, dict) or not isinstance(coverage, dict) or not isinstance(consistency, dict):
        raise ValueError("lifecycle_evidence_shape_invalid")
    if not consistency:
        raise ValueError("lifecycle_evidence_consistency_invalid")
    for key, value in consistency.items():
        if key == "duplicateScheduleKeys":
            if type(value) is not int or value != 0:
                raise ValueError("lifecycle_evidence_consistency_invalid")
        elif value is not True:
            raise ValueError("lifecycle_evidence_consistency_invalid")
    selected = cohort.get("selectedRaceCount")
    valid = cohort.get("validCaptureRaceCount")
    settled = cohort.get("featureSettledRaceCount")
    selected_coverage = coverage.get("validCaptureAgainstSelectedScope")
    if any(type(value) is not int or value < 0 for value in (selected, valid, settled)):
        raise ValueError("lifecycle_evidence_count_invalid")
    if not isinstance(selected_coverage, (int, float)) or not 0 <= float(selected_coverage) <= 1:
        raise ValueError("lifecycle_evidence_coverage_invalid")
    if valid > selected or settled > valid:
        raise ValueError("lifecycle_evidence_count_inconsistent")
    if isinstance(statuses, dict) and int(statuses.get("SETTLED", 0)) != settled:
        raise ValueError("lifecycle_evidence_settlement_count_invalid")
    return {
        "selectedRaceCount": selected,
        "validCaptureRaceCount": valid,
        "featureSettledRaceCount": settled,
        "selectedScopeCoverage": float(selected_coverage),
    }


def _time_band(deadline: str | None) -> str:
    if not deadline or "T" not in deadline:
        return "UNKNOWN"
    hour = int(deadline.split("T", 1)[1][:2])
    if hour < 12:
        return "morning"
    if hour < 16:
        return "afternoon"
    return "evening"


def _split_feature_record(base: dict, payload: dict, source_group: str) -> list[dict]:
    if source_group in FEATURE_GROUPS:
        return [{
            **base,
            "featureGroup": source_group,
            "values": {
                column: payload.get(column)
                for column in FEATURE_GROUPS[source_group]
            },
        }]
    output = []
    for group, columns in FEATURE_GROUPS.items():
        allowed_source = "A" if group in {"course_and_start_exhibition", "exhibition_time"} else "B" if group == "weather_and_water" else "C"
        if source_group != allowed_source:
            continue
        output.append({**base, "featureGroup": group, "values": {column: payload.get(column) for column in columns}})
    return output


def _schema_is_supported(schema_sha256: str) -> bool:
    return schema_sha256 in {SCHEMA_SHA256, LIVE_SCHEMA_SHA256}


def load_records_read_only(store_root: Path) -> list[dict]:
    store_root = store_root.resolve()
    if store_root.is_file() or store_root.suffix.lower() == ".sqlite3":
        raise ValueError("feature_store_must_be_directory")
    database = store_root / "feature_forward.sqlite3"
    if not database.is_file():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    snapshots = connection.execute("SELECT * FROM snapshots ORDER BY race_date,jcd,race_no").fetchall()
    for row in snapshots:
        if row["record_hash"] != stable_hash({key: row[key] for key in FeatureStore.F}):
            raise ValueError("snapshot_integrity_failed")
        raw = store_root / "raw" / row["race_date"] / row["jcd"] / str(row["race_no"]) / f"{row['snapshot_id']}.json"
        if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest() != row["raw_sha256"]:
            raise ValueError("raw_integrity_failed")
    rows = connection.execute(
        """
        SELECT s.*, f.id AS feature_record_id, f.boat_no, f.feature_group, f.payload_json,
               f.parse_status, f.missing_reason, f.record_hash AS feature_record_hash
        FROM snapshots s
        JOIN feature_records f ON f.snapshot_id = s.snapshot_id
        ORDER BY s.race_date, s.jcd, s.race_no, f.boat_no, f.feature_group
        """
    ).fetchall()
    source_hashes = {("snapshot", row["snapshot_id"]): row["record_hash"] for row in snapshots}
    for row in rows:
        source_hashes[("feature_record", row["feature_record_id"])] = row["feature_record_hash"]
    previous = "0" * 64
    chain_rows = connection.execute("SELECT * FROM ledger_chain ORDER BY sequence").fetchall()
    seen: set[tuple[str, str]] = set()
    for row in chain_rows:
        key = (row["record_type"], row["record_id"])
        payload_hash = source_hashes.get(key)
        if key in seen or payload_hash is None or row["previous_hash"] != previous:
            raise ValueError("ledger_integrity_failed")
        expected = stable_hash({"type": key[0], "id": key[1], "payloadHash": payload_hash, "previousHash": previous})
        if expected != row["record_hash"]:
            raise ValueError("ledger_integrity_failed")
        seen.add(key)
        previous = row["record_hash"]
    if len(chain_rows) != len(source_hashes):
        raise ValueError("ledger_integrity_failed")
    connection.close()
    output: list[dict] = []
    for row in rows:
        reasons = json.loads(row["reasons_json"])
        payload = json.loads(row["payload_json"])
        feature_hash = stable_hash({
            "boat_no": row["boat_no"],
            "feature_group": row["feature_group"],
            "payload": payload,
            "parse_status": row["parse_status"],
            "missing_reason": row["missing_reason"],
        })
        if feature_hash != row["feature_record_hash"]:
            raise ValueError("feature_integrity_failed")
        expected_provenance = stable_hash({
            "sourceType": row["source_type"],
            "sourceLocation": row["source_location"],
            "fetchedAtUtc": row["fetched_at_utc"],
            "fetchedAtJst": row["fetched_at_jst"],
            "deadlineJst": row["deadline_jst"],
            "rawSha256": row["raw_sha256"],
            "schemaSha256": row["schema_sha256"],
        })
        base = {
            "raceDate": row["race_date"],
            "jcd": row["jcd"],
            "raceNo": row["race_no"],
            "boatNo": row["boat_no"],
            "secondsBeforeDeadline": row["seconds_before_deadline"],
            "captureTimestampVerified": bool(row["capture_timestamp_verified"]),
            "researchEligible": bool(row["research_eligible"]),
            "provenanceSha256": row["provenance_sha256"],
            "schemaSha256": row["schema_sha256"],
            "provenanceVerified": expected_provenance == row["provenance_sha256"],
            "schemaVerified": _schema_is_supported(row["schema_sha256"]),
            "parseStatus": row["parse_status"],
            "missingReason": row["missing_reason"],
            "reasons": reasons,
            "duplicate": False,
            "timeBand": _time_band(row["deadline_jst"]),
        }
        output.extend(_split_feature_record(base, payload, row["feature_group"]))
    return output


def _validate_report_root(path: Path) -> None:
    resolved = path.resolve()
    if resolved != ALLOWED_REPORT_ROOT:
        raise ValueError("report_root_not_allowlisted")


def _quality_rows(as_of_date: str, quality: dict[str, dict]) -> list[dict]:
    rows = []
    for group in FEATURE_GROUPS:
        entry = quality[group]
        rows.append(
            {
                "date": as_of_date,
                "featureGroup": group,
                **{
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value
                    for key, value in entry.items()
                },
            }
        )
    return rows


def _write_daily_csv(path: Path, rows: list[dict]) -> None:
    existing: list[dict] = []
    existing_fieldnames: list[str] = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            existing = list(reader)
            existing_fieldnames = list(reader.fieldnames or [])
    keys = {(row["date"], row["featureGroup"]) for row in rows}
    merged = [row for row in existing if (row["date"], row["featureGroup"]) not in keys] + rows
    merged.sort(key=lambda row: (row["date"], row["featureGroup"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(existing_fieldnames)
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    for row in merged:
        for key in fieldnames:
            row.setdefault(key, "")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(merged)


def _quality_markdown(as_of_date: str, quality: dict[str, dict]) -> str:
    has_coverage_views = any(
        "rawCaptureCoverage" in entry or "matureCaptureCoverage" in entry
        for entry in quality.values()
    )
    lines = [
        "# Feature Quality Latest",
        "",
        f"- asOfDate: {as_of_date}",
        "- predictiveValueEvaluated: false",
        "- note: collection quality only; no accuracy or win-rate claim",
        "",
    ]
    if has_coverage_views:
        lines.extend([
            "| Feature group | Captured | Verified | Raw coverage | Mature coverage | Mature selected | Not due | Missing rate | Days |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
    else:
        lines.extend([
            "| Feature group | Captured | Verified | Coverage | Missing rate | Days |",
            "|---|---:|---:|---:|---:|---:|",
        ])
    for group in FEATURE_GROUPS:
        entry = quality[group]
        coverage = "UNKNOWN" if entry["coverage"] is None else f"{entry['coverage']:.3f}"
        missing = "UNKNOWN" if entry["missingRate"] is None else f"{entry['missingRate']:.3f}"
        if has_coverage_views:
            raw_coverage = entry.get("rawCaptureCoverage")
            mature_coverage = entry.get("matureCaptureCoverage")
            raw_text = "UNKNOWN" if raw_coverage is None else f"{raw_coverage:.3f}"
            mature_text = "UNKNOWN" if mature_coverage is None else f"{mature_coverage:.3f}"
            lines.append(
                f"| {group} | {entry['capturedRaceCount']} | {entry['verifiedPreDeadlineCount']} | "
                f"{raw_text} | {mature_text} | {entry.get('matureSelectedRaceCount')} | "
                f"{entry.get('captureWindowNotDueRaceCount')} | {missing} | "
                f"{entry['consecutiveCollectionDays']} |"
            )
        else:
            lines.append(f"| {group} | {entry['capturedRaceCount']} | {entry['verifiedPreDeadlineCount']} | {coverage} | {missing} | {entry['consecutiveCollectionDays']} |")
    return "\n".join(lines) + "\n"


def write_collection_quality_reports(
    *,
    report_root: Path,
    as_of_date: str,
    quality: dict[str, dict],
) -> None:
    """Refresh collection-only reports without running target-based evaluation."""
    _write_daily_csv(report_root / "feature_quality_daily.csv", _quality_rows(as_of_date, quality))
    _write(report_root / "feature_quality_latest.md", _quality_markdown(as_of_date, quality))
    _write(report_root / "feature_collection_priority.md", build_priority_markdown(quality))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--settled-races", type=int, default=0)
    parser.add_argument("--settlement-manifest", type=Path)
    parser.add_argument("--lifecycle-report", type=Path)
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--target-group", action="append", dest="target_groups")
    args = parser.parse_args(argv)

    _validate_report_root(args.report_root)
    records = load_records_read_only(args.store)
    schedule = validate_schedule_manifest(json.loads(args.schedule.read_text(encoding="utf-8")), args.schedule.parent) if args.schedule and args.schedule.is_file() else None
    quality = build_collection_quality(records, scheduled_races=schedule)
    lifecycle_evidence = _load_lifecycle_evidence(args.lifecycle_report) if args.lifecycle_report else None
    if lifecycle_evidence is not None and schedule is None:
        for entry in quality.values():
            if entry["capturedRaceCount"]:
                entry["scheduledRaceCount"] = lifecycle_evidence["selectedRaceCount"]
                entry["coverage"] = lifecycle_evidence["selectedScopeCoverage"]
                entry["coverageBasis"] = "selected_scope_from_verified_lifecycle_report"
    if args.settlement_manifest and args.settlement_manifest.is_file():
        from src.feature_forward_v1.value_evaluation import validate_settlement_manifest
        settled_keys = validate_settlement_manifest(json.loads(args.settlement_manifest.read_text(encoding="utf-8")), args.settlement_manifest.parent)
        group_keys = [complete_verified_race_keys(records, group) for group in FEATURE_GROUPS]
        common_feature_keys = set.intersection(*group_keys) if group_keys else set()
        settled_races = len(settled_keys & {canonical_race_key(key) for key in common_feature_keys})
    elif lifecycle_evidence is not None:
        settled_races = int(lifecycle_evidence["featureSettledRaceCount"])
    elif args.settled_races:
        raise ValueError("settlement_evidence_required")
    else:
        settled_races = 0
    gate = predictive_value_gate(quality, settled_races, target_groups=args.target_groups)
    report_root = args.report_root
    write_collection_quality_reports(
        report_root=report_root,
        as_of_date=args.as_of_date,
        quality=quality,
    )
    _write_contract(report_root / "feature_value_contract.json")
    _json(report_root / "predictive_value_status.json", gate)
    evidence = [
        "# Feature Predictive Evidence",
        "",
        f"- Status: {gate['status']}",
        f"- Predictive evidence ranking: {gate['predictiveEvidenceRanking']}",
        f"- Settled races: {gate['settledRaceCount']} / {gate['minimumSettledRaces']}",
        f"- Forward days: {gate['observedForwardDays']} / {gate['minimumForwardDays']}",
        "- Target evaluation executed: false",
        "- tree_15 connected: false",
        "",
        "## Blocked Reasons",
        "",
        *[f"- {reason}" for reason in gate["blockedReasons"]],
    ]
    _write(report_root / "feature_predictive_evidence.md", "\n".join(evidence) + "\n")
    print(json.dumps(gate, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
