from __future__ import annotations

from scripts.prepare_day1_readiness_v2 import DEFAULT_B_ROOT, ROOT, github_runtime_audit, require_runtime_artifacts
import pytest


def test_day1_runner_has_no_issue_transport_dependency() -> None:
    source = __import__("inspect").getsource(__import__("scripts.prepare_day1_readiness_v2", fromlist=["main"]))
    assert "GitHubIssueAnchor" not in source
    assert "GitHubRestTransport" not in source
    assert "create_issue" not in source


def test_day1_runner_default_b_root_is_repo_relative() -> None:
    assert DEFAULT_B_ROOT == ROOT / "data/raw/official/entries"
    assert DEFAULT_B_ROOT.relative_to(ROOT).as_posix() == "data/raw/official/entries"


def test_day1_runner_reports_missing_runtime_artifacts_fail_closed(tmp_path) -> None:
    with pytest.raises(SystemExit, match="missing_runtime_artifacts:candidate,model"):
        require_runtime_artifacts({"model": tmp_path / "model.joblib", "candidate": tmp_path / "manifest.json"})


def test_github_configuration_fails_closed_without_human_settings(monkeypatch) -> None:
    for name in (
        "BOATRACE_ANCHOR_GITHUB_OWNER",
        "BOATRACE_ANCHOR_GITHUB_REPO",
        "BOATRACE_ANCHOR_GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    audit = github_runtime_audit({
        "owner": None,
        "repository": None,
        "repositoryAllowlist": [],
        "humanApproved": False,
        "syntheticPublishApproved": False,
        "realPredictionPublishApproved": False,
        "issueRetentionApproved": False,
    })
    assert audit["configuredForSyntheticPublish"] is False
    assert audit["writeRequests"] == 0
    assert audit["credentialValueRecorded"] is False


def test_github_configuration_requires_exact_allowlist_and_approval(monkeypatch) -> None:
    monkeypatch.setenv("BOATRACE_ANCHOR_GITHUB_OWNER", "sasaki202020")
    monkeypatch.setenv("BOATRACE_ANCHOR_GITHUB_REPO", "boatrace-prediction-anchors")
    monkeypatch.setenv("BOATRACE_ANCHOR_GITHUB_TOKEN", "secret-not-returned")
    base = {
        "transportMode": "branch_path_commit",
        "owner": "sasaki202020",
        "repository": "boatrace-prediction-anchors",
        "branch": "main",
        "allowedPathPrefix": "anchors/synthetic/",
        "allowedRecordTypes": ["synthetic_anchor"],
        "credentialEnvironmentVariable": "BOATRACE_ANCHOR_GITHUB_TOKEN",
        "transportModeIssue": False,
        "repositoryAllowlist": ["sasaki202020/boatrace-prediction-anchors"],
        "humanApproved": True,
        "syntheticPublishApproved": True,
        "realPredictionPublishApproved": False,
    }
    audit = github_runtime_audit(base)
    assert audit["configuredForSyntheticPublish"] is True
    assert "secret-not-returned" not in str(audit)
    assert github_runtime_audit({**base, "repositoryAllowlist": []})["configuredForSyntheticPublish"] is False
    assert github_runtime_audit({**base, "repositoryAllowlist": ["sasaki202020/boatrace-prediction-anchors", "other/repo"]})["configuredForSyntheticPublish"] is False
    assert github_runtime_audit({**base, "realPredictionPublishApproved": True})["configuredForSyntheticPublish"] is False
