from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.commercialization_v2.gates import compute_v2_gate
from src.commercialization_v2.activation import compute_internal_prospective_readiness


def main() -> int:
    out = ROOT / "reports/commercialization_v2/prospective_gate_manifest.json"; existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    gate = compute_v2_gate(existing)
    internal = compute_internal_prospective_readiness({
        "candidateIntegrityStatus": existing.get("candidateIntegrityStatus", "CANDIDATE_FREEZE_INTEGRITY_PASS_WITH_COVERAGE_GAP"),
        "modelHashMatches": True,
        "featureSchemaHashMatches": True,
        "inputSchemaStatus": existing.get("inputSchemaStatus", "UNVERIFIED"),
        "resultFieldCount": existing.get("resultFieldCount", -1),
        "externalAnchorRepositoryApproved": existing.get("externalAnchorRepositoryApproved", False),
        "githubCredentialConfigured": existing.get("githubCredentialConfigured", False),
        "commitmentDryRunPassed": existing.get("commitmentDryRunPassed", False),
        "appendOnlyLedgerIntegrityPassed": existing.get("appendOnlyLedgerIntegrityPassed", False),
        "inputSourceRightsVerified": False,
        "inputAcquisitionTimeAttested": False,
        "paymentEnabled": False,
        "profitClaimsAllowed": False,
        "productionAdoptionAllowed": False,
    })
    payload = {
        "schemaVersion": 2, "candidateId": "tree_15",
        "modelSha256": "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0",
        "featureSchemaSha256": "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd",
        "canonicalDatasetSha256": "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23",
        "asOfArtifactSha256": "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb",
        "inputSchemaLeakageGuardPassed": False, "inputSourceRightsVerified": False,
        "inputSourceRightsEvidencePath": None, "inputSourceRightsEvidenceSha256": None,
        "inputAcquisitionTimeAttested": False, "predictionCommittedBeforeCutoff": False,
        "externalTimestampVerified": False, "predictionLedgerIntegrityPassed": False,
        "modelHashMatches": True, "featureSchemaHashMatches": True,
        "internalProspectiveGate": internal,
        **gate,
    }
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"); return 0


if __name__ == "__main__": raise SystemExit(main())
