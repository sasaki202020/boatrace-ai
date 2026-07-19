from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.anchor_provider import MockAnchorProvider
from src.commercialization_v2.canonical_package import build_prediction_package, canonical_package_bytes
from src.commercialization_v2.commitment import create_commitment, verify_reveal
from src.commercialization_v2.ledger import ShadowLedgerV2

MODEL = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
SCHEMA = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--work-dir", type=Path, required=True); parser.add_argument("--report", type=Path, required=True); args = parser.parse_args(); args.work_dir.mkdir(parents=True, exist_ok=True)
    values = [0.42, 0.22, 0.14, 0.10, 0.07, 0.05]
    rows = [{"raceId": "20990102-01-01", "venue": "01", "raceNumber": 1, "lane": lane, "racerId": f"SYNTH-{lane}", "predictedProbability": values[lane - 1], "probabilityRank": lane, "topPrediction": lane == 1} for lane in range(1, 7)]
    package = build_prediction_package(race_date="2099-01-02", generated_at_utc="2099-01-01T12:00:00+00:00", generated_at_jst="2099-01-01T21:00:00+09:00", candidate_id="tree_15", model_sha256=MODEL, feature_schema_sha256=SCHEMA, canonical_dataset_sha256="bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23", as_of_artifact_sha256="c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb", input_raw_sha256="1" * 64, input_rows_sha256="2" * 64, source_id="synthetic_fixture", input_rights_status="SYNTHETIC", code_version="commercialization_v2", seed=42, predictions=rows)
    raw = canonical_package_bytes(package); commitment = create_commitment(raw, salt=bytes(range(32))); ledger = ShadowLedgerV2(args.work_dir / "shadow.sqlite3"); package_id = ledger.append_prediction_package(package, raw, commitment)
    anchor = {"schemaVersion": 2, "commitment": commitment["commitment"], "raceDate": package["raceDate"]}; provider = MockAnchorProvider("fixture/anchor"); receipt = provider.publish_anchor(anchor, repository="fixture/anchor", server_created_at="2099-01-01T14:59:59+00:00"); verification = provider.verify_anchor_receipt(receipt, payload=anchor, repository_allowlist={"fixture/anchor"}, cutoff=package["conservativeCutoff"]); ledger.append_anchor(package_id, receipt, verification["status"]); reveal_id = ledger.append_reveal(package_id, raw, commitment["saltHex"], "2099-01-02T00:00:01+09:00"); before = ledger.prediction_digest(); ledger.append_result_package("2099-01-02", [{"raceId": "20990102-01-01", "winningLane": 1}], "3" * 64); after = ledger.prediction_digest()
    report = {"dataMode": "SYNTHETIC_FIXTURE", "packageDeterministic": raw == canonical_package_bytes(package), "commitmentVerified": verify_reveal(raw, commitment["saltHex"], commitment["commitment"]), "anchorStatus": verification["status"], "externalTimestampVerified": verification["externalTimestampVerified"], "predictionHashUnchangedAfterResult": before == after, "ledgerIntegrity": ledger.verify_integrity(), "packageId": package_id, "revealId": reveal_id, "networkRequests": 0, "productionWrites": 0, "paymentActions": 0}
    args.report.parent.mkdir(parents=True, exist_ok=True); args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps(report, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
