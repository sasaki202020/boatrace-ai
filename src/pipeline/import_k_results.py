from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.evaluation.audit_k_result_coverage import k_date_from_filename
from src.ingest.official_k_loader import collect_official_k_results
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_text


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = ROOT / "_archive"
REPORT_ROOT = ROOT / "reports" / "backtest"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _candidate_files(input_dir: str | None) -> list[Path]:
    roots: list[Path] = []
    if input_dir:
        roots.append(Path(input_dir))
    seen: set[str] = set()
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            if path.suffix.lower() in {".txt", ".zip"}:
                files.append(path)
    return files


def _detect_encoding(data: bytes) -> tuple[str, str]:
    candidates = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except Exception:
            continue
    return data.decode("cp932", errors="replace"), "cp932"


def _archive_existing(target_path: Path, *, date8: str) -> Path:
    archive_dir = ARCHIVE_ROOT / date8 / "k_results"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    archived = archive_dir / f"{target_path.stem}_{stamp}{target_path.suffix}"
    shutil.move(str(target_path), str(archived))
    return archived


def _iter_txt_payloads(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                member_name = Path(member.filename).name
                if not member_name.lower().endswith(".txt"):
                    continue
                with zf.open(member, "r") as f:
                    data = f.read()
                yield {
                    "fileName": member_name,
                    "sourcePath": f"{path}::{member.filename}",
                    "bytes": data,
                }
        return
    yield {"fileName": path.name, "sourcePath": str(path), "bytes": path.read_bytes()}


def _write_manifest_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "fileName",
        "date",
        "sourcePath",
        "targetPath",
        "action",
        "checksum",
        "encoding",
        "parsedRaceCount",
        "resultTxtOkCount",
        "warnings",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def import_k_results(*, input_dir: str, target_dir: str) -> dict[str, Any]:
    input_root = Path(input_dir)
    target_root = Path(target_dir)
    target_root.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    imported = skipped = replaced = invalid = parse_error = 0

    for payload in _candidate_files(str(input_root)):
        if payload.suffix.lower() == ".zip":
            for item in _iter_txt_payloads(payload):
                manifest_rows.append(
                    _import_single_payload(
                        file_name=item["fileName"],
                        source_path=item["sourcePath"],
                        data=item["bytes"],
                        target_root=target_root,
                    )
                )
        elif payload.suffix.lower() == ".txt":
            manifest_rows.append(
                _import_single_payload(
                    file_name=payload.name,
                    source_path=str(payload),
                    data=payload.read_bytes(),
                    target_root=target_root,
                )
            )

    for row in manifest_rows:
        action = str(row.get("action") or "")
        if action == "imported":
            imported += 1
        elif action == "skipped_existing_same":
            skipped += 1
        elif action == "replaced_existing_different":
            replaced += 1
        elif action == "invalid_name":
            invalid += 1
        elif action == "parse_error":
            parse_error += 1

    date_tag = datetime.now().strftime("%Y%m%d")
    json_path = REPORT_ROOT / "k_result_import_manifest.json"
    csv_path = REPORT_ROOT / "k_result_import_manifest.csv"
    payload = {
        "summary": {
            "importedFileCount": imported,
            "skippedFileCount": skipped,
            "replacedFileCount": replaced,
            "invalidNameFileCount": invalid,
            "parseErrorFileCount": parse_error,
            "totalFiles": len(manifest_rows),
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
        },
        "rows": manifest_rows,
        "files": {"json": str(json_path), "csv": str(csv_path)},
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_manifest_csv(csv_path, manifest_rows)
    return payload


def _import_single_payload(*, file_name: str, source_path: str, data: bytes, target_root: Path) -> dict[str, Any]:
    checksum = _sha256_bytes(data)
    date8 = k_date_from_filename(file_name)
    encoding_text, encoding = _detect_encoding(data)
    target_path = target_root / file_name
    row = {
        "fileName": file_name,
        "date": date8 or "",
        "sourcePath": source_path,
        "targetPath": str(target_path),
        "action": "invalid_name",
        "checksum": checksum,
        "encoding": encoding,
        "parsedRaceCount": 0,
        "resultTxtOkCount": 0,
        "warnings": "",
    }
    if date8 is None:
        return row

    existing_checksum = _sha256_path(target_path) if target_path.exists() else None
    if existing_checksum == checksum:
        row["action"] = "skipped_existing_same"
        parsed = parse_official_k_result_text(text=encoding_text, source_path=source_path, date8=date8)
        row["parsedRaceCount"] = int(parsed.get("raceCount") or 0)
        row["resultTxtOkCount"] = int(parsed.get("resultTxtOkCount") or 0)
        row["warnings"] = "|".join(sorted(str(item) for item in parsed.get("parseWarnings") or []))
        return row

    if target_path.exists() and existing_checksum != checksum:
        _archive_existing(target_path, date8=date8)
        action = "replaced_existing_different"
    else:
        action = "imported"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)

    try:
        parsed = parse_official_k_result_text(text=encoding_text, source_path=source_path, date8=date8)
    except Exception as exc:
        row["action"] = "parse_error"
        row["warnings"] = type(exc).__name__
        return row

    race_count = int(parsed.get("raceCount") or 0)
    row["parsedRaceCount"] = race_count
    row["resultTxtOkCount"] = int(parsed.get("resultTxtOkCount") or 0)
    row["warnings"] = "|".join(sorted(str(item) for item in parsed.get("parseWarnings") or []))
    if race_count <= 0:
        row["action"] = "parse_error"
    else:
        row["action"] = action
    return row


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Import KYYMMDD.TXT files into data/raw/official/results.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    args = parser.parse_args()
    result = import_k_results(input_dir=args.input_dir, target_dir=args.target_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
