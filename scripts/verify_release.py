from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_review_register import verify_register


HEX = set("0123456789abcdef")
TEXT_SUFFIXES = {".bat", ".js", ".json", ".md", ".ps1", ".py", ".toml", ".txt", ".yaml", ".yml"}
EXCLUDED_PARTS = {".git", "backups", "data", "logs", "models", "reports", "__pycache__", ".pytest_cache"}
RECOVERY_TOKEN = "競艇" + "-recovery"
FORWARD_RUNTIME_TOKEN = "boatrace-" + "feature-" + "forward-v1"
FORBIDDEN_REFERENCE_PATTERNS = (
    ("absolute_windows_path", re.compile(r"(?i)[a-z]:[\\/]users[\\/]")),
    ("file_url_path", re.compile(r"(?i)file:///+[a-z]:")),
    ("recovery_clone_reference", re.compile(f"{RECOVERY_TOKEN}|{FORWARD_RUNTIME_TOKEN}", re.IGNORECASE)),
)
AUTHORITY_ORDER = "FINAL_PRODUCT_SPEC > AGENTS execution rules > CODEX_TASKS > CONTEXT/HANDOFF historical context > reports evidence"
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def _error(code: str, path: str | None = None, detail: str | None = None) -> dict[str, str]:
    result = {"code": code}
    if path is not None:
        result["path"] = path
    if detail is not None:
        result["detail"] = detail
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _tracked_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS or part.startswith(".pytest_tmp") for part in relative.parts):
            continue
        if relative.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return sorted(files)


