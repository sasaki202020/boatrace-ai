from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import verify_release, verify_review_register


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _register_entry(path: str, sha256: str) -> dict:
    return {
        "path": path,
        "kind": "file",
        "required": True,
        "owner": "release-readiness",
        "validation": "sha256",
        "hashPolicy": "sha256",
        "sourceType": "canonical_source",
        "sha256": sha256,
    }


def test_review_register_rejects_hash_mismatch_and_duplicate_entries(tmp_path: Path) -> None:
    document = tmp_path / "docs" / "canonical.md"
    document.parent.mkdir(parents=True)
    document.write_text("canonical\n", encoding="utf-8")
    register_path = tmp_path / "docs" / "review_register.json"
    entry = _register_entry("docs/canonical.md", _sha256(document))
    _write_json(
        register_path,
        {
            "schemaVersion": 1,
            "canonicalCommit": "0" * 40,
            "entries": [entry],
        },
    )

    assert verify_review_register.verify_register(tmp_path, register_path)["status"] == "PASS"

    document.write_text("changed\n", encoding="utf-8")
    mismatch = verify_review_register.verify_register(tmp_path, register_path)
    assert mismatch["status"] == "FAIL"
    assert any(item["code"] == "sha256_mismatch" for item in mismatch["errors"])

    _write_json(
        register_path,
        {
            "schemaVersion": 1,
            "canonicalCommit": "0" * 40,
            "entries": [entry, entry],
        },
    )
    duplicate = verify_review_register.verify_register(tmp_path, register_path)
    assert duplicate["status"] == "FAIL"
    assert any(item["code"] == "duplicate_path" for item in duplicate["errors"])


def test_review_register_requires_minimum_count_for_globs(tmp_path: Path) -> None:
    register_path = tmp_path / "docs" / "review_register.json"
    _write_json(
        register_path,
        {
            "schemaVersion": 1,
            "canonicalCommit": "0" * 40,
            "entries": [
                {
                    "path": "docs/*.md",
                    "kind": "glob",
                    "required": True,
                    "owner": "release-readiness",
                    "validation": "exists",
                    "hashPolicy": "none",
                    "sourceType": "generated_evidence",
                }
            ],
        },
    )

    result = verify_review_register.verify_register(tmp_path, register_path)

    assert result["status"] == "FAIL"
    assert any(item["code"] == "glob_expected_min_count_missing" for item in result["errors"])


def test_runtime_lock_rejects_missing_or_changed_runtime_source(tmp_path: Path) -> None:
    runtime_file = tmp_path / "src" / "runtime.py"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("VERSION = 1\n", encoding="utf-8")
    lock_path = tmp_path / "config" / "runtime_lock.json"
    _write_json(
        lock_path,
        {
            "schemaVersion": 1,
            "canonicalCommit": "0" * 40,
            "entries": [
                {
                    "path": "src/runtime.py",
                    "sha256": _sha256(runtime_file),
                    "sourceRepository": "local/test",
                    "sourceCommit": "0" * 40,
                    "adoptedAtUtc": "2026-08-16T00:00:00Z",
                    "mutable": False,
                }
            ],
        },
    )

    assert verify_release.verify_runtime_lock(tmp_path, lock_path)["status"] == "PASS"

    runtime_file.write_text("VERSION = 2\n", encoding="utf-8")
    changed = verify_release.verify_runtime_lock(tmp_path, lock_path)
    assert changed["status"] == "FAIL"
    assert any(item["code"] == "runtime_sha256_mismatch" for item in changed["errors"])

    runtime_file.unlink()
    missing = verify_release.verify_runtime_lock(tmp_path, lock_path)
    assert missing["status"] == "FAIL"
    assert any(item["code"] == "runtime_source_missing" for item in missing["errors"])


def test_forbidden_references_detect_absolute_and_recovery_paths(tmp_path: Path) -> None:
    source = tmp_path / "docs" / "bad.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "C:/" + "Users/example\n"
        + "file:///" + "C:/example\n"
        + "C:" + "\\\\Users\\\\example\\\\" + "競艇" + "-recovery\\n",
        encoding="utf-8",
    )

    findings = verify_release.find_forbidden_references(tmp_path, [source])

    assert {item["code"] for item in findings} >= {
        "absolute_windows_path",
        "file_url_path",
        "recovery_clone_reference",
    }


def test_forbidden_references_include_untracked_source_files(tmp_path: Path) -> None:
    source = tmp_path / "new_source.py"
    absolute_path = "C:" + "/" + "Users/example"
    source.write_text(f'ROOT = "{absolute_path}"\n', encoding="utf-8")

    findings = verify_release.find_forbidden_references(tmp_path)

    assert findings == [
        {
            "code": "absolute_windows_path",
            "path": "new_source.py",
            "detail": "line=1",
        }
    ]


def test_release_status_preserves_blocked_instead_of_passing() -> None:
    assert verify_release.release_status([{"status": "PASS"}]) == ("PASS", 0)
    assert verify_release.release_status([{"status": "BLOCKED"}, {"status": "PASS"}]) == (
        "BLOCKED",
        2,
    )
    assert verify_release.release_status([{"status": "FAIL"}, {"status": "BLOCKED"}]) == (
        "FAIL",
        1,
    )
