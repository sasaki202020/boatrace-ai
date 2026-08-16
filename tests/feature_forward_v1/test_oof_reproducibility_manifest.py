from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_oof_reproducibility_manifest_v1 as manifest_builder


def test_reproducibility_manifest_captures_git_state_patch_and_spec(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    spec = repo / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"
    spec.parent.mkdir(parents=True)
    spec.write_text('{"fixed":true}\n', encoding="utf-8")
    report_root = repo / "reports" / "feature_forward"
    responses = {
        ("status", "--porcelain=v1"): " M reports/example.json\n?? reports/untracked.json\n",
        (
            "status", "--porcelain=v1", "--untracked-files=all", "--",
            "src", "scripts", "config",
        ): "",
        ("rev-parse", "HEAD"): "a" * 40 + "\n",
        ("diff", "--binary"): "diff --git a/reports/example.json b/reports/example.json\n",
        ("ls-files", "--others", "--exclude-standard"): "reports/untracked.json\n",
    }

    monkeypatch.setattr(
        manifest_builder,
        "_run",
        lambda root, *args: responses[args],
    )

    manifest = manifest_builder.write_reproducibility_manifest(
        root=repo,
        report_root=report_root,
        spec_path=spec,
    )

    assert manifest["gitHead"] == "a" * 40
    assert manifest["gitStatusPorcelain"] == [
        " M reports/example.json",
        "?? reports/untracked.json",
    ]
    assert manifest["trackedDiffSha256"] == hashlib.sha256(
        responses[("diff", "--binary")].encode("utf-8")
    ).hexdigest()
    assert manifest["untrackedFiles"] == ["reports/untracked.json"]
    assert manifest["sourceWorktreeClean"] is True
    assert manifest["oofSpecSha256"] == hashlib.sha256(spec.read_bytes()).hexdigest()
    assert manifest["productionAdoptionAllowed"] is False
    assert manifest["oofExecuted"] is False
    assert json.loads(
        (report_root / "oof_reproducibility_manifest.json").read_text(encoding="utf-8")
    ) == manifest
    assert (report_root / "oof_reproducibility.patch").read_bytes() == responses[
        ("diff", "--binary")
    ].encode("utf-8")


@pytest.mark.parametrize(
    "source_status",
    [
        " M src/feature_forward_v1/course_start_challenger.py\n",
        "M  scripts/run_course_start_challenger_v1.py\n",
        "?? scripts/untracked_oof_logic.py\n",
        "?? config/feature_forward_v1/untracked_spec.json\n",
    ],
)
def test_reproducibility_manifest_rejects_dirty_source_worktree(
    tmp_path: Path,
    monkeypatch,
    source_status: str,
) -> None:
    repo = tmp_path / "repo"
    spec = repo / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"
    spec.parent.mkdir(parents=True)
    spec.write_text('{"fixed":true}\n', encoding="utf-8")

    def fake_run(root: Path, *args: str) -> str:
        if args == (
            "status", "--porcelain=v1", "--untracked-files=all", "--",
            "src", "scripts", "config",
        ):
            return source_status
        raise AssertionError(f"git command must not run after dirty source detection: {args}")

    monkeypatch.setattr(manifest_builder, "_run", fake_run)

    with pytest.raises(ValueError, match="dirty_source_worktree"):
        manifest_builder.write_reproducibility_manifest(
            root=repo,
            report_root=repo / "reports" / "feature_forward",
            spec_path=spec,
        )


def test_reproducibility_manifest_allows_generated_reports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    spec = repo / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"
    spec.parent.mkdir(parents=True)
    spec.write_text('{"fixed":true}\n', encoding="utf-8")
    responses = {
        (
            "status", "--porcelain=v1", "--untracked-files=all", "--",
            "src", "scripts", "config",
        ): "",
        ("status", "--porcelain=v1"): " M reports/feature_forward/readiness.json\n",
        ("rev-parse", "HEAD"): "a" * 40 + "\n",
        ("diff", "--binary"): "diff --git a/reports/feature_forward/readiness.json b/reports/feature_forward/readiness.json\n",
        ("ls-files", "--others", "--exclude-standard"): "reports/feature_forward/generated.json\n",
    }
    monkeypatch.setattr(manifest_builder, "_run", lambda root, *args: responses[args])

    manifest = manifest_builder.write_reproducibility_manifest(
        root=repo,
        report_root=repo / "reports" / "feature_forward",
        spec_path=spec,
    )

    assert manifest["sourceWorktreeClean"] is True
    assert manifest["dirtyWorktree"] is True


def test_reproducibility_manifest_cli_reports_manifest_fields(
    monkeypatch,
    capsys,
) -> None:
    manifest = {
        "gitHead": "a" * 40,
        "dirtyWorktree": True,
        "sourceWorktreeClean": True,
        "trackedDiffSha256": "b" * 64,
        "untrackedManifestSha256": "c" * 64,
        "configSha256": "d" * 64,
    }
    monkeypatch.setattr(
        manifest_builder,
        "write_reproducibility_manifest",
        lambda: manifest,
    )

    assert manifest_builder.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result == {
        "status": "REPRODUCIBILITY_MANIFEST_WRITTEN",
        "gitHead": "a" * 40,
        "dirtyWorktree": True,
        "sourceWorktreeClean": True,
        "trackedDiffSha256": "b" * 64,
        "untrackedManifestSha256": "c" * 64,
        "configSha256": "d" * 64,
    }
