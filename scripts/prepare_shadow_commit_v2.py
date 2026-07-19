from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.canonical_package import canonical_package_bytes
from src.commercialization_v2.commitment import create_commitment
from src.commercialization_v2.ledger import ShadowLedgerV2

MODEL = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
SCHEMA = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an offline commit-reveal package. No network access.")
    parser.add_argument("--package-json", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/commercialization_v2/shadow/shadow_v2.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/commercialization_v2")
    args = parser.parse_args(); package = json.loads(args.package_json.read_text(encoding="utf-8")); raw = canonical_package_bytes(package); commitment = create_commitment(raw)
    ledger = ShadowLedgerV2(args.ledger, expected_model_sha256=MODEL, expected_schema_sha256=SCHEMA); package_id = ledger.append_prediction_package(package, raw, commitment)
    secret = args.output_dir / "packages" / f"{package['raceDate']}.prediction.json"; salt = args.output_dir / "commitments" / f"{package['raceDate']}.salt"
    public = args.output_dir / "commitments" / f"{package['raceDate']}.anchor.json"; secret.parent.mkdir(parents=True, exist_ok=True); salt.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(raw); salt.write_text(commitment["saltHex"] + "\n", encoding="ascii")
    anchor = {"schemaVersion": 2, "commitment": commitment["commitment"], "raceDate": package["raceDate"], "conservativeCutoff": package["conservativeCutoff"], "candidateId": package["candidateId"], "modelSha256": package["modelSha256"], "featureSchemaSha256": package["featureSchemaSha256"], "packageRowCount": len(package["predictions"]), "raceCount": len({row["raceId"] for row in package["predictions"]}), "createdAt": package["generatedAtUtc"], "noProfitClaim": True}
    public.write_text(json.dumps(anchor, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"status": "LOCAL_COMMIT_ONLY", "packageId": package_id, "packageSha256": commitment["packageSha256"], "commitment": commitment["commitment"], "publicAnchor": str(public)})); return 0


if __name__ == "__main__": raise SystemExit(main())
