from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "feature_forward"
SPEC_PATH = ROOT / "config" / "feature_forward_v1" / "oof_evaluation_spec.json"
PATCH_PATH = REPORT_ROOT / "oof_reproducibility.patch"
MANIFEST_PATH = REPORT_ROOT / "oof_reproducibility_manifest.json"


def _run(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_reproducibility_manifest(
    *,
    root: Path,
    spec_path: Path,
    tracked_diff_path: str,
) -> tuple[dict[str, Any], bytes]:
    status = _run(root, "status", "--porcelain=v1")
    head = _run(root, "rev-parse", "HEAD").strip()
    tracked_diff = _run(root, "diff", "--binary")
    untracked = _run(root, "ls-files", "--others", "--exclude-standard")
    patch_bytes = tracked_diff.encode("utf-8")
    untracked_names = sorted(line for line in untracked.splitlines() if line)
    config_bytes = spec_path.read_bytes()
    spec_relative_path = _relative_path(spec_path, root)
    manifest = {
        "schemaVersion": 1,
        "artifactType": "OOF_REPRODUCIBILITY_MANIFEST",
        "gitHead": head,
        "gitStatusPorcelain": status.splitlines(),
        "dirtyWorktree": bool(status.strip()),
        "trackedDiffPath": tracked_diff_path,
        "trackedDiffSha256": _sha256_bytes(patch_bytes),
        "untrackedFiles": untracked_names,
        "untrackedManifestSha256": _sha256_bytes(
            ("\n".join(untracked_names) + "\n").encode("utf-8")
            if untracked_names else b""
        ),
        "configPath": spec_relative_path,
        "configSha256": _sha256_bytes(config_bytes),
        "oofSpecPath": spec_relative_path,
        "oofSpecSha256": _sha256_bytes(config_bytes),
        "productionAdoptionAllowed": False,
        "oofExecuted": False,
        "note": "Patch captures tracked dirty diff only; untracked content is represented by manifest names and hash.",
    }
    return manifest, patch_bytes


def write_reproducibility_manifest(
    *,
    root: Path = ROOT,
    report_root: Path = REPORT_ROOT,
    spec_path: Path = SPEC_PATH,
) -> dict[str, Any]:
    report_root.mkdir(parents=True, exist_ok=True)
    patch_path = report_root / "oof_reproducibility.patch"
    manifest_path = report_root / "oof_reproducibility_manifest.json"
    manifest, patch_bytes = build_reproducibility_manifest(
        root=root,
        spec_path=spec_path,
        tracked_diff_path=_relative_path(patch_path, root),
    )
    patch_path.write_bytes(patch_bytes)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    manifest = write_reproducibility_manifest()
    print(json.dumps({
        "status": "REPRODUCIBILITY_MANIFEST_WRITTEN",
        "gitHead": manifest["gitHead"],
        "dirtyWorktree": manifest["dirtyWorktree"],
        "trackedDiffSha256": manifest["trackedDiffSha256"],
        "untrackedManifestSha256": manifest["untrackedManifestSha256"],
        "configSha256": manifest["configSha256"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
