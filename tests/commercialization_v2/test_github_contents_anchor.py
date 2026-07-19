from __future__ import annotations

import base64

import pytest

from src.commercialization_v2.github_contents_anchor import (
    CommitmentTarget,
    GitHubContentsTransport,
    SyntheticAnchorCommitService,
    canonical_synthetic_bytes,
)


TARGET = CommitmentTarget(
    owner="sasaki202020",
    repository="boatrace-prediction-anchors",
    branch="main",
    allowed_path_prefix="anchors/synthetic/",
)
PACKAGE = {
    "schemaVersion": 2,
    "testType": "SYNTHETIC_EXTERNAL_ANCHOR",
    "commitment": "a" * 64,
    "candidateId": "tree_15",
    "modelSha256": "b" * 64,
    "featureSchemaSha256": "c" * 64,
    "syntheticRowCount": 6,
    "syntheticRaceCount": 1,
    "clientCreatedAt": "2026-07-19T00:00:00+00:00",
    "noProfitClaim": True,
    "realPrediction": False,
}


class FakeContentsTransport:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str, str, str], bytes] = {}
        self.calls: list[tuple[str, str, str, str, str]] = []
        self.readback_override: bytes | None = None

    def get_content(self, owner: str, repository: str, branch: str, path: str):
        self.calls.append(("GET", owner, repository, branch, path))
        key = (owner, repository, branch, path)
        value = self.files.get(key)
        if value is None:
            return None
        return {"content": base64.b64encode(self.readback_override or value).decode(), "sha": "blob-sha"}

    def create_content(self, owner: str, repository: str, branch: str, path: str, content: bytes):
        self.calls.append(("PUT", owner, repository, branch, path))
        key = (owner, repository, branch, path)
        if key in self.files:
            raise AssertionError("overwrite attempted")
        self.files[key] = content
        return {"content": {"sha": "blob-sha"}, "commit": {"sha": "commit-sha", "committer": {"date": "2026-07-18T00:00:00Z"}}}


def service(transport=None, *, credential_marker="configured", target=TARGET):
    return SyntheticAnchorCommitService(target=target, token=credential_marker, transport=transport or FakeContentsTransport())


def test_valid_target_creates_one_file_and_readback_hash_matches() -> None:
    transport = FakeContentsTransport()
    result = service(transport).publish(PACKAGE)
    expected_hash = __import__("hashlib").sha256(canonical_synthetic_bytes(PACKAGE)).hexdigest()
    assert result["status"] == "CREATED"
    assert result["package_sha256"] == expected_hash
    assert result["path"] == f"anchors/synthetic/{PACKAGE['commitment']}.json"
    assert result["readback_sha256"] == expected_hash
    assert [call[0] for call in transport.calls] == ["GET", "PUT", "GET"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("owner", "other", "owner_not_allowed"),
        ("repository", "other", "repository_not_allowed"),
        ("branch", "dev", "branch_not_allowed"),
        ("allowed_path_prefix", "other/", "path_prefix_not_allowed"),
    ],
)
def test_target_allowlist_rejected_before_transport(field: str, value: str, reason: str) -> None:
    transport = FakeContentsTransport()
    target = CommitmentTarget(**{**TARGET.__dict__, field: value})
    with pytest.raises(ValueError, match=reason):
        service(transport, target=target).publish(PACKAGE)
    assert transport.calls == []


@pytest.mark.parametrize(
    "prefix",
    ["../anchors/synthetic/", "/anchors/synthetic/", "anchors\\synthetic/", "anchors/%2e%2e/synthetic/", "anchors/%252e%252e/synthetic/"],
)
def test_path_traversal_rejected_before_transport(prefix: str) -> None:
    transport = FakeContentsTransport()
    with pytest.raises(ValueError, match="path"):
        service(transport, target=CommitmentTarget(**{**TARGET.__dict__, "allowed_path_prefix": prefix})).publish(PACKAGE)
    assert transport.calls == []


def test_unknown_record_type_and_missing_credential_fail_before_transport() -> None:
    transport = FakeContentsTransport()
    with pytest.raises(ValueError, match="synthetic_public_schema_mismatch"):
        service(transport).publish({**PACKAGE, "prediction": "forbidden"})
    with pytest.raises(ValueError, match="credential_missing"):
        service(transport, credential_marker="").publish(PACKAGE)
    assert transport.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidateId", "REAL_RACER_4321"),
        ("clientCreatedAt", "token=secret"),
        ("modelSha256", "not-a-hash"),
        ("syntheticRowCount", 7),
        ("realPrediction", True),
    ],
)
def test_public_payload_values_are_fixed_and_safe(field: str, value: object) -> None:
    transport = FakeContentsTransport()
    with pytest.raises(ValueError):
        service(transport).publish({**PACKAGE, field: value})
    assert transport.calls == []


def test_same_content_is_idempotent_without_second_write() -> None:
    transport = FakeContentsTransport()
    first = service(transport).publish(PACKAGE)
    second = service(transport).publish(PACKAGE)
    assert first["path"] == second["path"]
    assert second["status"] == "IDEMPOTENT"
    assert [call[0] for call in transport.calls].count("PUT") == 1


def test_existing_different_content_and_readback_mismatch_fail_closed() -> None:
    transport = FakeContentsTransport()
    raw = canonical_synthetic_bytes(PACKAGE)
    package_hash = __import__("hashlib").sha256(raw).hexdigest()
    key = (TARGET.owner, TARGET.repository, TARGET.branch, f"{TARGET.allowed_path_prefix}{PACKAGE['commitment']}.json")
    transport.files[key] = b"different"
    with pytest.raises(ValueError, match="existing_content_mismatch"):
        service(transport).publish(PACKAGE)
    assert [call[0] for call in transport.calls] == ["GET"]

    transport = FakeContentsTransport()
    transport.readback_override = b"tampered"
    with pytest.raises(ValueError, match="readback_hash_mismatch"):
        service(transport).publish(PACKAGE)
    assert [call[0] for call in transport.calls] == ["GET", "PUT", "GET"]


def test_contents_transport_contract_has_no_issue_api() -> None:
    transport = FakeContentsTransport()
    service(transport).publish(PACKAGE)
    assert not hasattr(transport, "create_issue")
    assert all("issue" not in call[0].casefold() for call in transport.calls)


def test_transport_missing_credential_does_not_expose_value() -> None:
    with pytest.raises(ValueError, match="credential_missing") as error:
        GitHubContentsTransport("")
    assert "token" not in str(error.value).casefold()
