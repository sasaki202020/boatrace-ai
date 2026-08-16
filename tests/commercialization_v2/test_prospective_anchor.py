from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from src.commercialization_v2.github_contents_anchor import CommitmentTarget
from src.commercialization_v2.prospective_anchor import (
    ProspectiveAnchorCommitService,
    canonical_prospective_bytes,
    validate_real_approval,
)


HASH = "a" * 64
PAYLOAD = {
    "schemaVersion": 2,
    "testType": "PROSPECTIVE_COMMITMENT",
    "commitment": "b" * 64,
    "candidateId": "tree_15",
    "modelSha256": HASH,
    "featureSchemaSha256": "c" * 64,
    "predictionCodeCommitSha": "3bac12373118794b1696debfefa8bd67cc202ba2",
    "raceCount": 12,
    "clientCreatedAt": "2026-07-19T14:00:00+09:00",
    "noProfitClaim": True,
    "realPrediction": True,
}
APPROVAL = {
    "humanApproved": True,
    "realPredictionPublishApproved": True,
    "issueRetentionApproved": True,
    "owner": "sasaki202020",
    "repository": "boatrace-prediction-anchors",
    "repositoryAllowlist": ["sasaki202020/boatrace-prediction-anchors"],
    "branch": "main",
    "prospectivePathPrefix": "anchors/prospective/",
    "paymentEnabled": False,
    "profitClaimsAllowed": False,
    "productionAdoptionAllowed": False,
    "bettingEnabled": False,
    "maximumVenues": 1,
    "maximumRaces": 12,
    "maximumPackages": 1,
    "maximumExternalWrites": 1,
    "maximumRetries": 0,
}


class FakeTransport:
    def __init__(self, *, date="Sun, 19 Jul 2026 05:01:00 GMT"):
        self.value = None
        self.date = date
        self.writes = 0

    def get_content(self, owner, repository, branch, path):
        if self.value is None:
            return None
        blob = __import__("hashlib").sha1(f"blob {len(self.value)}\0".encode() + self.value).hexdigest()
        return {"content": base64.b64encode(self.value).decode(), "sha": blob, "_http_date": self.date}

    def create_content(self, owner, repository, branch, path, content):
        self.writes += 1
        self.value = content
        blob = __import__("hashlib").sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        return {"content": {"sha": blob}, "commit": {"sha": "2" * 40}, "_http_date": self.date}


def service(transport):
    return ProspectiveAnchorCommitService(
        target=CommitmentTarget("sasaki202020", "boatrace-prediction-anchors", "main", "anchors/prospective/"),
        token="secret-marker",
        transport=transport,
    )


def test_public_payload_is_exact_and_rejects_secrets():
    canonical_prospective_bytes(PAYLOAD)
    for forbidden in ("raceDate", "venue", "racerId", "probability", "salt", "rawB", "inputHash", "result", "payout"):
        with pytest.raises(ValueError):
            canonical_prospective_bytes({**PAYLOAD, forbidden: "x"})


def test_approval_is_fail_closed():
    validate_real_approval(APPROVAL)
    for key in ("realPredictionPublishApproved", "humanApproved", "issueRetentionApproved"):
        with pytest.raises(ValueError):
            validate_real_approval({**APPROVAL, key: False})
    with pytest.raises(ValueError):
        validate_real_approval({**APPROVAL, "maximumRaces": 13})
    with pytest.raises(ValueError):
        validate_real_approval({**APPROVAL, "maximumRetries": 1})


def test_create_idempotent_and_conflict():
    transport = FakeTransport()
    cutoff = datetime(2026, 7, 20, tzinfo=timezone.utc)
    first = service(transport).publish(PAYLOAD, cutoff=cutoff, approval=APPROVAL)
    second = service(transport).publish(PAYLOAD, cutoff=cutoff, approval=APPROVAL)
    assert first["status"] == "CREATED"
    assert first["prospectiveRaces"] == PAYLOAD["raceCount"]
    assert second["status"] == "IDEMPOTENT"
    assert second["prospectiveRaces"] == 0
    assert transport.writes == 1
    transport.value = b"different"
    with pytest.raises(ValueError, match="existing_content_mismatch"):
        service(transport).publish(PAYLOAD, cutoff=cutoff, approval=APPROVAL)


def test_response_date_at_or_after_cutoff_is_rejected():
    transport = FakeTransport(date="Mon, 20 Jul 2026 00:00:00 GMT")
    result = service(transport).publish(PAYLOAD, cutoff=datetime(2026, 7, 20, tzinfo=timezone.utc), approval=APPROVAL)
    assert result["status"] == "LATE_COMMIT_REJECTED"
    assert result["status"] != "PASS"
    assert result["prospectiveRaces"] == 0
    assert transport.writes == 1


def test_missing_server_time_is_unverified_and_does_not_count_races():
    transport = FakeTransport(date="")

    result = service(transport).publish(
        PAYLOAD,
        cutoff=datetime(2026, 7, 20, tzinfo=timezone.utc),
        approval=APPROVAL,
    )

    assert result["status"] == "EXTERNAL_WRITE_UNVERIFIED"
    assert result["status"] != "PASS"
    assert result["prospectiveRaces"] == 0
    assert transport.writes == 1


def test_real_prediction_publish_not_approved_rejects_before_write():
    transport = FakeTransport()

    with pytest.raises(ValueError, match="real_publish_not_approved"):
        service(transport).publish(
            PAYLOAD,
            cutoff=datetime(2026, 7, 20, tzinfo=timezone.utc),
            approval={**APPROVAL, "realPredictionPublishApproved": False},
        )

    assert transport.writes == 0


def test_client_cutoff_rejects_before_write():
    transport = FakeTransport()
    late = {**PAYLOAD, "clientCreatedAt": "2026-07-20T00:00:00+00:00"}
    with pytest.raises(ValueError, match="LATE_COMMIT_REJECTED"):
        service(transport).publish(late, cutoff=datetime(2026, 7, 20, tzinfo=timezone.utc), approval=APPROVAL)
    assert transport.writes == 0


def test_wrong_path_is_rejected_before_write():
    transport = FakeTransport()
    bad = ProspectiveAnchorCommitService(
        target=CommitmentTarget("sasaki202020", "boatrace-prediction-anchors", "main", "anchors/synthetic/"),
        token="secret-marker",
        transport=transport,
    )
    with pytest.raises(ValueError):
        bad.publish(PAYLOAD, cutoff=datetime(2026, 7, 20, tzinfo=timezone.utc), approval=APPROVAL)
    assert transport.writes == 0
