from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Iterable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.commercialization_v2.day1_readiness import validate_runtime_bfile
from src.ingest.parsers.official_k_result_parser import (
    parse_official_k_result_file,
)

Validator = Callable[[Path], bool]
JST = ZoneInfo("Asia/Tokyo")
MIN_FINAL_RESULT_COVERAGE = 0.95


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_validated_bytes(
    source: Path,
    validator: Validator,
) -> tuple[bytes, str]:
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"official_input_invalid:{source.name}") from exc
    with tempfile.TemporaryDirectory(prefix="boatrace-input-") as temp:
        snapshot = Path(temp) / source.name
        snapshot.write_bytes(raw)
        if not validator(snapshot):
            raise ValueError(f"official_input_invalid:{source.name}")
    return raw, hashlib.sha256(raw).hexdigest()


def copy_validated_append_only(
    *,
    source: Path,
    destination: Path,
    validator: Validator,
) -> dict[str, str]:
    raw, source_hash = _read_validated_bytes(source, validator)
    if destination.exists():
        if _sha256(destination) != source_hash:
            raise ValueError(f"official_input_conflict:{destination.name}")
        return {
            "fileName": destination.name,
            "status": "EXISTING",
            "sha256": source_hash,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as output:
            output.write(raw)
    except FileExistsError:
        if _sha256(destination) != source_hash:
            raise ValueError(f"official_input_conflict:{destination.name}") from None
        status = "EXISTING"
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    else:
        status = "COPIED"

    if _sha256(destination) != source_hash:
        destination.unlink(missing_ok=True)
        raise ValueError(f"official_input_hash_mismatch:{destination.name}")
    return {
        "fileName": destination.name,
        "status": status,
        "sha256": source_hash,
    }


def _valid_b_file(path: Path) -> bool:
    try:
        frame = validate_runtime_bfile(path)
    except Exception:
        return False
    return not frame.empty and frame["race_id"].nunique() > 0


def _valid_k_file(
    path: Path,
    *,
    entry_root: Path,
    today: date | None = None,
) -> bool:
    token = path.stem[1:]
    if len(token) != 6 or not token.isdigit():
        return False
    try:
        result_date = date(
            2000 + int(token[:2]),
            int(token[2:4]),
            int(token[4:]),
        )
    except ValueError:
        return False
    if result_date >= (today or datetime.now(JST).date()):
        return False
    b_file = entry_root / f"B{token}.TXT"
    try:
        expected_frame = validate_runtime_bfile(b_file)
        parsed = parse_official_k_result_file(path)
    except (OSError, ValueError):
        return False

    expected = {
        (str(row.jcd).zfill(2), int(row.race_no))
        for row in expected_frame[["jcd", "race_no"]]
        .drop_duplicates()
        .itertuples()
    }
    result_rows = parsed.get("races", [])
    terminal_rows = [
        row
        for row in result_rows
        if str(row.get("raceStatus") or "").lower()
        in {"not_held", "canceled", "refund"}
        and row.get("raceNo") is None
    ]
    terminal_venues = {
        str(row.get("jcd") or "").zfill(2)
        for row in terminal_rows
        if row.get("jcd") is not None
    }
    actual = {
        (str(row.get("jcd")).zfill(2), int(row["raceNo"]))
        for row in result_rows
        if row.get("raceNo") is not None
    }
    if (
        not expected
        or (not actual and not terminal_rows)
        or len(actual) + len(terminal_rows) != len(result_rows)
        or (
            int(parsed.get("resultTxtOkCount", 0)) <= 0
            and not terminal_rows
        )
        or not actual.issubset(expected)
        or bool(terminal_venues & {venue for venue, _ in actual})
    ):
        return False
    incomplete_statuses = {
        "",
        "missing",
        "pending",
        "unavailable",
        "parse_error",
    }
    if any(
        (
            str(row.get("raceStatus") or "").lower() in incomplete_statuses
            or (
                str(row.get("raceStatus") or "").lower() == "not_held"
                and row.get("raceNo") is not None
            )
        )
        for row in result_rows
    ):
        return False

    expected_venues = {venue for venue, _ in expected}
    covered_venues = {venue for venue, _ in actual} | (
        terminal_venues & expected_venues
    )
    if covered_venues != expected_venues:
        return False
    for venue in expected_venues:
        if venue in terminal_venues:
            continue
        expected_races = sorted(race for code, race in expected if code == venue)
        actual_races = sorted(race for code, race in actual if code == venue)
        if actual_races != expected_races[: len(actual_races)]:
            return False
    resolved_count = len(actual) + sum(
        1 for venue, _ in expected if venue in terminal_venues
    )
    return resolved_count / len(expected) >= MIN_FINAL_RESULT_COVERAGE


def _eligible_files(
    roots: Iterable[Path],
    pattern: str,
    *,
    minimum_token: str,
) -> dict[str, list[Path]]:
    selected: dict[str, list[Path]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob(pattern)):
            token = path.stem[1:]
            if len(token) != 6 or not token.isdigit() or token < minimum_token:
                continue
            selected.setdefault(path.name.upper(), []).append(path)
    return {name: selected[name] for name in sorted(selected)}


def _select_valid_source(
    *,
    candidates: list[Path],
    validator: Validator,
) -> Path | None:
    valid = [path for path in candidates if validator(path)]
    if not valid:
        return None
    hashes = {_sha256(path) for path in valid}
    if len(hashes) != 1:
        raise ValueError(f"official_source_conflict:{valid[0].name}")
    return valid[0]


def sync_runtime_official_inputs(
    *,
    runtime_root: Path,
    entry_sources: Iterable[Path],
    result_sources: Iterable[Path],
    minimum_token: str,
) -> dict[str, object]:
    copied = existing = 0
    rejected: list[dict[str, str]] = []
    records: list[dict[str, str]] = []
    entry_sources = list(entry_sources)
    result_sources = list(result_sources)
    source_errors = [
        {"source": str(root), "reason": "SOURCE_DIRECTORY_MISSING"}
        for root in [*entry_sources, *result_sources]
        if not root.is_dir()
    ]
    entry_root = runtime_root / "data/raw/official/entries"

    jobs = [
        (
            _eligible_files(
                entry_sources,
                "B*.TXT",
                minimum_token=minimum_token,
            ),
            entry_root,
            _valid_b_file,
        ),
        (
            _eligible_files(
                result_sources,
                "K*.TXT",
                minimum_token=minimum_token,
            ),
            runtime_root / "data/raw/official/results",
            lambda path: _valid_k_file(path, entry_root=entry_root),
        ),
    ]
    for source_groups, destination_root, validator in jobs:
        for file_name, candidates in source_groups.items():
            source = _select_valid_source(
                candidates=candidates,
                validator=validator,
            )
            if source is None:
                rejected.append(
                    {
                        "fileName": file_name,
                        "reason": f"official_input_invalid:{file_name}",
                    }
                )
                continue
            try:
                record = copy_validated_append_only(
                    source=source,
                    destination=destination_root / file_name,
                    validator=validator,
                )
            except ValueError as exc:
                if str(exc).startswith("official_input_invalid:"):
                    rejected.append(
                        {"fileName": source.name, "reason": str(exc)}
                    )
                    continue
                raise
            records.append(record)
            if record["status"] == "COPIED":
                copied += 1
            else:
                existing += 1
    return {
        "copied": copied,
        "existing": existing,
        "rejected": rejected,
        "sourceErrors": source_errors,
        "records": records,
    }
