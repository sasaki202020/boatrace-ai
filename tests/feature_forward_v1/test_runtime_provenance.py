from __future__ import annotations

import hashlib

from src.feature_forward_v1.runtime_provenance import (
    _hash_source_entries,
    _iter_source_files,
)


def _entries(root, paths):
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in _iter_source_files(root, paths)
    ]


def test_runtime_source_hash_changes_for_runtime_source_and_ignores_data_reports(tmp_path):
    source_root = tmp_path / "src" / "feature_forward_v1"
    source_root.mkdir(parents=True)
    (source_root / "collector.py").write_text("VERSION = 1\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "data" / "runtime.py").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "reports" / "report.py").write_text("ignored\n", encoding="utf-8")

    paths = ("src/feature_forward_v1",)
    first = _entries(tmp_path, paths)
    first_hash = _hash_source_entries(first)

    (tmp_path / "data" / "runtime.py").write_text("changed\n", encoding="utf-8")
    (tmp_path / "reports" / "report.py").write_text("changed\n", encoding="utf-8")
    assert _hash_source_entries(_entries(tmp_path, paths)) == first_hash

    (source_root / "untracked.py").write_text("VERSION = 2\n", encoding="utf-8")
    assert _hash_source_entries(_entries(tmp_path, paths)) != first_hash
