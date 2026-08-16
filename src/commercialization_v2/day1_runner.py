from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .anchor_provider import AnchorReceipt
from .canonical_package import canonical_package_bytes
from .commitment import create_commitment, verify_reveal
from .github_contents_anchor import CommitmentTarget, ContentsTransport
from .ledger import ShadowLedgerV2
from .prospective_anchor import ProspectiveAnchorCommitService


@dataclass(frozen=True)
class Day1Result:
    status: str
    external_writes: int
    prospective_races: int
    detail: dict[str, Any]


def find_next_bfile(root: Path, *, now: datetime) -> Path | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_timezone_required")
    eligible = []
    for path in root.glob("B??????.TXT"):
        try:
            race_date = datetime.strptime(path.stem[1:], "%y%m%d").date()
        except ValueError:
            continue
        if race_date > now.date():
            eligible.append((race_date, path.name, path))
    return min(eligible)[2] if eligible else None


def build_public_payload(package: Mapping[str, Any], commitment: str, *, code_commit: str) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "testType": "PROSPECTIVE_COMMITMENT",
        "commitment": commitment,
        "candidateId": package["candidateId"],
        "modelSha256": package["modelSha256"],
        "featureSchemaSha256": package["featureSchemaSha256"],
        "predictionCodeCommitSha": code_commit,
        "raceCount": len({row["raceId"] for row in package["predictions"]}),
        "clientCreatedAt": package["generatedAtJst"],
        "noProfitClaim": True,
        "realPrediction": True,
    }


def execute_package(
    package: dict[str, Any], *, ledger_path: Path, approval: Mapping[str, Any],
    token: str, transport: ContentsTransport, code_commit: str, salt: bytes | None = None,
) -> Day1Result:
    predictions = list(package.get("predictions", []))
    venues = {str(row.get("venue", "")) for row in predictions}
    races = {str(row.get("raceId", "")) for row in predictions}
    if len(venues) != 1:
        raise ValueError("single_venue_required")
    if not 1 <= len(races) <= 12:
        raise ValueError("day1_race_count_invalid")
    raw = canonical_package_bytes(package)
    ledger = ShadowLedgerV2(
        ledger_path,
        expected_model_sha256=str(package["modelSha256"]),
        expected_schema_sha256=str(package["featureSchemaSha256"]),
    )
    existing = ledger.connection.execute(
        "SELECT id,package_hash,commitment,salt_hex,package_json FROM prediction_packages WHERE race_date=?",
        (str(package["raceDate"]),),
    ).fetchone()
    if existing:
        commitment = create_commitment(raw, salt=bytes.fromhex(str(existing["salt_hex"])))
    else:
        commitment = create_commitment(raw, salt=salt)
    if existing:
        if existing["package_hash"] != commitment["packageSha256"] or existing["commitment"] != commitment["commitment"] or existing["package_json"] != raw.decode():
            raise ValueError("existing_prediction_package_mismatch")
        package_id = str(existing["id"])
    else:
        package_id = ledger.append_prediction_package(package, raw, commitment)
    guard_path = ledger_path.parent / "external_write_guard.json"
    guard_payload = json.dumps({"raceDate": package["raceDate"], "commitment": commitment["commitment"]}, sort_keys=True)
    try:
        with guard_path.open("x", encoding="utf-8") as handle:
            handle.write(guard_payload + "\n")
    except FileExistsError:
        existing_guard = guard_path.read_text(encoding="utf-8").strip()
        if existing_guard != guard_payload:
            raise ValueError("daily_external_write_limit_reached") from None
    payload = build_public_payload(package, commitment["commitment"], code_commit=code_commit)
    target = CommitmentTarget(
        str(approval["owner"]), str(approval["repository"]), str(approval["branch"]),
        str(approval["prospectivePathPrefix"]),
    )
    result = ProspectiveAnchorCommitService(target=target, token=token, transport=transport).publish(
        payload, cutoff=datetime.fromisoformat(str(package["conservativeCutoff"])), approval=approval,
    )
    prior_verified = ledger.connection.execute(
        "SELECT 1 FROM external_anchors WHERE package_id=? AND status='EXTERNALLY_COMMITTED'", (package_id,)
    ).fetchone()
    verified = result["status"] == "CREATED" or (result["status"] == "IDEMPOTENT" and prior_verified is not None)
    if result["status"] == "IDEMPOTENT" and prior_verified is not None:
        integrity = ledger.verify_integrity()
        if integrity.get("valid") is not True:
            raise ValueError("ledger_integrity_failed")
        return Day1Result("PASS", 0, 0, {"ledgerIntegrity": integrity, "idempotent": True})
    anchor_status = "EXTERNALLY_COMMITTED" if verified else (
        "LATE_COMMIT_REJECTED" if result["status"] == "LATE_COMMIT_REJECTED" else "INVALID_COMMITMENT"
    )
    receipt_base = {
        "provider": "github_contents_api", "repository": f"{target.owner}/{target.repository}",
        "external_id": result["commitSha"],
        "url": f"https://github.com/{target.owner}/{target.repository}/blob/{target.branch}/{result['path']}",
        "created_at": result["serverCreatedAt"] or str(package["generatedAtUtc"]),
        "updated_at": result["serverCreatedAt"] or str(package["generatedAtUtc"]),
        "body_hash": result["contentSha256"],
    }
    receipt = AnchorReceipt(
        **receipt_base,
        receipt_hash=hashlib.sha256(json.dumps(receipt_base, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )
    prior_anchor = ledger.connection.execute(
        "SELECT 1 FROM external_anchors WHERE package_id=? AND receipt_hash=?", (package_id, receipt.receipt_hash)
    ).fetchone()
    if not prior_anchor:
        ledger.append_anchor(package_id, receipt, anchor_status)
    if not verify_reveal(raw, commitment["saltHex"], commitment["commitment"]):
        raise ValueError("reveal_verification_failed")
    prior_reveal = ledger.connection.execute("SELECT 1 FROM reveals WHERE package_id=?", (package_id,)).fetchone()
    if not prior_reveal:
        ledger.append_reveal(package_id, raw, commitment["saltHex"], result["serverCreatedAt"] or str(package["generatedAtUtc"]))
    integrity = ledger.verify_integrity()
    if integrity.get("valid") is not True:
        raise ValueError("ledger_integrity_failed")
    race_count = int(payload["raceCount"])
    return Day1Result(
        "PASS" if verified else result["status"], int(result["externalWriteCount"]), race_count if result["status"] == "CREATED" else 0,
        {"receiptHash": receipt.receipt_hash, "contentSha256": result["contentSha256"], "ledgerIntegrity": integrity},
    )