def find_forbidden_references(root: Path, files: Iterable[Path] | None = None) -> list[dict[str, str]]:
    root = Path(root).resolve()
    findings: list[dict[str, str]] = []
    for path in files if files is not None else _tracked_text_files(root):
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        try:
            display_path = path.resolve().relative_to(root).as_posix()
        except ValueError:
            display_path = str(path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for code, pattern in FORBIDDEN_REFERENCE_PATTERNS:
                if pattern.search(line):
                    findings.append(_error(code, display_path, f"line={line_number}"))
    return findings


def verify_runtime_lock(root: Path, lock_path: Path | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    lock_path = Path(lock_path or root / "config/runtime_lock.json")
    if not lock_path.is_absolute():
        lock_path = (root / lock_path).resolve()
    payload = _load_json(lock_path)
    if payload is None:
        return {"status": "FAIL", "lockPath": str(lock_path), "errors": [_error("runtime_lock_unreadable")]}

    errors: list[dict[str, str]] = []
    if payload.get("schemaVersion") != 1:
        errors.append(_error("runtime_lock_schema_version_invalid"))
    if not _is_hex(payload.get("canonicalCommit"), 40):
        errors.append(_error("runtime_lock_canonical_commit_invalid"))
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append(_error("runtime_lock_entries_invalid"))
        entries = []
    seen_paths: set[str] = set()
    for index, entry in enumerate(entries):
        location = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(_error("runtime_lock_entry_invalid", location))
            continue
        required = {"path", "sha256", "sourceRepository", "sourceCommit", "adoptedAtUtc", "mutable"}
        missing = sorted(required - set(entry))
        if missing:
            errors.append(_error("runtime_lock_entry_fields_missing", location, ",".join(missing)))
            continue
        raw_path = entry["path"]
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(_error("runtime_lock_path_invalid", location))
            continue
        if raw_path in seen_paths:
            errors.append(_error("runtime_lock_duplicate_path", raw_path))
            continue
        seen_paths.add(raw_path)
        if not _is_hex(entry["sha256"], 64):
            errors.append(_error("runtime_lock_sha256_invalid", raw_path))
            continue
        if not isinstance(entry["sourceRepository"], str) or not entry["sourceRepository"]:
            errors.append(_error("runtime_lock_source_repository_invalid", raw_path))
        if not _is_hex(entry["sourceCommit"], 40):
            errors.append(_error("runtime_lock_source_commit_invalid", raw_path))
        if not isinstance(entry["adoptedAtUtc"], str) or not entry["adoptedAtUtc"].endswith("Z"):
            errors.append(_error("runtime_lock_adopted_at_invalid", raw_path))
        if entry["mutable"] is not False:
            errors.append(_error("runtime_lock_mutable_source", raw_path))
        path = _safe_relative_path(root, raw_path)
        if path is None:
            errors.append(_error("runtime_lock_path_outside_root", raw_path))
            continue
        if not path.is_file():
            errors.append(_error("runtime_source_missing", raw_path))
            continue
        if _sha256(path) != entry["sha256"]:
            errors.append(_error("runtime_sha256_mismatch", raw_path))
    return {
        "status": "FAIL" if errors else "PASS",
        "lockPath": str(lock_path),
        "checkedEntryCount": len(entries),
        "errors": errors,
    }


def _gate(identifier: str, status: str, detail: str, errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"id": identifier, "status": status, "detail": detail, "errors": errors or []}


def check_importability(root: Path) -> dict[str, Any]:
    required_modules = (
        "src.feature_forward_v1.live_capture",
        "src.feature_forward_v1.runtime_lifecycle",
        "src.feature_forward_v1.runtime_provenance",
        "src.feature_forward_v1.source_policy",
        "src.commercialization_v2.prospective_anchor",
    )
    errors: list[dict[str, str]] = []
    root_text = str(Path(root).resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # Import errors must fail the release gate, not crash it.
            errors.append(_error("required_module_unimportable", module_name, type(exc).__name__))
    try:
        importlib.import_module("pytest")
    except Exception as exc:
        errors.append(_error("pytest_unavailable", detail=type(exc).__name__))
    if not (Path(root) / "tests").is_dir():
        errors.append(_error("tests_directory_missing"))
    return _gate("G3", "FAIL" if errors else "PASS", "import and test-runner availability", errors)


def _markdown_local_links(path: Path, root: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [_error("markdown_unreadable", path.relative_to(root).as_posix())]
    errors: list[dict[str, str]] = []
    for match in MARKDOWN_LINK.finditer(text):
        raw_target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
        if not raw_target or raw_target.startswith(("#", "https://", "http://", "mailto:")):
            continue
        normalized = unquote(raw_target.split("#", 1)[0].split("?", 1)[0])
        if not normalized:
            continue
        if normalized.startswith("file:") or Path(normalized).is_absolute() or re.match(r"^[a-zA-Z]:[\\/]", normalized):
            errors.append(_error("markdown_absolute_link", path.relative_to(root).as_posix(), normalized))
            continue
        candidate = (path.parent / normalized).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(_error("markdown_link_outside_root", path.relative_to(root).as_posix(), normalized))
            continue
        if not candidate.exists():
            errors.append(_error("markdown_link_missing", path.relative_to(root).as_posix(), normalized))
    return errors


def check_document_authority(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    files = {
        "AGENTS.md": root / "AGENTS.md",
        "FINAL_PRODUCT_SPEC.md": root / "docs/FINAL_PRODUCT_SPEC.md",
        "CODEX_CONTEXT.md": root / "docs/CODEX_CONTEXT.md",
        "CODEX_HANDOFF.md": root / "docs/CODEX_HANDOFF.md",
        "00_MASTER_INDEX.md": root / "docs/00_MASTER_INDEX.md",
    }
    errors: list[dict[str, str]] = []
    contents: dict[str, str] = {}
    for name, path in files.items():
        try:
            contents[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(_error("authority_document_missing", name))
    for name in ("AGENTS.md", "FINAL_PRODUCT_SPEC.md", "CODEX_CONTEXT.md", "CODEX_HANDOFF.md"):
        if name in contents and AUTHORITY_ORDER not in contents[name]:
            errors.append(_error("authority_order_missing", name))
    for name in ("CODEX_CONTEXT.md", "CODEX_HANDOFF.md"):
        if name in contents and "NON-NORMATIVE / HISTORICAL CONTEXT" not in contents[name]:
            errors.append(_error("historical_context_marker_missing", name))
    index = files["00_MASTER_INDEX.md"]
    if index.is_file():
        errors.extend(_markdown_local_links(index, root))
        if "FINAL_PRODUCT_SPEC.md" not in contents.get("00_MASTER_INDEX.md", ""):
            errors.append(_error("master_index_canonical_link_missing", "00_MASTER_INDEX.md"))
    return _gate("G4", "FAIL" if errors else "PASS", "canonical authority and entry links", errors)


def check_safety_configuration(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    paths = {
        "source": root / "config/feature_forward_v1/source_approval.json",
        "oof": root / "config/feature_forward_v1/oof_evaluation_spec.json",
        "shadow": root / "config/feature_forward_v1/parallel_shadow_config.json",
    }
    payloads = {name: _load_json(path) for name, path in paths.items()}
    errors: list[dict[str, str]] = []
    if any(payload is None for payload in payloads.values()):
        errors.append(_error("safety_config_unreadable"))
        return _gate("G5", "FAIL", "production and prospective safety configuration", errors)
    source = payloads["source"]
    oof = payloads["oof"]
    shadow = payloads["shadow"]
    assert source is not None and oof is not None and shadow is not None
    if source.get("automatedNetworkFetchAllowed") is not False:
        errors.append(_error("automated_fetch_must_remain_disabled", "source_approval.json"))
    for key in ("commercialUseAllowed", "redistributionAllowed", "publicReleaseAllowed", "paidServiceAllowed"):
        if source.get(key) is not False:
            errors.append(_error("source_permission_must_remain_disabled", "source_approval.json", key))
    adoption = oof.get("adoption")
    if not isinstance(adoption, dict) or adoption.get("productionAdoptionAllowed") is not False or adoption.get("personalAdoptionAllowed") is not False:
        errors.append(_error("oof_adoption_must_remain_disabled", "oof_evaluation_spec.json"))
    for key in ("productionAdoptionAllowed", "prospectiveConnectionAllowed", "oofExecuted"):
        if shadow.get(key) is not False:
            errors.append(_error("shadow_activation_must_remain_disabled", "parallel_shadow_config.json", key))
    return _gate("G5", "FAIL" if errors else "PASS", "production and prospective safety configuration", errors)


def _explicit_status(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = value.get("status")
            if isinstance(nested, str) and nested:
                return nested
    return None


def check_oof_evidence(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    candidates = (
        root / "reports/feature_forward_v1/oof_evaluation.json",
        root / "reports/feature_forward_v1/oof_result.json",
        root / "reports/feature_forward_v1/latest_status.json",
    )
    status: str | None = None
    for path in candidates:
        payload = _load_json(path)
        if payload is None:
            continue
        status = _explicit_status(payload, ("oofStatus", "oofEvaluationStatus", "oofEvaluation", "oof"))
        if status is not None:
            break
    if status in {"OOF_COMPLETED", "DECISION_READY", "COMPLETED"}:
        return _gate("G7", "PASS", f"explicit OOF status: {status}")
    detail = "explicit OOF status absent" if status is None else f"OOF not decision-ready: {status}"
    return _gate("G7", "BLOCKED", detail)


def check_live_shadow_evidence(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    path = root / "reports/monitoring/live_shadow_evidence.json"
    payload = _load_json(path)
    status = _explicit_status(payload, ("status", "liveShadowStatus")) if payload else None
    if status in {"PASS", "LIVE_SHADOW_COMPLETED", "COMPLETED"}:
        return _gate("G8", "PASS", f"explicit live shadow status: {status}")
    detail = "explicit live shadow status absent" if status is None else f"live shadow not ready: {status}"
    return _gate("G8", "BLOCKED", detail)


def release_status(gates: Iterable[dict[str, Any]]) -> tuple[str, int]:
    statuses = {str(gate.get("status")) for gate in gates}
    if "FAIL" in statuses:
        return "FAIL", 1
    if "BLOCKED" in statuses:
        return "BLOCKED", 2
    return "PASS", 0


def build_release_report(root: Path, *, mode: str) -> dict[str, Any]:
    if mode != "no-live":
        raise ValueError("only_no_live_mode_is_supported")
    root = Path(root).resolve()
    register = verify_register(root)
    runtime = verify_runtime_lock(root)
    forbidden = find_forbidden_references(root)
    pending_evidence = list(register.get("pendingEvidence", []))
    gates = [
        _gate("G0", "FAIL" if forbidden else "PASS", "no absolute Windows or recovery runtime references", forbidden),
        _gate("G1", str(register["status"]), "review register integrity", list(register.get("errors", []))),
        _gate("G2", str(runtime["status"]), "runtime lock integrity", list(runtime.get("errors", []))),
        check_importability(root),
        check_document_authority(root),
        check_safety_configuration(root),
        _gate(
            "G6",
            "BLOCKED" if pending_evidence else "PASS",
            "generated evidence provenance" if not pending_evidence else "generated evidence is incomplete",
            pending_evidence,
        ),
        check_oof_evidence(root),
        check_live_shadow_evidence(root),
    ]
    status, exit_code = release_status(gates)
    return {
        "schemaVersion": 1,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "status": status,
        "exitCode": exit_code,
        "networkUsed": False,
        "liveExecuted": False,
        "buyExecuted": False,
        "wageringExecuted": False,
        "gates": gates,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Release Readiness",
        "",
        f"- status: {report['status']}",
        f"- mode: {report['mode']}",
        f"- generatedAtUtc: {report['generatedAtUtc']}",
        f"- exitCode: {report['exitCode']}",
        f"- networkUsed: {report['networkUsed']}",
        f"- liveExecuted: {report['liveExecuted']}",
        f"- buyExecuted: {report['buyExecuted']}",
        f"- wageringExecuted: {report['wageringExecuted']}",
        "",
        "| Gate | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for gate in report["gates"]:
        detail = str(gate["detail"]).replace("|", "\\|")
        lines.append(f"| {gate['id']} | {gate['status']} | {detail} |")
    return "\n".join(lines) + "\n"


def write_release_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest.json"
    markdown_path = output_dir / "latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run release-readiness checks without live data access.")
    parser.add_argument("--mode", choices=("no-live",), required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    output_dir = args.output_dir or root / "reports/release_readiness"
    try:
        report = build_release_report(root, mode=args.mode)
    except ValueError as exc:
        report = {
            "schemaVersion": 1,
            "generatedAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "mode": args.mode,
            "status": "FAIL",
            "exitCode": 1,
            "networkUsed": False,
            "liveExecuted": False,
            "buyExecuted": False,
            "wageringExecuted": False,
            "gates": [_gate("CLI", "FAIL", str(exc))],
        }
    json_path, markdown_path = write_release_report(report, output_dir)
    print(json.dumps({"status": report["status"], "json": str(json_path), "markdown": str(markdown_path)}, ensure_ascii=False))
    return int(report["exitCode"])


if __name__ == "__main__":
    raise SystemExit(main())
