from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
        ("status", "--porcelain=v1"): " M src/example.py\n?? src/untracked.py\n",
        ("rev-parse", "HEAD"): "a" * 40 + "\n",
        ("diff", "--binary"): "diff --git a/src/example.py b/src/example.py\n",
        ("ls-files", "--others", "--exclude-standard"): "src/untracked.py\n",
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
        " M src/example.py",
        "?? src/untracked.py",
    ]
    assert manifest["trackedDiffSha256"] == hashlib.sha256(
        responses[("diff", "--binary")].encode("utf-8")
    ).hexdigest()
    assert manifest["untrackedFiles"] == ["src/untracked.py"]
    assert manifest["oofSpecSha256"] == hashlib.sha256(spec.read_bytes()).hexdigest()
    assert manifest["productionAdoptionAllowed"] is False
    assert manifest["oofExecuted"] is False
    assert json.loads(
        (report_root / "oof_reproducibility_manifest.json").read_text(encoding="utf-8")
    ) == manifest
    assert (report_root / "oof_reproducibility.patch").read_bytes() == responses[
        ("diff", "--binary")
    ].encode("utf-8")


def test_reproducibility_manifest_cli_reports_manifest_fields(
    monkeypatch,
    capsys,
) -> None:
    manifest = {
        "gitHead": "a" * 40,
        "dirtyWorktree": True,
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
        "trackedDiffSha256": "b" * 64,
        "untrackedManifestSha256": "c" * 64,
        "configSha256": "d" * 64,
    }
