from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.feature_forward_v1.runtime_sync import (
    _valid_k_file,
    copy_validated_append_only,
    sync_runtime_official_inputs,
)


def _bfile(*, race_count: int = 1, jcd: str = "01") -> bytes:
    lines = [b"STARTB", f"{jcd}BBGN".encode("ascii")]
    for race_no in range(1, race_count + 1):
        lines.append(
            f"{race_no}R 電話投票締切予定12:34".encode("cp932")
        )
        for lane in range(1, 7):
            lines.append(
                f"{lane} {4000 + lane:04d}".encode("ascii") + b" " * 65
            )
    lines.append(b"END")
    return b"\r\n".join(lines)


def _kfile(*, race_count: int, completed_count: int | None = None) -> bytes:
    completed_count = race_count if completed_count is None else completed_count
    lines = ["01KBGN"]
    for race_no in range(1, race_count + 1):
        lines.append(f"{race_no}R 一般 H1800m 晴 風 東 2m 波 2cm")
        if race_no <= completed_count:
            lines.append("３連単 １－２－３ 1,000 1人気")
    lines.append("01KEND")
    return "\r\n".join(lines).encode("cp932")


def _not_held_kfile(*, jcd: str = "09") -> bytes:
    return f"{jcd}KBGN\r\n{jcd}KEND".encode("cp932")


def test_runtime_sync_copies_missing_file_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source" / "B260729.TXT"
    destination = tmp_path / "runtime" / "B260729.TXT"
    source.parent.mkdir()
    source.write_bytes(b"official-input")

    first = copy_validated_append_only(
        source=source,
        destination=destination,
        validator=lambda path: path.read_bytes() == b"official-input",
    )
    second = copy_validated_append_only(
        source=source,
        destination=destination,
        validator=lambda path: path.read_bytes() == b"official-input",
    )

    assert first["status"] == "COPIED"
    assert second["status"] == "EXISTING"
    assert destination.read_bytes() == b"official-input"
    assert first["sha256"] == second["sha256"]


def test_runtime_sync_rejects_invalid_source_and_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source" / "K260728.TXT"
    destination = tmp_path / "runtime" / "K260728.TXT"
    source.parent.mkdir()
    destination.parent.mkdir()
    source.write_bytes(b"placeholder")

    with pytest.raises(ValueError, match="official_input_invalid"):
        copy_validated_append_only(
            source=source,
            destination=destination,
            validator=lambda _path: False,
        )

    source.write_bytes(b"complete-result")
    destination.write_bytes(b"different-result")
    with pytest.raises(ValueError, match="official_input_conflict"):
        copy_validated_append_only(
            source=source,
            destination=destination,
            validator=lambda _path: True,
        )


def test_runtime_sync_rejects_partial_k_result_file(tmp_path: Path) -> None:
    entry_root = tmp_path / "entries"
    result_root = tmp_path / "results"
    entry_root.mkdir()
    result_root.mkdir()
    (entry_root / "B260101.TXT").write_bytes(_bfile(race_count=12))
    partial = result_root / "K260101.TXT"
    partial.write_bytes(_kfile(race_count=1))

    assert not _valid_k_file(
        partial,
        entry_root=entry_root,
        today=date(2026, 1, 2),
    )

    partial.write_bytes(_kfile(race_count=12))
    assert _valid_k_file(
        partial,
        entry_root=entry_root,
        today=date(2026, 1, 2),
    )

    partial.write_bytes(_kfile(race_count=12, completed_count=1))
    assert not _valid_k_file(
        partial,
        entry_root=entry_root,
        today=date(2026, 1, 2),
    )


def test_runtime_sync_accepts_explicit_not_held_venue(tmp_path: Path) -> None:
    entry_root = tmp_path / "entries"
    result_root = tmp_path / "results"
    entry_root.mkdir()
    result_root.mkdir()
    (entry_root / "B260101.TXT").write_bytes(_bfile(race_count=12, jcd="09"))
    not_held = result_root / "K260101.TXT"
    not_held.write_bytes(_not_held_kfile())

    assert _valid_k_file(
        not_held,
        entry_root=entry_root,
        today=date(2026, 1, 2),
    )


def test_runtime_sync_reports_missing_source_directory(tmp_path: Path) -> None:
    report = sync_runtime_official_inputs(
        runtime_root=tmp_path / "runtime",
        entry_sources=[tmp_path / "missing-entries"],
        result_sources=[],
        minimum_token="260101",
    )

    assert report["sourceErrors"] == [
        {
            "source": str(tmp_path / "missing-entries"),
            "reason": "SOURCE_DIRECTORY_MISSING",
        }
    ]


def test_runtime_sync_uses_valid_fallback_source(tmp_path: Path) -> None:
    invalid_root = tmp_path / "invalid"
    valid_root = tmp_path / "valid"
    invalid_root.mkdir()
    valid_root.mkdir()
    (invalid_root / "B260101.TXT").write_bytes(b"placeholder")
    valid_bytes = _bfile()
    (valid_root / "B260101.TXT").write_bytes(valid_bytes)

    report = sync_runtime_official_inputs(
        runtime_root=tmp_path / "runtime",
        entry_sources=[invalid_root, valid_root],
        result_sources=[],
        minimum_token="260101",
    )

    copied = (
        tmp_path
        / "runtime"
        / "data/raw/official/entries/B260101.TXT"
    )
    assert copied.read_bytes() == valid_bytes
    assert report["copied"] == 1
    assert report["rejected"] == []


def test_runtime_sync_copies_the_exact_validated_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source" / "B260101.TXT"
    destination = tmp_path / "runtime" / "B260101.TXT"
    source.parent.mkdir()
    source.write_bytes(b"validated")

    def validate_snapshot(snapshot: Path) -> bool:
        valid = snapshot.read_bytes() == b"validated"
        source.write_bytes(b"changed-after-read")
        return valid

    copy_validated_append_only(
        source=source,
        destination=destination,
        validator=validate_snapshot,
    )

    assert destination.read_bytes() == b"validated"
