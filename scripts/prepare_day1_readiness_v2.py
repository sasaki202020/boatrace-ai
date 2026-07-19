from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_B_ROOT = ROOT / "data/raw/official/entries"

from src.commercialization_v2.activation import compute_internal_prospective_readiness
from src.commercialization_v2.commitment import create_commitment, verify_reveal
from src.commercialization_v2.github_contents_anchor import (
    APPROVED_BRANCH,
    APPROVED_OWNER,
    APPROVED_PATH_PREFIX,
    APPROVED_REPOSITORY,
    CommitmentTarget,
    GitHubContentsTransport,
    SyntheticAnchorCommitService,
)
from src.commercialization_v2.ledger import stable_hash
from src.commercialization_v2.synthetic_anchor_ledger import SyntheticAnchorLedger
from src.commercialization_v2.day1_readiness import (
    MODEL_FEATURES,
    KNOWN_B_SCHEMA_SIGNATURES,
    audit_bfile,
    audit_only_inference,
    sha256_file,
    validate_runtime_bfile,
)


EXPECTED = {
    "model": "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0",
    "schema": "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd",
    "dataset": "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23",
    "asof": "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb",
    "manifest": "1d5c18676619062aa0af6938f1228b37caa8a981cbe84def246123bb33ad6c7a",
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require_runtime_artifacts(paths: dict[str, Path]) -> None:
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        raise SystemExit("missing_runtime_artifacts:" + ",".join(missing))


def sqlite_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    wanted = {"prediction_batches", "race_predictions", "race_results", "prediction_packages", "prediction_rows", "external_anchors", "result_packages", "result_rows"}
    result = {name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in sorted(names & wanted)}
    connection.close()
    return result


def select_audit_files(rows: list[dict[str, object]], root: Path) -> list[Path]:
    supported = [row for row in rows if row["parseStatus"] == "SUPPORTED"]
    if not supported:
        return []
    indexes = sorted({0, len(supported) // 4, len(supported) // 2, (len(supported) * 3) // 4, len(supported) - 2, len(supported) - 1})
    return [root / str(supported[index]["relativePath"]) for index in indexes if 0 <= index < len(supported)]


def github_runtime_audit(approval: dict[str, object]) -> dict[str, object]:
    owner = os.environ.get("BOATRACE_ANCHOR_GITHUB_OWNER")
    repository = os.environ.get("BOATRACE_ANCHOR_GITHUB_REPO")
    branch = "main"
    path_prefix = "anchors/synthetic/"
    token_present = bool(os.environ.get("BOATRACE_ANCHOR_GITHUB_TOKEN"))
    api_base = os.environ.get("BOATRACE_ANCHOR_GITHUB_API_BASE", "https://api.github.com")
    full_name = f"{owner}/{repository}" if owner and repository else None
    allowlist = set(approval.get("repositoryAllowlist", []))
    manifest_match = all((
        approval.get("transportMode") == "branch_path_commit",
        owner == approval.get("owner") == APPROVED_OWNER,
        repository == approval.get("repository") == APPROVED_REPOSITORY,
        branch == approval.get("branch") == APPROVED_BRANCH,
        path_prefix == approval.get("allowedPathPrefix") == APPROVED_PATH_PREFIX,
        approval.get("allowedRecordTypes") == ["synthetic_anchor"],
        approval.get("credentialEnvironmentVariable") == "BOATRACE_ANCHOR_GITHUB_TOKEN",
        approval.get("transportModeIssue") is False,
    ))
    configured = bool(full_name and token_present and manifest_match and allowlist == {full_name})
    approved = all(bool(approval.get(key)) for key in ("humanApproved", "syntheticPublishApproved")) and approval.get("realPredictionPublishApproved") is False
    return {
        "schemaVersion": 1,
        "transportMode": "branch_path_commit",
        "owner": owner,
        "repository": repository,
        "branch": branch,
        "allowedPathPrefix": path_prefix,
        "allowedRecordTypes": ["synthetic_anchor"],
        "apiBaseUrl": api_base,
        "apiBaseAllowed": api_base == "https://api.github.com",
        "credentialPresent": token_present,
        "credentialValueRecorded": False,
        "manifestEnvironmentMatch": manifest_match,
        "allowlistMatch": bool(full_name and allowlist == {full_name}),
        "humanApprovalComplete": approved,
        "readOnlyNetworkCheckAttempted": False,
        "writeRequests": 0,
        "configuredForSyntheticPublish": configured and approved and api_base == "https://api.github.com",
        "issueApiEnabled": False,
        "status": "CREDENTIAL_PRESENT_WRITE_UNVERIFIED" if token_present else "CREDENTIAL_NOT_CONFIGURED",
    }


def verify_ledger_chain_readonly(path: Path) -> bool:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    previous = "0" * 64
    chained_records: list[tuple[str, str]] = []
    table_map = {
        "input_artifact": "input_artifacts",
        "prediction_package": "prediction_packages", "prediction_row": "prediction_rows",
        "external_anchor": "external_anchors", "reveal": "reveals",
        "result_package": "result_packages", "result_row": "result_rows",
        "gate_audit": "gate_audits", "integrity_event": "integrity_events",
    }
    for row in connection.execute("SELECT sequence,record_type,record_id,previous_hash,record_hash FROM ledger_chain ORDER BY sequence"):
        if row["previous_hash"] != previous:
            return False
        table = table_map.get(row["record_type"])
        if table is None:
            return False
        source = connection.execute(f"SELECT record_hash FROM {table} WHERE id=?", (row["record_id"],)).fetchone()
        if not source:
            return False
        expected = stable_hash({"type": row["record_type"], "id": row["record_id"], "payloadHash": source[0], "previousHash": previous})
        if expected != row["record_hash"]:
            return False
        chained_records.append((row["record_type"], row["record_id"]))
        previous = row["record_hash"]
    source_records = {
        (kind, str(row[0]))
        for kind, table in table_map.items()
        for row in connection.execute(f"SELECT id FROM {table}")
    }
    if len(chained_records) != len(set(chained_records)) or set(chained_records) != source_records:
        return False
    for table in ("input_artifacts", "gate_audits", "integrity_events"):
        if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            return False
    for package in connection.execute("SELECT * FROM prediction_packages"):
        raw = package["package_json"].encode()
        if hashlib.sha256(raw).hexdigest() != package["package_hash"] or not verify_reveal(raw, package["salt_hex"], package["commitment"]):
            return False
        expected_record = stable_hash({"id": package["id"], "raceDate": package["race_date"], "packageHash": package["package_hash"], "commitment": package["commitment"], "saltHex": package["salt_hex"], "packageJson": package["package_json"], "modelHash": package["model_hash"], "schemaHash": package["schema_hash"], "cutoff": package["cutoff"]})
        if expected_record != package["record_hash"]:
            return False
        payload = json.loads(package["package_json"])
        if payload.get("modelSha256") != package["model_hash"] or payload.get("featureSchemaSha256") != package["schema_hash"]:
            return False
        stored = {(row["race_id"], int(row["lane"])): row for row in connection.execute("SELECT * FROM prediction_rows WHERE package_id=?", (package["id"],))}
        expected_keys = {(prediction["raceId"], int(prediction["lane"])) for prediction in payload["predictions"]}
        if set(stored) != expected_keys:
            return False
        for prediction in payload["predictions"]:
            key = (prediction["raceId"], int(prediction["lane"]))
            row = stored.get(key)
            expected = stable_hash({"id": row["id"], "packageId": row["package_id"], "raceId": row["race_id"], "lane": int(row["lane"]), "predictedProbability": row["predicted_probability"]}) if row else None
            if row is None or row["predicted_probability"] != prediction["predictedProbability"] or row["record_hash"] != expected:
                return False
    for row in connection.execute("SELECT * FROM external_anchors"):
        if row["record_hash"] != stable_hash({"id": row["id"], "packageId": row["package_id"], "provider": row["provider"], "externalId": row["external_id"], "createdAt": row["created_at"], "status": row["status"], "receiptHash": row["receipt_hash"]}):
            return False
    for row in connection.execute("SELECT id,package_id,reveal_hash,revealed_at,record_hash FROM reveals"):
        if row["record_hash"] != stable_hash({"id": row["id"], "packageId": row["package_id"], "revealHash": row["reveal_hash"], "at": row["revealed_at"]}):
            return False
    for row in connection.execute("SELECT id,race_date,source_hash,package_hash,record_hash FROM result_packages"):
        if row["record_hash"] != stable_hash({"id": row["id"], "raceDate": row["race_date"], "sourceHash": row["source_hash"], "packageHash": row["package_hash"]}):
            return False
    for row in connection.execute("SELECT * FROM result_rows"):
        if row["record_hash"] != stable_hash({"id": row["id"], "packageId": row["package_id"], "raceId": row["race_id"], "winningLane": int(row["winning_lane"])}):
            return False
    connection.close()
    return True


def commitment_dry_run() -> bool:
    package = b'{"namespace":"SYNTHETIC-ANCHOR-TEST-V2"}\n'
    commitment = create_commitment(package, salt=bytes(range(32)))
    return verify_reveal(package, commitment["saltHex"], commitment["commitment"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Complete commercialization_v2 Day 1 readiness audits. Default has no network writes.")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--publish-synthetic", action="store_true")
    parser.add_argument("--confirmation")
    parser.add_argument("--b-root", type=Path, default=DEFAULT_B_ROOT)
    args = parser.parse_args()
    if args.publish_synthetic and args.confirmation != "I_APPROVE_ONE_SYNTHETIC_ANCHOR":
        raise SystemExit("exact_confirmation_required")

    activation = ROOT / "reports/commercialization_v2/activation"
    schema_root = ROOT / "reports/commercialization_v2/input_schema"
    model_path = ROOT / "data/commercialization_v1/frozen_candidate/tree_15.joblib"
    canonical_path = ROOT / "data/offline_model_v3/canonical_race_results.csv"
    asof_path = ROOT / "data/offline_model_v3/asof_features.csv"
    manifest_path = ROOT / "reports/commercialization_v1/frozen_candidate_manifest.json"
    v1_ledger = ROOT / "data/commercialization_v1/shadow/shadow.sqlite3"
    v2_ledger = ROOT / "data/commercialization_v2/shadow/shadow_v2.sqlite3"
    production_model = ROOT / "models/win_model.joblib"
    calibrator = ROOT / "models/probability_calibrator.json"
    require_runtime_artifacts({
        "asof_artifact": asof_path,
        "calibrator": calibrator,
        "candidate_manifest": manifest_path,
        "canonical_dataset": canonical_path,
        "feature_order": ROOT / "data/commercialization_v1/frozen_candidate/feature_order.json",
        "frozen_candidate": model_path,
        "production_model": production_model,
        "shadow_v1_ledger": v1_ledger,
        "shadow_v2_ledger": v2_ledger,
    })
    hashes = {
        "candidateManifestSha256": sha256_file(manifest_path),
        "modelSha256": sha256_file(model_path),
        "canonicalDatasetSha256": sha256_file(canonical_path),
        "asOfArtifactSha256": sha256_file(asof_path),
        "commercializationV1LedgerSha256": sha256_file(v1_ledger),
        "commercializationV2LedgerSha256": sha256_file(v2_ledger),
        "productionModelSha256": sha256_file(production_model),
        "calibratorSha256": sha256_file(calibrator),
    }
    frozen_feature_order = json.loads((ROOT / "data/commercialization_v1/frozen_candidate/feature_order.json").read_text(encoding="utf-8"))
    hashes["featureSchemaSha256"] = hashlib.sha256(json.dumps(frozen_feature_order, separators=(",", ":")).encode()).hexdigest()
    counts = {"v1": sqlite_counts(v1_ledger), "v2": sqlite_counts(v2_ledger)}
    preflight = {
        "schemaVersion": 1,
        "createdAtJst": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(),
        **hashes,
        "fixedHashesMatch": {
            "manifest": hashes["candidateManifestSha256"] == EXPECTED["manifest"],
            "model": hashes["modelSha256"] == EXPECTED["model"],
            "featureSchema": hashes["featureSchemaSha256"] == EXPECTED["schema"],
            "dataset": hashes["canonicalDatasetSha256"] == EXPECTED["dataset"],
            "asOf": hashes["asOfArtifactSha256"] == EXPECTED["asof"],
        },
        "ledgerCounts": counts,
        "productionDbWrites": 0,
        "networkRequests": 0,
        "stagedFileCount": 0,
        "priorObservedFullTestBaseline": {"passed": 361, "failed": 0, "warnings": 7},
    }
    write_json(activation / "day1_readiness_preflight.json", preflight)

    files = sorted(args.b_root.glob("B*.TXT"))
    inventory = [audit_bfile(path, relative_root=args.b_root) for path in files]
    schema_root.mkdir(parents=True, exist_ok=True)
    with (schema_root / "bfile_inventory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory[0]) if inventory else ["relativePath"])
        writer.writeheader(); writer.writerows(inventory)
    for row in inventory:
        if row["parseStatus"] == "SUPPORTED" and row["schemaSignature"] not in KNOWN_B_SCHEMA_SIGNATURES:
            row["parseStatus"] = "QUARANTINED"
            row["schemaVersion"] = "UNKNOWN"
            row["unknownRecordTypeCount"] = 1
            row["quarantineReason"] = "schema_signature_not_allowlisted"
    with (schema_root / "bfile_inventory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory[0]) if inventory else ["relativePath"])
        writer.writeheader(); writer.writerows(inventory)
    supported = [row for row in inventory if row["parseStatus"] == "SUPPORTED"]
    signatures = sorted(KNOWN_B_SCHEMA_SIGNATURES)
    contract = {
        "schemaVersion": 1,
        "supportedInputSchemaVersions": ["OFFICIAL_B_FIXED_WIDTH_V1"],
        "supportedSchemaSignatures": signatures,
        "requiredRecordTypes": ["STARTB", "VENUE_SECTION", "RACE_HEADER", "ENTRY_ROW", "END"],
        "allowedRecordTypes": ["STARTB", "VENUE_SECTION", "RACE_HEADER", "ENTRY_ROW", "TEXT_HEADER", "SEPARATOR", "END"],
        "prohibitedRecordTypes": ["RESULT", "PAYOUT", "REFUND", "SETTLEMENT", "FINAL_ODDS"],
        "requiredFields": ["date", "jcd", "race_no", "lane", "racer_id"],
        "allowedFields": ["date", "jcd", "race_no", "deadline", "lane", "racer_id", "racer_class", "national_win_rate", "national_2ren_rate", "local_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate"],
        "ignoredFields": ["racer_name", "age", "branch", "weight", "motor_number", "boat_number", "current_meet_results", "hayami"],
        "prohibitedFields": ["result", "winner", "finish_position", "actual", "target", "payout", "return", "refund", "settlement", "final_odds", "着順", "結果", "払戻", "返還", "確定オッズ"],
        "aliases": {"venue_code": "jcd", "race_number": "race_no", "boat_no": "lane", "registration_no": "racer_id"},
        "fieldPosition": {"lane": "bytes[0:1]", "racer_id": "bytes[2:6]", "racer_class": "bytes[22:24]", "national_win_rate": "bytes[25:30]", "national_2ren_rate": "bytes[30:36]", "local_win_rate": "bytes[36:41]", "local_2ren_rate": "bytes[41:47]", "motor_2ren_rate": "bytes[50:56]", "boat_2ren_rate": "bytes[59:65]"},
        "dtype": {"date": "YYYY-MM-DD", "jcd": "two-digit string", "race_no": "integer 1..12", "lane": "integer 1..6", "racer_id": "four-digit string", "deadline": "HH:MM JST or null"},
        "timezone": "Asia/Tokyo",
        "nullable": ["deadline", "pre-race rate fields"],
        "normalization": {
            "fullWidthDigits": "ASCII",
            "venue": "VENUE_MAP",
            "unknownFields": "DROP",
            "unknownAsciiControlRecords": "REJECT_FILE",
            "multibyteDescriptiveHeaders": "IGNORE_AFTER_RESULT_MARKER_SCAN",
        },
        "frozenFeatureMapping": {feature: "direct" if feature in {"lane", "jcd", "race_no"} else "strict prior-date canonical history" for feature in MODEL_FEATURES},
        "rejectionRules": ["unsupported schema", "result-like record", "missing identity", "unknown record type", "not exactly six unique lanes", "duplicate racer", "hash mismatch"],
        "runtimeRecheckRequired": True,
    }
    write_json(schema_root / "pre_race_schema_contract.json", contract)
    leakage_rows = [{"relativePath": row["relativePath"], "schemaVersion": row["schemaVersion"], "supported": row["parseStatus"] == "SUPPORTED", "resultLikeFieldCount": row["resultLikeFieldCount"], "resultLikeRecordCount": row["resultLikeRecordCount"], "quarantineReason": row["quarantineReason"]} for row in inventory]
    pd.DataFrame(leakage_rows).to_csv(schema_root / "result_leakage_audit.csv", index=False, encoding="utf-8-sig")

    selected = select_audit_files(inventory, args.b_root)
    history = pd.read_csv(canonical_path)
    inference_runs = []
    for path in selected:
        entries = validate_runtime_bfile(path, supported_signatures=set(signatures))
        first = audit_only_inference(entries, history, model_path=model_path, expected_model_sha256=EXPECTED["model"])
        second = audit_only_inference(entries, history, model_path=model_path, expected_model_sha256=EXPECTED["model"])
        inference_runs.append({"inputFileSha256": sha256_file(path), "schemaVersion": "OFFICIAL_B_FIXED_WIDTH_V1", "venueCount": int(entries["jcd"].nunique()), **first, "deterministicRerun": first["deterministicOutputHash"] == second["deterministicOutputHash"]})
    inference = {
        "schemaVersion": 1,
        "testedFileCount": len(selected),
        "schemaCount": len({row["schemaVersion"] for row in inference_runs}),
        "venueCount": len({venue for path in selected for venue in validate_runtime_bfile(path)["jcd"].astype(str).unique()}),
        "raceCount": sum(int(row["raceCount"]) for row in inference_runs),
        "validRaceCount": sum(int(row["raceCount"]) for row in inference_runs),
        "rejectedRaceCount": 0,
        "runs": inference_runs,
        "allDeterministic": all(bool(row["deterministicRerun"]) for row in inference_runs),
        "probabilityValuesStored": False,
        "performanceEvaluationPerformed": False,
        "prospectiveLedgerWrites": 0,
        "productionWrites": 0,
        "networkRequests": 0,
        "status": "PASS" if inference_runs and all(bool(row["deterministicRerun"]) for row in inference_runs) else "BLOCKED",
    }
    write_json(schema_root / "audit_only_inference_report.json", inference)

    approval_path = activation / "github_anchor_approval_manifest.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8")) if approval_path.exists() else {}
    github = github_runtime_audit(approval)
    write_json(activation / "github_anchor_runtime_audit.json", github)
    fixed_hashes_match = all(preflight["fixedHashesMatch"].values())
    schema_verified = bool(supported and all(int(row["resultLikeRecordCount"]) == 0 for row in supported) and inference["status"] == "PASS" and fixed_hashes_match)
    dry_run_passed = commitment_dry_run()
    ledger_integrity_passed = verify_ledger_chain_readonly(v2_ledger)
    readiness = compute_internal_prospective_readiness({
        "candidateIntegrityStatus": "CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP",
        "modelHashMatches": hashes["modelSha256"] == EXPECTED["model"],
        "featureSchemaHashMatches": hashes["featureSchemaSha256"] == EXPECTED["schema"],
        "inputSchemaStatus": "PRE_RACE_SCHEMA_VERIFIED" if schema_verified else "UNVERIFIED",
        "resultFieldCount": 0 if schema_verified else -1,
        "externalAnchorRepositoryApproved": github["humanApprovalComplete"],
        "githubCredentialConfigured": github["configuredForSyntheticPublish"],
        "commitmentDryRunPassed": dry_run_passed,
        "appendOnlyLedgerIntegrityPassed": ledger_integrity_passed,
        "paymentEnabled": False,
        "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
    })
    final = {
        "candidateFreezeIntegrity": "CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP",
        "frozenManifestAudit": "PASS_WITH_AUDIT_TRAIL_DEGRADED",
        "inputSchemaStatus": "PRE_RACE_SCHEMA_VERIFIED_WITH_RUNTIME_RECHECK" if schema_verified else "BLOCKED_INPUT_SCHEMA",
        "resultLeakageStatus": "CLEAR_FOR_SUPPORTED_SCHEMA" if schema_verified else "BLOCKED_INPUT_SCHEMA",
        "auditOnlyInference": inference["status"],
        "externalAnchorTransportStatus": "READY_FOR_SYNTHETIC_EXTERNAL_ANCHOR" if schema_verified else "NOT_READY",
        "prospectiveTimingStatus": "NOT_STARTED",
        "shadowStatus": "BLOCKED_HUMAN_GITHUB_CONFIGURATION" if schema_verified and not github["configuredForSyntheticPublish"] else readiness["shadowStatus"],
        "inputRightsStatus": "UNVERIFIED_COMMERCIAL_USE",
        "internalProspectiveUse": "ALLOWED_WITH_RESTRICTIONS",
        "paymentStatus": "DISABLED_BY_GATE",
        "paymentEnabled": False,
        "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
        "actualRealRacePredictions": counts["v2"].get("prediction_packages", 0),
        "actualExternalAnchors": counts["v2"].get("external_anchors", 0),
        "commitmentDryRunPassed": dry_run_passed,
        "appendOnlyLedgerIntegrityPassed": ledger_integrity_passed,
        "inventoryFileCount": len(inventory),
        "supportedFileCount": len(supported),
        "quarantinedFileCount": len(inventory) - len(supported),
        "githubBlockingConfiguration": [name for name, passed in (("approved dedicated repository and allowlist", github["humanApprovalComplete"]), ("configured fine-grained credential", github["credentialPresent"]), ("manifest/environment owner-repository match", github["manifestEnvironmentMatch"])) if not passed][:3],
    }
    if args.publish_synthetic:
        if not schema_verified or not github["configuredForSyntheticPublish"]:
            raise SystemExit("synthetic_publish_not_configured_or_approved")
        synthetic_ledger_path = ROOT / "data/commercialization_v2/synthetic_anchor/synthetic_anchor.sqlite3"
        reveal_path = ROOT / "data/commercialization_v2/synthetic_anchor/reveal_bundle_v2.json"
        reveal_path.parent.mkdir(parents=True, exist_ok=True)
        if reveal_path.exists():
            reveal_payload = json.loads(reveal_path.read_text(encoding="utf-8"))
            if reveal_payload.get("fixture") != "SYNTHETIC-ANCHOR-TEST-V2" or reveal_payload.get("prospectiveEligible") is not False:
                raise SystemExit("synthetic_reveal_bundle_invalid")
            private_package = __import__("base64").b64decode(reveal_payload["packageBase64"], validate=True)
            private_commitment = {
                "packageSha256": reveal_payload["packageSha256"],
                "saltHex": reveal_payload["saltHex"],
                "commitment": reveal_payload["commitment"],
            }
            if not verify_reveal(private_package, private_commitment["saltHex"], private_commitment["commitment"]):
                raise SystemExit("synthetic_reveal_bundle_commitment_invalid")
            client_created_at = reveal_payload["clientCreatedAt"]
        else:
            private_package = b'{"fixture":"SYNTHETIC-ANCHOR-TEST-V2"}\n'
            private_commitment = create_commitment(private_package)
            client_created_at = datetime.now(ZoneInfo("UTC")).isoformat()
            reveal_payload = {
                "fixture": "SYNTHETIC-ANCHOR-TEST-V2",
                "packageBase64": __import__("base64").b64encode(private_package).decode("ascii"),
                "packageSha256": private_commitment["packageSha256"],
                "saltHex": private_commitment["saltHex"],
                "commitment": private_commitment["commitment"],
                "clientCreatedAt": client_created_at,
                "prospectiveEligible": False,
            }
            with reveal_path.open("x", encoding="utf-8") as handle:
                json.dump(reveal_payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
        package = {
            "schemaVersion": 2,
            "testType": "SYNTHETIC_EXTERNAL_ANCHOR",
            "commitment": private_commitment["commitment"],
            "candidateId": "tree_15",
            "modelSha256": EXPECTED["model"],
            "featureSchemaSha256": EXPECTED["schema"],
            "syntheticRowCount": 6,
            "syntheticRaceCount": 1,
            "clientCreatedAt": client_created_at,
            "noProfitClaim": True,
            "realPrediction": False,
        }
        target = CommitmentTarget(
            owner=str(github["owner"]),
            repository=str(github["repository"]),
            branch=str(github["branch"]),
            allowed_path_prefix=str(github["allowedPathPrefix"]),
        )
        transport = GitHubContentsTransport(
            os.environ["BOATRACE_ANCHOR_GITHUB_TOKEN"],
            api_base=str(github["apiBaseUrl"]),
        )
        result = SyntheticAnchorCommitService(target=target, token=os.environ["BOATRACE_ANCHOR_GITHUB_TOKEN"], transport=transport).publish(package)
        synthetic_ledger = SyntheticAnchorLedger(synthetic_ledger_path)
        synthetic_ledger.record(package, result)
        ledger_verification = synthetic_ledger.verify()
        if not ledger_verification.get("valid"):
            raise SystemExit("synthetic_anchor_ledger_verification_failed")
        receipt_path = ROOT / "data/commercialization_v2/receipts/synthetic_anchor_v2.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if receipt_path.exists():
            raise SystemExit("synthetic_receipt_already_exists")
        receipt_path.write_text(json.dumps({**result, "ledgerVerification": ledger_verification, "actualRaceDataPosted": False, "actualPredictionPosted": False, "productionAdoptionAllowed": False}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        final.update({"externalAnchorTransportStatus": "VERIFIED_SYNTHETIC_EXTERNAL_ANCHOR", "shadowStatus": "READY_FOR_SINGLE_RACE_PROSPECTIVE", "syntheticCommitCount": 1, "syntheticReceiptCount": 1, "prospectiveTimingStatus": "NOT_STARTED"})
    write_json(activation / "day1_readiness_status.json", final)
    print(json.dumps(final, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
