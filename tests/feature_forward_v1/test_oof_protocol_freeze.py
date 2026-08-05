"""Verify the committed OOF protocol points at the exact frozen artifacts."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "feature_forward_v1" / "OOF_PROTOCOL_FREEZE.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_oof_protocol_freeze_manifest_is_self_consistent() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    protocol = ROOT / manifest["protocolPath"]
    spec = ROOT / manifest["specPath"]

    assert manifest["protocolSha256"] == _sha256(protocol)
    assert manifest["specSha256"] == _sha256(spec)
    assert manifest["oofExecutedAtFreeze"] is False
    assert manifest["productionAdoptionAllowed"] is False
    assert manifest["personalAdoptionAllowed"] is False
    assert manifest["coverageDefinition"] == "mature_selected_capture_window_passed"
    assert manifest["decisionGate"]["minimumForwardDays"] == 30
    assert manifest["decisionGate"]["minimumFeatureSettledRaces"] == 1500
    assert manifest["decisionGate"]["minimumMatureCoverage"] == 0.8
