from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.ingest.official_k_loader import find_k_file_for_date
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "backtest"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _daterange(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def k_filename_for_date(date8: str) -> str:
    return f"K{date8[2:]}.TXT"


def k_date_from_filename(filename: str) -> str | None:
    token = str(filename).strip()
    if len(token) != 11 or not token.upper().startswith("K") or not token.upper().endswith(".TXT"):
        return None
    digits = token[1:7]
    if len(digits) != 6 or not digits.isdigit():
        return None
    return f"20{digits[:2]}{digits[2:]}"


def _ready_result_status(status: str) -> bool:
    return status in {"ok", "refund", "canceled", "no_contest", "available_without_trifecta"}


def audit_k_result_coverage(*, start_date: str, end_date: str, input_dir: str | None = None) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)

    rows: list[dict[str, Any]] = []
    summary_counts = defaultdict(int)
    missing_dates: list[str] = []

    for day in days:
        expected_name = k_filename_for_date(day)
        k_path = find_k_file_for_date(day, input_dir=input_dir)
        has_k_file = k_path is not None
        parsed_race_count = 0
        ok_race_count = 0
        parse_error_count = 0
        missing_race_count = 0
        ready_race_count = 0
        can_use_for_settlement = False
        missing_reason = "missing_k_file"
        parse_warnings: list[str] = []

        if has_k_file:
            try:
                parsed = parse_official_k_result_file(k_path, date8=day)
            except Exception as exc:
                parse_warnings.append(type(exc).__name__)
                parse_error_count = 1
                missing_reason = "parse_error"
            else:
                races = parsed.get("races") or []
                parsed_race_count = int(parsed.get("raceCount") or len(races))
                ok_race_count = int(parsed.get("resultTxtOkCount") or 0)
                parse_error_count = int(parsed.get("resultTxtParseErrorCount") or 0)
                missing_race_count = int(parsed.get("resultTxtMissingCount") or 0)
                ready_race_count = sum(1 for race in races if _ready_result_status(str(race.get("raceStatus") or "").lower()))
                can_use_for_settlement = ready_race_count > 0
                parse_warnings.extend(str(item) for item in parsed.get("parseWarnings") or [])
                if parse_error_count > 0:
                    missing_reason = "parse_error"
                elif parsed_race_count == 0:
                    missing_reason = "invalid_snapshot_shape"
                elif not can_use_for_settlement:
                    missing_reason = "insufficient_settled_bets"
                else:
                    missing_reason = ""
        else:
            missing_dates.append(day)

        rows.append(
            {
                "date": day,
                "expectedKFileName": expected_name,
                "hasKFile": has_k_file,
                "kFilePath": str(k_path) if k_path else "",
                "parsedRaceCount": parsed_race_count,
                "okRaceCount": ok_race_count,
                "parseErrorCount": parse_error_count,
                "missingRaceCount": missing_race_count,
                "canUseForSettlement": can_use_for_settlement,
                "missingReason": missing_reason,
                "parseWarnings": "|".join(sorted(dict.fromkeys(parse_warnings))),
            }
        )
        summary_counts["parsedResultTxtRaceCount"] += parsed_race_count
        summary_counts["resultTxtOkCount"] += ok_race_count
        summary_counts["resultTxtMissingCount"] += missing_race_count
        if has_k_file:
            summary_counts["daysWithKFile"] += 1
        else:
            summary_counts["daysMissingKFile"] += 1

    summary = {
        "dateRange": f"{start8}_{end8}",
        "totalDays": len(days),
        "daysWithKFile": int(summary_counts.get("daysWithKFile", 0)),
        "daysMissingKFile": int(summary_counts.get("daysMissingKFile", 0)),
        "parsedResultTxtRaceCount": int(summary_counts.get("parsedResultTxtRaceCount", 0)),
        "resultTxtOkCount": int(summary_counts.get("resultTxtOkCount", 0)),
        "resultTxtMissingCount": int(summary_counts.get("resultTxtMissingCount", 0)),
        "missingDates": missing_dates,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    date_tag = f"{start8}_{end8}"
    json_path = REPORT_ROOT / f"{date_tag}_k_result_coverage.json"
    csv_path = REPORT_ROOT / f"{date_tag}_k_result_coverage.csv"
    _write_csv(csv_path, rows)
    json_path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "rows": rows, "files": {"json": str(json_path), "csv": str(csv_path)}}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "date",
        "expectedKFileName",
        "hasKFile",
        "kFilePath",
        "parsedRaceCount",
        "okRaceCount",
        "parseErrorCount",
        "missingRaceCount",
        "canUseForSettlement",
        "missingReason",
        "parseWarnings",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Audit availability of KYYMMDD.TXT historical result files.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--input-dir", default=None)
    args = parser.parse_args()

    result = audit_k_result_coverage(start_date=args.start_date, end_date=args.end_date, input_dir=args.input_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
