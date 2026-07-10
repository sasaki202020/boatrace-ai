from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evaluation.audit_k_result_coverage import audit_k_result_coverage
from src.evaluation.export_missing_k_checklist import export_missing_k_checklist
from src.pipeline.import_k_results import _candidate_files, _detect_encoding, _sha256_bytes


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"
DEFAULT_TARGET_DIR = ROOT / "data" / "raw" / "official" / "results"
K_FILENAME_RE = re.compile(r"^K\d{6}\.TXT$", re.IGNORECASE)


def _existing_targets(target_dir: str | None = None) -> set[str]:
    root = Path(target_dir) if target_dir else DEFAULT_TARGET_DIR
    if not root.exists():
        return set()
    return {path.name for path in root.rglob("K*.TXT") if path.is_file()}


def _is_valid_k_filename(file_name: str) -> bool:
    return bool(K_FILENAME_RE.fullmatch(file_name))


def _manifest_row(
    *,
    file_name: str,
    source_path: str,
    payload: bytes,
    target_dir: str | None,
    missing_names: set[str],
    existing_targets: set[str],
) -> dict[str, Any]:
    checksum = _sha256_bytes(payload)
    target_path = ""
    action = "invalid"
    import_target = False
    skip_target = False
    invalid_target = False
    filename_valid = _is_valid_k_filename(file_name)
    encoding_text, encoding = _detect_encoding(payload)
    if not filename_valid:
        invalid_target = True
    else:
        date_token = file_name[1:7]
        if len(date_token) == 6 and date_token.isdigit() and file_name in existing_targets:
            skip_target = True
            action = "skip_candidate"
            target_path = str(Path(target_dir or "") / file_name) if target_dir else ""
        elif len(date_token) == 6 and date_token.isdigit() and file_name in missing_names:
            import_target = True
            action = "import_candidate"
            target_path = str(Path(target_dir or "") / file_name) if target_dir else ""
        else:
            action = "invalid"
            invalid_target = True
    return {
        "fileName": file_name,
        "sourcePath": source_path,
        "checksum": checksum,
        "encoding": encoding,
        "isZip": source_path.lower().endswith(".zip") or "::" in source_path,
        "isValidKFileName": filename_valid,
        "existsInMissingList": file_name in missing_names,
        "existsInTarget": file_name in existing_targets,
        "action": action,
        "importTarget": import_target,
        "skipTarget": skip_target,
        "invalidTarget": invalid_target,
        "targetPath": target_path,
    }


def check_k_inbox(*, input_dir: str, start_date: str, end_date: str, target_dir: str | None = None) -> dict[str, Any]:
    inbox = Path(input_dir)
    report_rows: list[dict[str, Any]] = []
    target_root = target_dir or str(DEFAULT_TARGET_DIR)
    missing = export_missing_k_checklist(start_date=start_date, end_date=end_date, input_dir=target_root)
    missing_names = {str(row.get("expectedFileName") or "") for row in missing.get("rows") or []}
    target_names = _existing_targets(target_dir=target_root)

    inbox_exists = inbox.exists()
    txt_count = 0
    zip_count = 0
    k_count = 0
    import_count = 0
    skip_count = 0
    invalid_count = 0

    if inbox_exists:
        for path in _candidate_files(str(inbox)):
            if path.suffix.lower() == ".zip":
                zip_count += 1
                with zipfile.ZipFile(path, "r") as zf:
                    for member in zf.infolist():
                        if member.is_dir():
                            continue
                        member_name = Path(member.filename).name
                        if not member_name.lower().endswith(".txt"):
                            continue
                        with zf.open(member, "r") as f:
                            payload = f.read()
                        row = _manifest_row(
                            file_name=member_name,
                            source_path=f"{path}::{member.filename}",
                            payload=payload,
                            target_dir=target_root,
                            missing_names=missing_names,
                            existing_targets=target_names,
                        )
                        report_rows.append(row)
                        if row["isValidKFileName"]:
                            k_count += 1
            elif path.suffix.lower() == ".txt":
                txt_count += 1
                payload = path.read_bytes()
                row = _manifest_row(
                    file_name=path.name,
                    source_path=str(path),
                    payload=payload,
                    target_dir=target_root,
                    missing_names=missing_names,
                    existing_targets=target_names,
                )
                report_rows.append(row)
                if row["isValidKFileName"]:
                    k_count += 1

    for row in report_rows:
        if row["action"] == "import_candidate":
            import_count += 1
        elif row["action"] == "skip_candidate":
            skip_count += 1
        else:
            invalid_count += 1

    summary = {
        "inputDirExists": inbox_exists,
        "txtFileCount": txt_count,
        "zipFileCount": zip_count,
        "kFileCount": k_count,
        "totalEntries": len(report_rows),
        "importTargetCount": import_count,
        "skipTargetCount": skip_count,
        "invalidTargetCount": invalid_count,
        "missingChecklistCount": len(missing_names),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    if not inbox_exists or len(report_rows) == 0:
        summary["recommendedNextAction"] = "place_missing_k_files_in_inbox"
    elif import_count > 0:
        summary["recommendedNextAction"] = "run_import_k_results"
    else:
        summary["recommendedNextAction"] = "review_invalid_or_existing_files"

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_ROOT / "k_inbox_check.json"
    csv_path = REPORT_ROOT / "k_inbox_check.csv"
    json_path.write_text(json.dumps({"summary": summary, "rows": report_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "fileName",
                "sourcePath",
                "checksum",
            "encoding",
            "isZip",
            "isValidKFileName",
            "existsInMissingList",
            "existsInTarget",
            "action",
            "importTarget",
            "skipTarget",
                "invalidTarget",
                "targetPath",
            ],
        )
        writer.writeheader()
        for row in report_rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
    return {"summary": summary, "rows": report_rows, "files": {"json": str(json_path), "csv": str(csv_path)}, "missingChecklist": missing}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Check the K result inbox before import.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    result = check_k_inbox(input_dir=args.input_dir, start_date=args.start_date, end_date=args.end_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
