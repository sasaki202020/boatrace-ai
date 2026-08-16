from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.live_capture import run_capture_cycle
from src.feature_forward_v1.runtime_lifecycle import (
    RuntimeGateError,
    append_capture_lifecycle,
    load_runtime_gate,
    new_run_id,
    write_append_only_json,
)
from src.feature_forward_v1.runtime_provenance import (
    RuntimeProvenanceError,
    build_runtime_provenance,
)
from src.feature_forward_v1.store import FeatureStore


def _cumulative_capture_status(store_root: Path) -> dict:
    store = FeatureStore(store_root)
    snapshots = store.connection.execute(
        "SELECT race_date, research_eligible, capture_timestamp_verified, reasons_json "
        "FROM snapshots ORDER BY race_date, jcd, race_no"
    ).fetchall()
    reasons = Counter(
        reason
        for row in snapshots
        for reason in json.loads(row["reasons_json"] or "[]")
    )
    request_db = store_root / "request_ledger.sqlite3"
    request_count = http_200_count = 0
    if request_db.exists():
        with sqlite3.connect(request_db) as connection:
            request_count = int(
                connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            )
            http_200_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM requests WHERE status_code=200"
                ).fetchone()[0]
            )
    return {
        "snapshotCount": len(snapshots),
        "researchEligibleSnapshotCount": sum(
            int(row["research_eligible"]) for row in snapshots
        ),
        "captureTimestampVerifiedCount": sum(
            int(row["capture_timestamp_verified"]) for row in snapshots
        ),
        "featureRecordCount": int(
            store.connection.execute("SELECT COUNT(*) FROM feature_records").fetchone()[0]
        ),
        "collectionDays": len(
            {row["race_date"] for row in snapshots if row["research_eligible"]}
        ),
        "requestCount": request_count,
        "http200Count": http_200_count,
        "reasonCounts": dict(reasons),
        "integrity": store.verify_integrity(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--b-file", type=Path)
    source.add_argument("--b-root", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "config/feature_forward_v1/source_approval.json",
    )
    parser.add_argument(
        "--gate-config",
        type=Path,
        default=ROOT / "config/feature_forward_v1/runtime_gate.json",
    )
    args = parser.parse_args()

    run_id = new_run_id("feature-collector")
    task_run_id = "BOATRACE-Feature-Forward-Collector-V1"
    manifest_dir = args.status.parent / "run_manifests"
    manifest_path = manifest_dir / f"{run_id}.json"
    provenance_path = manifest_dir / f"{run_id}.provenance.json"
    try:
        provenance_bundle = build_runtime_provenance(
            ROOT,
            gate_config_path=args.gate_config,
            policy_path=args.policy,
        )
        provenance = provenance_bundle["provenance"]
        write_append_only_json(
            provenance_path,
            {
                "schemaVersion": 1,
                "runId": run_id,
                "taskRunId": task_run_id,
                "provenance": provenance,
                "sourceFiles": provenance_bundle["sourceFiles"],
            },
        )
    except (OSError, RuntimeGateError, RuntimeProvenanceError) as exc:
        result = {
            "status": "BLOCKED_RUNTIME_PROVENANCE",
            "executionStatus": "BLOCKED",
            "blockingGate": "RUNTIME_PROVENANCE",
            "reason": type(exc).__name__,
            "runtimeAttempts": 0,
            "runtimeFailures": 1,
            "networkRequests": 0,
            "productionWrites": 0,
            "prospectiveWrites": 0,
            "runId": run_id,
        }
        args.status.parent.mkdir(parents=True, exist_ok=True)
        args.status.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
        return 2

    try:
        gate = load_runtime_gate(
            ROOT,
            gate_config_path=args.gate_config,
            policy_path_override=args.policy,
        )
    except RuntimeGateError as exc:
        result = {
            "status": "BLOCKED_RUNTIME_GATE",
            "executionStatus": "BLOCKED",
            "blockingGate": "SOURCE_POLICY_RUNTIME",
            "reason": str(exc),
            "runtimeAttempts": 0,
            "runtimeFailures": 0,
            "networkRequests": 0,
            "productionWrites": 0,
            "prospectiveWrites": 0,
            "runId": run_id,
            "provenance": provenance,
            "provenancePath": str(provenance_path.resolve()),
        }
        write_append_only_json(
            manifest_path,
            {
                "schemaVersion": 1,
                "runId": run_id,
                "taskRunId": task_run_id,
                "provenance": provenance,
                "result": result,
            },
        )
        args.status.parent.mkdir(parents=True, exist_ok=True)
        args.status.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
        return 2

    candidates = sorted(args.b_root.glob("B*.TXT")) if args.b_root else []
    if args.b_file is not None:
        b_file = args.b_file
    elif candidates:
        b_file = candidates[-1]
    else:
        result = {
            **gate.as_dict(),
            "status": "WAITING_FOR_NEXT_BFILE",
            "executionStatus": "WAITING",
            "runtimeAttempts": 0,
            "runtimeFailures": 0,
            "networkRequests": 0,
            "productionWrites": 0,
            "prospectiveWrites": 0,
            "runId": run_id,
            "provenance": provenance,
            "provenancePath": str(provenance_path.resolve()),
        }
        write_append_only_json(
            manifest_path,
            {
                "schemaVersion": 1,
                "runId": run_id,
                "taskRunId": task_run_id,
                "provenance": provenance,
                "result": result,
            },
        )
        args.status.parent.mkdir(parents=True, exist_ok=True)
        args.status.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    now_utc = datetime.now(timezone.utc)
    result = run_capture_cycle(
        b_file=b_file,
        store_root=args.store,
        requests_per_day=gate.requests_per_day,
    )
    result.update(
        {
            **gate.as_dict(),
            "runId": run_id,
            "taskRunId": task_run_id,
            "bFile": str(b_file.resolve()),
            "bFileSha256": hashlib.sha256(b_file.read_bytes()).hexdigest(),
            "provenance": provenance,
            "provenancePath": str(provenance_path.resolve()),
            "runtimeAttempts": 1,
            "runtimeFailures": int(result.get("status") in {"FEATURE_CAPTURE_NETWORK_ERROR", "FEATURE_COLLECTION_STOPPED"}),
            "productionWrites": 0,
            "prospectiveWrites": 0,
        }
    )
    try:
        result["lifecycle"] = append_capture_lifecycle(
            b_file=b_file,
            store_root=args.store,
            gate=gate,
            collector_run_id=run_id,
            task_run_id=task_run_id,
            now_utc=now_utc,
        )
    except Exception as exc:
        result.update(
            {
                "status": "BLOCKED_LIFECYCLE",
                "executionStatus": "BLOCKED",
                "blockingGate": "LIFECYCLE_INTEGRITY",
                "reason": type(exc).__name__,
                "runtimeFailures": 1,
            }
        )
    result["cumulative"] = _cumulative_capture_status(args.store)
    manifest = {
        "schemaVersion": 1,
        "runId": run_id,
        "taskRunId": task_run_id,
        "runAtUtc": now_utc.isoformat(),
        "provenance": provenance,
        **gate.as_dict(),
        "bFile": str(b_file.resolve()),
        "bFileSha256": result["bFileSha256"],
        "result": result,
    }
    write_append_only_json(manifest_path, manifest)
    result["runManifestPath"] = str(manifest_path.resolve())
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] in {
        "FEATURE_COLLECTION_STOPPED",
        "FEATURE_CAPTURE_NETWORK_ERROR",
        "BLOCKED_LIFECYCLE",
    } else 0


if __name__ == "__main__":
    raise SystemExit(main())
