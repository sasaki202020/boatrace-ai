from __future__ import annotations

import argparse
from collections import Counter
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feature_forward_v1.live_capture import run_capture_cycle
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
    args = parser.parse_args()
    b_file = args.b_file or sorted(args.b_root.glob("B*.TXT"))[-1]
    result = run_capture_cycle(b_file=b_file, store_root=args.store)
    result["cumulative"] = _cumulative_capture_status(args.store)
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] in {
        "FEATURE_COLLECTION_STOPPED", "FEATURE_CAPTURE_NETWORK_ERROR"
    } else 0


if __name__ == "__main__":
    raise SystemExit(main())
