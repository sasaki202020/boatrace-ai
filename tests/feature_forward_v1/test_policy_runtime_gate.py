from __future__ import annotations

import hashlib
import json

import pytest

from src.feature_forward_v1.source_policy import PolicyGateError, load_policy_manifest


def manifest(evidence_path=None, evidence_sha256=None, **overrides):
    value = {
        "schemaVersion": 2,
        "usageMode": "PERSONAL_RESEARCH_ONLY",
        "personalResearchAllowed": True,
        "localStorageAllowed": True,
        "localAnalysisAllowed": True,
        "personalModelTrainingAllowed": True,
        "personalPredictionUseAllowed": True,
        "manualIngestAllowed": True,
        "automatedNetworkFetchAllowed": True,
        "networkSafetyIntegrated": True,
        "allowedHttpsHosts": ["www.boatrace.jp"],
        "commercialUseAllowed": False,
        "redistributionAllowed": False,
        "publicReleaseAllowed": False,
        "paidServiceAllowed": False,
        "allowedSourceLocationPrefixes": [
            "https://www.boatrace.jp/owpc/pc/race/beforeinfo"
        ],
        "rightsStatus": "INTERNAL_RESEARCH_APPROVED",
        "writtenConfirmation": True,
        "evidencePath": str(evidence_path) if evidence_path is not None else None,
        "evidenceSha256": evidence_sha256,
        "numericStorageAllowed": True,
        "rawStorageAllowed": True,
        "minimumRequestIntervalSeconds": 60,
        "requestsPerRace": 1,
        "requestsPerDay": 12,
        "retriesPerRace": 0,
    }
    value.update(overrides)
    return value


def test_policy_loader_returns_hash_and_runtime_loaded_marker(tmp_path):
    evidence = tmp_path / "rights-evidence.txt"
    evidence.write_text("approved for internal research", encoding="utf-8")
    path = tmp_path / "source_approval.json"
    path.write_text(
        json.dumps(
            manifest(
                evidence_path=evidence,
                evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
            )
        ),
        encoding="utf-8",
    )

    policy, metadata = load_policy_manifest(path, require_automated_fetch=True)

    assert policy.automated_fetch_allowed is True
    assert metadata["policyLoaded"] is True
    assert len(metadata["policyHash"]) == 64
    assert metadata["policyVersion"] == 2


@pytest.mark.parametrize(
    "payload",
    [manifest(schemaVersion=99), manifest(automatedNetworkFetchAllowed=False)],
)
def test_scheduled_policy_gate_fails_closed(tmp_path, payload):
    path = tmp_path / "source_approval.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PolicyGateError):
        load_policy_manifest(path, require_automated_fetch=True)


def test_missing_policy_fails_closed(tmp_path):
    with pytest.raises(PolicyGateError, match="policy_file_missing"):
        load_policy_manifest(tmp_path / "missing.json", require_automated_fetch=True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"rightsStatus": "UNVERIFIED_COMMERCIAL_USE"},
        {"writtenConfirmation": False},
        {"evidencePath": "missing-evidence.txt"},
        {"evidenceSha256": "0" * 64},
        {"numericStorageAllowed": False},
        {"rawStorageAllowed": False},
        {"requestsPerDay": 13},
        {"retriesPerRace": 1},
        {"allowedSourceLocationPrefixes": []},
    ],
)
def test_automated_fetch_requires_complete_rights_attestation(
    tmp_path, overrides
):
    evidence = tmp_path / "rights-evidence.txt"
    evidence.write_text("approved for internal research", encoding="utf-8")
    payload = manifest(
        evidence_path=evidence,
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )
    payload.update(overrides)
    path = tmp_path / "source_approval.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PolicyGateError):
        load_policy_manifest(path, require_automated_fetch=True)
