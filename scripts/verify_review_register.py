from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HEX = set("0123456789abcdef")
REQUIRED_ENTRY_KEYS = {
    "path",
    "kind",
    "required",
    "owner",
    "validation",
    "hashPolicy",
    "sourceType",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _error(code: str, path: str | None = None, detail: str | None = None) -> dict[str, str]:
    result = {"code": code}
    if path is not None:
        result["path"] = path
    if detail is not None:
        result["detail"] = detail
    return result


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in HEX for character in value.lower())
    )


def _safe_relative_path(root: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _load_register(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, [_error("review_register_unreadable", str(path))]
    if not isinstance(payload, dict):
        return None, [_error("review_register_schema_invalid", str(path))]
    return payload, []


def verify_register(root: Path, register_path: Path | None = None) -> dict[str, Any]:
    """Validate the static review register without running a pipeline."""

    root = Path(root).resolve()
    register_path = Path(register_path or root / "docs/review_register.json")
    if not register_path.is_absolute():
        register_path = (root / register_path).resolve()
    payload, errors = _load_register(register_path)
    if payload is None:
        return {
            "schemaVersion": 1,
            "status": "FAIL",
            "registerPath": str(register_path),
            "checkedEntryCount": 0,
            "errors": errors,
        }

    if payload.get("schemaVersion") != 1:
        errors.append(_error("review_register_schema_version_invalid"))
    if not _is_hex(payload.get("canonicalCommit"), 40):
        errors.append(_error("canonical_commit_invalid"))
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append(_error("review_register_entries_invalid"))
        entries = []

    seen_paths: set[str] = set()
    checked_paths: list[str] = []
    pending_evidence: list[dict[str, str]] = []
    for index, entry in enumerate(entries):
        location = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(_error("review_register_entry_invalid", location))
            continue
        missing = sorted(REQUIRED_ENTRY_KEYS - set(entry))
        if missing:
            errors.append(_error("review_register_entry_fields_missing", location, ",".join(missing)))
            continue
        raw_path = entry["path"]
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(_error("review_register_entry_path_invalid", location))
            continue
        if raw_path in seen_paths:
            errors.append(_error("duplicate_path", raw_path))
            continue
        seen_paths.add(raw_path)
        kind = entry["kind"]
        required = entry["required"]
        validation = entry["validation"]
        hash_policy = entry["hashPolicy"]
        if kind not in {"file", "glob"}:
            errors.append(_error("review_register_kind_invalid", raw_path))
            continue
        if type(required) is not bool:
            errors.append(_error("review_register_required_invalid", raw_path))
            continue
        if not isinstance(entry["owner"], str) or not entry["owner"]:
            errors.append(_error("review_register_owner_invalid", raw_path))
            continue
        if validation not in {"sha256", "exists", "generated_evidence"}:
            errors.append(_error("review_register_validation_invalid", raw_path))
            continue
        if hash_policy not in {"sha256", "none"}:
            errors.append(_error("review_register_hash_policy_invalid", raw_path))
            continue
        if not isinstance(entry["sourceType"], str) or not entry["sourceType"]:
            errors.append(_error("review_register_source_type_invalid", raw_path))
            continue
        evidence_fields = entry.get("evidenceFields")
        if validation == "generated_evidence" and (
            not isinstance(evidence_fields, list)
            or not evidence_fields
            or any(not isinstance(field, str) or not field for field in evidence_fields)
        ):
            errors.append(_error("generated_evidence_fields_invalid", raw_path))
            continue

        if kind == "glob":
            expected_minimum = entry.get("expectedMinCount")
            if type(expected_minimum) is not int or expected_minimum < 1:
                errors.append(_error("glob_expected_min_count_missing", raw_path))
                continue
            if _safe_relative_path(root, raw_path.replace("*", "placeholder")) is None:
                errors.append(_error("review_register_path_outside_root", raw_path))
                continue
            matches = sorted(path for path in root.glob(raw_path) if path.is_file())
            if len(matches) < expected_minimum:
                finding = _error(
                    "glob_expected_min_count_not_met",
                    raw_path,
                    f"expected={expected_minimum},actual={len(matches)}",
                )
                if required:
                    errors.append(finding)
                else:
                    pending_evidence.append(finding)
            checked_paths.extend(path.relative_to(root).as_posix() for path in matches)
            continue

        path = _safe_relative_path(root, raw_path)
        if path is None:
            errors.append(_error("review_register_path_outside_root", raw_path))
            continue
        if not path.is_file():
            if required:
                errors.append(_error("required_path_missing", raw_path))
            elif validation == "generated_evidence":
                pending_evidence.append(_error("generated_evidence_missing", raw_path))
            continue
        checked_paths.append(raw_path)
        if validation == "generated_evidence":
            try:
                evidence = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                pending_evidence.append(_error("generated_evidence_unreadable", raw_path))
                continue
            missing_fields = [
                field for field in evidence_fields
                if field not in evidence or evidence[field] in (None, "")
            ]
            if missing_fields:
                pending_evidence.append(
                    _error("generated_evidence_metadata_missing", raw_path, ",".join(missing_fields))
                )
            continue
        if hash_policy == "sha256" or validation == "sha256":
            expected = entry.get("sha256")
            if not _is_hex(expected, 64):
                errors.append(_error("sha256_missing_or_invalid", raw_path))
                continue
            if _sha256(path) != expected:
                errors.append(_error("sha256_mismatch", raw_path))

    return {
        "schemaVersion": 1,
        "status": "FAIL" if errors else "PASS",
        "registerPath": str(register_path),
        "canonicalCommit": payload.get("canonicalCommit"),
        "checkedEntryCount": len(entries),
        "checkedPathCount": len(checked_paths),
        "errors": errors,
        "pendingEvidence": pending_evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate docs/review_register.json without live operations.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--register", type=Path)
    args = parser.parse_args()
    result = verify_register(args.root, args.register)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
