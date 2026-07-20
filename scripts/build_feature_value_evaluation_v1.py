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
from src.feature_forward_v1.collector import SCHEMA_SHA256
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
    output = []
    for group, columns in FEATURE_GROUPS.items():
        allowed_source = "A" if group in {"course_and_start_exhibition", "exhibition_time"} else "B" if group == "weather_and_water" else "C"
        if source_group != allowed_source:
            continue
        output.append({**base, "featureGroup": group, "values": {column: payload.get(column) for column in columns}})
    return output


def load_records_read_only(store_root: Path) -> list[dict]:
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
            "schemaVerified": row["schema_sha256"] == SCHEMA_SHA256,
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
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as handle:
            existing = list(csv.DictReader(handle))
    keys = {(row["date"], row["featureGroup"]) for row in rows}
    merged = [row for row in existing if (row["date"], row["featureGroup"]) not in keys] + rows
    merged.sort(key=lambda row: (row["date"], row["featureGroup"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(merged)


def _quality_markdown(as_of_date: str, quality: dict[str, dict]) -> str:
    lines = [
        "# Feature Quality Latest",
        "",
        f"- asOfDate: {as_of_date}",
        "- predictiveValueEvaluated: false",
        "- note: collection quality only; no accuracy or win-rate claim",
        "",
        "| Feature group | Captured | Verified | Coverage | Missing rate | Days |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for group in FEATURE_GROUPS:
        entry = quality[group]
        coverage = "UNKNOWN" if entry["coverage"] is None else f"{entry['coverage']:.3f}"
        missing = "UNKNOWN" if entry["missingRate"] is None else f"{entry['missingRate']:.3f}"
        lines.append(f"| {group} | {entry['capturedRaceCount']} | {entry['verifiedPreDeadlineCount']} | {coverage} | {missing} | {entry['consecutiveCollectionDays']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--settled-races", type=int, default=0)
    parser.add_argument("--settlement-manifest", type=Path)
    parser.add_argument("--schedule", type=Path)
    args = parser.parse_args(argv)

    _validate_report_root(args.report_root)
    records = load_records_read_only(args.store)
    schedule = validate_schedule_manifest(json.loads(args.schedule.read_text(encoding="utf-8")), args.schedule.parent) if args.schedule and args.schedule.is_file() else None
    quality = build_collection_quality(records, scheduled_races=schedule)
    if args.settlement_manifest and args.settlement_manifest.is_file():
        from src.feature_forward_v1.value_evaluation import validate_settlement_manifest
        settled_keys = validate_settlement_manifest(json.loads(args.settlement_manifest.read_text(encoding="utf-8")), args.settlement_manifest.parent)
        group_keys = [complete_verified_race_keys(records, group) for group in FEATURE_GROUPS]
        common_feature_keys = set.intersection(*group_keys) if group_keys else set()
        settled_races = len(settled_keys & {canonical_race_key(key) for key in common_feature_keys})
    elif args.settled_races:
        raise ValueError("settlement_evidence_required")
    else:
        settled_races = 0
    gate = predictive_value_gate(quality, settled_races)
    report_root = args.report_root
    _write_daily_csv(report_root / "feature_quality_daily.csv", _quality_rows(args.as_of_date, quality))
    _write(report_root / "feature_quality_latest.md", _quality_markdown(args.as_of_date, quality))
    _write(report_root / "feature_collection_priority.md", build_priority_markdown(quality))
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
