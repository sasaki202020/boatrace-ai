from __future__ import annotations

import json

import pytest

from src.feature_forward_v1.source_policy import PolicyGateError, load_policy_manifest


def manifest(**overrides):
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
        "minimumRequestIntervalSeconds": 60,
        "requestsPerRace": 1,
        "requestsPerDay": 12,
        "retriesPerRace": 0,
    }
    value.update(overrides)
    return value


def test_policy_loader_returns_hash_and_runtime_loaded_marker(tmp_path):
    path = tmp_path / "source_approval.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")

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
