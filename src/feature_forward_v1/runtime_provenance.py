from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


class RuntimeProvenanceError(RuntimeError):
    """Raised when the source attestation cannot be generated safely."""


TREE15_SHA256 = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
SOURCE_ROOTS = (
    "scripts/run_live_feature_capture_v1.py",
    "src/feature_forward_v1",
    "src/commercialization_v2",
    "config/feature_forward_v1",
)
SOURCE_SUFFIXES = {".json", ".py", ".toml", ".yaml", ".yml"}
EXCLUDED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "backups",
    "data",
    "logs",
    "models",
    "reports",
}


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeProvenanceError(f"git_probe_failed:{args[0]}") from exc
    return result.stdout


def _git_commit(root: Path) -> str:
    value = _git_bytes(root, "rev-parse", "HEAD").decode("ascii").strip()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise RuntimeProvenanceError("git_commit_invalid")
    return value


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_source_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES


def _iter_source_files(root: Path, source_roots: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for raw_root in source_roots:
        path = (root / raw_root).resolve()
        if not path.exists():
            raise RuntimeProvenanceError(f"runtime_source_missing:{raw_root}")
        if path.is_file():
            if _is_source_file(path):
                files.add(path)
            continue
        for candidate in path.rglob("*"):
            if any(part in EXCLUDED_PARTS for part in candidate.parts):
                continue
            if _is_source_file(candidate):
                files.add(candidate.resolve())
    return sorted(files, key=lambda item: _relative_path(root, item))


def _git_status_entries(root: Path) -> tuple[bool, set[str]]:
    raw = _git_bytes(root, "status", "--porcelain=v1", "-z")
    untracked: set[str] = set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        text = entry.decode("utf-8", errors="surrogateescape")
        if text.startswith("?? "):
            untracked.add(text[3:])
    return bool(raw), untracked


def _hash_source_entries(entries: list[dict[str, str]]) -> str:
    payload = b"".join(
        f"{entry['path']}\0{entry['sha256']}\n".encode("utf-8")
        for entry in entries
    )
    return _sha256_bytes(payload)


def _resolve_config_path(root: Path, gate_config_path: Path) -> Path | None:
    if not gate_config_path.is_file():
        return None
    try:
        gate = json.loads(gate_config_path.read_text(encoding="utf-8"))
        raw_path = gate["configPath"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeProvenanceError("runtime_gate_config_invalid") from exc
    path = Path(raw_path)
    return (path if path.is_absolute() else root / path).resolve()


def build_runtime_provenance(
    root: Path,
    *,
    gate_config_path: Path,
    policy_path: Path,
) -> dict[str, object]:
    """Build source attestation without touching runtime data stores."""

    root = Path(root).resolve()
    gate_config_path = Path(gate_config_path)
    policy_path = Path(policy_path)
    gate_config_path = (gate_config_path if gate_config_path.is_absolute() else root / gate_config_path).resolve()
    policy_path = (policy_path if policy_path.is_absolute() else root / policy_path).resolve()
    source_files = _iter_source_files(root, SOURCE_ROOTS)
    git_dirty, untracked_paths = _git_status_entries(root)
    tracked_diff_hash = _sha256_bytes(_git_bytes(root, "diff", "--binary"))
    staged_diff_hash = _sha256_bytes(_git_bytes(root, "diff", "--cached", "--binary"))

    entries = [
        {
            "path": _relative_path(root, path),
            "sha256": _sha256_file(path),
            "gitState": "untracked" if _relative_path(root, path) in untracked_paths else "tracked_or_modified",
        }
        for path in source_files
    ]
    untracked_entries = [entry for entry in entries if entry["gitState"] == "untracked"]
    config_path = _resolve_config_path(root, gate_config_path)
    for required_path, label in ((policy_path, "source_policy"),):
        if not required_path.is_file():
            raise RuntimeProvenanceError(f"{label}_missing")
    if config_path is not None and not config_path.is_file():
        raise RuntimeProvenanceError("runtime_config_missing")
    protocol_path = root / "docs/feature_forward_v1/OOF_DECISION_PROTOCOL.md"
    if not protocol_path.is_file():
        raise RuntimeProvenanceError("protocol_missing")

    summary = {
        "provenanceSchemaVersion": 1,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "gitCommit": _git_commit(root),
        "gitDirty": git_dirty,
        "runtimeSourceHash": _hash_source_entries(entries),
        "runtimeSourceFileCount": len(entries),
        "trackedDiffHash": tracked_diff_hash,
        "stagedDiffHash": staged_diff_hash,
        "untrackedRuntimeSourceHash": _hash_source_entries(untracked_entries),
        "untrackedRuntimeSourceFileCount": len(untracked_entries),
        "runtimeSourceRoots": list(SOURCE_ROOTS),
        "runtimeSourceManifestHash": _sha256_bytes(
            json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "policyHash": _sha256_file(policy_path),
        "configHash": _sha256_file(config_path) if config_path else None,
        "protocolHash": _sha256_file(protocol_path),
        "tree15Hash": TREE15_SHA256,
    }
    return {"provenance": summary, "sourceFiles": entries}
