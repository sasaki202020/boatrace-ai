from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file
from src.ingest.official_fetcher import JCD_TO_VENUE
from src.normalize.race_snapshot import build_race_snapshot
from src.normalize.schema import Boat


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "official"
NORM_ROOT = ROOT / "data" / "normalized"
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


def _normalize_jcd(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(2) if digits else ""


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_dirs(input_dir: str | None) -> list[Path]:
    roots: list[Path] = []
    if input_dir:
        roots.append(Path(input_dir))
    roots.extend(
        [
            RAW_ROOT / "txt",
            RAW_ROOT / "results_txt",
            RAW_ROOT / "results",
            RAW_ROOT,
        ]
    )
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def find_k_file_for_date(date8: str, *, input_dir: str | None = None) -> Path | None:
    date8 = _normalize_date(date8)
    yy = date8[2:]
    exact_names = [f"K{yy}.TXT", f"k{yy}.TXT"]
    wildcard_names = [f"K{yy}*.TXT", f"k{yy}*.TXT", f"K{yy}*.txt", f"k{yy}*.txt"]
    for root in _candidate_dirs(input_dir):
        if not root.exists():
            continue
        if root.is_file():
            if root.name in exact_names or root.name.upper() in {name.upper() for name in exact_names}:
                return root
            continue
        for name in exact_names:
            candidate = root / name
            if candidate.exists():
                return candidate
        for pattern in wildcard_names:
            for candidate in sorted(root.rglob(pattern)):
                if candidate.is_file() and candidate.name.upper().startswith(f"K{yy}".upper()) and candidate.suffix.lower() == ".txt":
                    return candidate
    return None


def _boat_from_result_row(row: dict[str, Any]) -> Boat | None:
    boat_no = row.get("boat_no") or row.get("boatNo")
    if not isinstance(boat_no, int):
        return None
    return Boat(
        boat_no=int(boat_no),
        racer_name=row.get("racer_name"),
        racer_id=row.get("racer_id"),
        start_exhibition_course=row.get("course"),
        start_exhibition_st=row.get("startTiming"),
        exhibition_time=row.get("exhibitionTime"),
        tilt=row.get("tilt"),
        data_status="missing",
    )


def _build_minimal_snapshot(
    *,
    date8: str,
    jcd: str,
    venue_name: str,
    race_no: int,
    result_record: dict[str, Any],
    source_path: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_source = dict((existing or {}).get("source") or {})
    existing_result = dict((existing or {}).get("result") or {})
    result_source = dict(result_record.get("source") or {})
    result_source.setdefault("resultSource", "official_txt_k")
    result_source.setdefault("kFilePath", source_path)
    result_source.setdefault("resultSourceType", "official_txt_k")
    if existing_source.get("resultUrl"):
        result_source.setdefault("resultUrl", existing_source.get("resultUrl"))
    merged_source = dict(existing_source)
    merged_source.update(result_source)
    merged_source.setdefault("resultSource", "official_txt_k")
    merged_source.setdefault("kResultPath", source_path)
    existing_result_status = str(existing_result.get("raceStatus") or existing_result.get("race_status") or existing.get("dataStatus", {}).get("result") if existing else "").lower()
    k_status = str(result_record.get("raceStatus") or "").lower()
    prefer_k = False
    if k_status == "ok":
        prefer_k = existing_result_status not in {"ok"}
        if existing_result_status in {"refund", "canceled", "no_contest"}:
            prefer_k = True
    elif k_status in {"refund", "canceled", "no_contest"}:
        prefer_k = existing_result_status not in {"ok"}
    elif existing_result_status == "ok":
        prefer_k = False
    else:
        prefer_k = True
    selected_result = result_record if prefer_k or not existing_result else existing_result or result_record
    data_status = dict((existing or {}).get("dataStatus") or {})
    data_status.setdefault("racelist", "ok" if (existing or {}).get("boats") else "pending")
    data_status.setdefault("odds3t", (existing or {}).get("dataStatus", {}).get("odds3t", "pending") if isinstance((existing or {}).get("dataStatus"), dict) else "pending")
    data_status["result"] = "ok" if str(selected_result.get("raceStatus") or "").lower() == "ok" else str(selected_result.get("raceStatus") or data_status.get("result") or "pending")
    data_status_reason = dict((existing or {}).get("dataStatusReason") or {})
    existing_boats = (existing or {}).get("boats") or []
    boats = list(existing_boats) if isinstance(existing_boats, list) else []
    if not boats:
        boats = [_boat_from_result_row(row) for row in (result_record.get("boatResults") or []) if _boat_from_result_row(row)]
    normalized = build_race_snapshot(
        date=date8,
        jcd=jcd,
        venue_name=venue_name,
        rno=race_no,
        deadline=str((existing or {}).get("deadline") or ""),
        race_title=str((existing or {}).get("raceTitle") or ""),
        stage=str((existing or {}).get("stage") or "result"),
        boats=boats,
        before_info=dict((existing or {}).get("beforeInfo") or {}),
        weather=(existing or {}).get("weather") or result_record.get("weather") or {},
        start_exhibition=list((existing or {}).get("startExhibition") or []),
        odds3t=dict((existing or {}).get("odds3t") or {}),
        result={
            **selected_result,
            "resultSource": "official_txt_k",
            "result_source": "official_txt_k",
        },
        source=merged_source,
        data_status=data_status,
        data_status_reason=data_status_reason,
        predictions=list((existing or {}).get("predictions") or []),
        model_version=str((existing or {}).get("modelVersion") or (existing or {}).get("model_version") or "baseline_rule_v1"),
        updated_at=str((existing or {}).get("updatedAt") or (existing or {}).get("updated_at") or datetime.now().isoformat(timespec="seconds")),
    ).to_dict()
    normalized["result"] = {
        **selected_result,
        "resultSource": "official_txt_k",
        "result_source": "official_txt_k",
    }
    normalized["source"] = merged_source
    normalized["source"]["resultSource"] = "official_txt_k"
    normalized["source"]["kResultPath"] = source_path
    normalized["source"]["resultSourceType"] = "official_txt_k"
    normalized["dataStatus"] = data_status
    normalized["dataStatus"]["result"] = data_status["result"]
    normalized["dataStatusReason"] = data_status_reason
    return normalized


def _merge_k_result(existing: dict[str, Any] | None, result_record: dict[str, Any], *, date8: str, source_path: str) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    jcd = str(result_record.get("jcd") or "").zfill(2)
    race_no = int(result_record.get("rno") or result_record.get("raceNo") or 0)
    venue_name = str(result_record.get("venueName") or result_record.get("venue_name") or JCD_TO_VENUE.get(jcd, jcd))
    existing_result = dict((existing or {}).get("result") or {})
    existing_status = str(existing_result.get("raceStatus") or existing_result.get("race_status") or (existing or {}).get("dataStatus", {}).get("result") if existing else "").lower()
    k_status = str(result_record.get("raceStatus") or "").lower()
    selected_source = "official_txt_k"
    if existing_result and existing_status == "ok" and k_status == "ok":
        existing_combo = str(existing_result.get("trifectaCombo") or existing_result.get("trifecta_combo") or "").strip()
        k_combo = str(result_record.get("trifectaCombo") or result_record.get("trifecta_combo") or "").strip()
        existing_payout = existing_result.get("trifectaPayout") or existing_result.get("trifecta_payout")
        k_payout = result_record.get("trifectaPayout") or result_record.get("trifecta_payout")
        if existing_combo and k_combo and (existing_combo != k_combo or str(existing_payout) != str(k_payout)):
            warnings.append("result_conflict")
    if existing_status == "ok" and k_status != "ok":
        selected = existing
        selected_source = str((existing or {}).get("source", {}).get("resultSource") or "official_html")
    elif k_status == "ok":
        selected = _build_minimal_snapshot(
            date8=date8,
            jcd=jcd,
            venue_name=venue_name,
            race_no=race_no,
            result_record=result_record,
            source_path=source_path,
            existing=existing,
        )
        selected_source = "official_txt_k"
    elif existing_status in {"refund", "canceled", "no_contest"} and k_status not in {"ok"}:
        selected = existing or _build_minimal_snapshot(
            date8=date8,
            jcd=jcd,
            venue_name=venue_name,
            race_no=race_no,
            result_record=result_record,
            source_path=source_path,
            existing=existing,
        )
        selected_source = str((existing or {}).get("source", {}).get("resultSource") or "official_html")
    elif k_status in {"refund", "canceled", "no_contest"}:
        selected = _build_minimal_snapshot(
            date8=date8,
            jcd=jcd,
            venue_name=venue_name,
            race_no=race_no,
            result_record=result_record,
            source_path=source_path,
            existing=existing,
        )
        selected_source = "official_txt_k"
    else:
        selected = _build_minimal_snapshot(
            date8=date8,
            jcd=jcd,
            venue_name=venue_name,
            race_no=race_no,
            result_record=result_record,
            source_path=source_path,
            existing=existing,
        )
        selected_source = "official_txt_k"
    if selected_source == "official_txt_k":
        selected.setdefault("source", {})
        selected["source"]["resultSource"] = "official_txt_k"
        selected["source"]["kResultPath"] = source_path
        selected["source"]["resultSourceType"] = "official_txt_k"
    if str(selected.get("result", {}).get("raceStatus") or "").lower() == "ok":
        selected["dataStatus"] = dict(selected.get("dataStatus") or {})
        selected["dataStatus"]["result"] = "ok"
    return selected, warnings


def collect_official_k_results(
    *,
    date8: str,
    input_dir: str | None = None,
    jcd: str = "all",
    force: bool = False,
) -> dict[str, Any]:
    date8 = _normalize_date(date8)
    file_path = find_k_file_for_date(date8, input_dir=input_dir)
    details: list[dict[str, Any]] = []
    normalized_written: list[dict[str, Any]] = []
    counts = defaultdict(int)
    if file_path is None:
        details.append(
            {
                "date": date8,
                "jcd": jcd,
                "rno": 0,
                "stage": "result_txt",
                "action": "missing",
                "status": "missing",
                "httpStatus": "missing",
                "parsedCount": 0,
                "rawPath": "",
                "normalizedPath": "",
                "errorType": "result_txt_missing",
                "message": "KYYMMDD.TXT not found in candidate directories",
            }
        )
        counts["resultTxtMissingCount"] += 1
        summary = {
            "dateRange": f"{date8}_{date8}",
            "targetDates": 1,
            "targetVenues": 0,
            "targetRaces": 0,
            "fetchedRacelistCount": 0,
            "fetchedOddsCount": 0,
            "fetchedResultCount": 0,
            "fetchedResultTxtCount": 0,
            "parsedResultTxtRaceCount": 0,
            "resultTxtMissingCount": 1,
            "resultTxtParseErrorCount": 0,
            "resultTxtOkCount": 0,
            "skippedExistingCount": 0,
            "fetchErrorCount": 0,
            "parseErrorCount": 0,
            "notHeldCount": 0,
            "pendingCount": 0,
            "browserFallbackCount": 0,
            "oddsParsed120Count": 0,
            "resultOkCount": 0,
            "warnings": ["result_txt_missing"],
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
        }
        return {"summary": summary, "details": details, "normalized": normalized_written, "sourcePath": "", "fileStatus": "missing"}

    parsed = parse_official_k_result_file(file_path, date8=date8)
    races = parsed.get("races") or []
    date_result_count = 0
    result_ok_count = 0
    parse_error_count = 0
    missing_count = 0
    not_held_count = 0
    processed_races = 0
    for race in races:
        if not isinstance(race, dict):
            continue
        race_jcd = _normalize_jcd(str(race.get("jcd") or ""))
        if jcd != "all" and race_jcd != _normalize_jcd(jcd):
            continue
        processed_races += 1
        status = str(race.get("raceStatus") or "").lower()
        if status == "ok":
            result_ok_count += 1
        elif status == "parse_error":
            parse_error_count += 1
        elif status == "not_held":
            not_held_count += 1
        else:
            missing_count += 1
        date_result_count += 1
        normalized_path = NORM_ROOT / date8 / race_jcd / f"race_{int(race.get('rno') or race.get('raceNo') or 0)}.json"
        existing = _load_json(normalized_path)
        merged, warnings = _merge_k_result(existing, race, date8=date8, source_path=str(file_path))
        if force and normalized_path.exists():
            try:
                normalized_path.unlink()
            except Exception:
                pass
        _write_json(normalized_path, merged)
        normalized_written.append({"date": date8, "jcd": race_jcd, "rno": int(race.get("rno") or race.get("raceNo") or 0), "path": str(normalized_path)})
        details.append(
            {
                "date": date8,
                "jcd": race_jcd,
                "rno": int(race.get("rno") or race.get("raceNo") or 0),
                "stage": "result_txt",
                "action": "parsed" if status == "ok" else status or "missing",
                "status": status or "missing",
                "httpStatus": "k_file",
                "parsedCount": len(race.get("boatResults") or []),
                "rawPath": str(file_path),
                "normalizedPath": str(normalized_path),
                "errorType": "" if status == "ok" else "result_txt_parse_error",
                "message": ";".join(warnings or race.get("parseWarnings") or []),
            }
        )
    summary = {
        "dateRange": f"{date8}_{date8}",
        "targetDates": 1,
        "targetVenues": len({str(r.get("jcd") or "").zfill(2) for r in races if r.get("jcd")}),
        "targetRaces": len(races),
        "fetchedRacelistCount": 0,
        "fetchedOddsCount": 0,
        "fetchedResultCount": 0,
        "fetchedResultTxtCount": 1,
        "parsedResultTxtRaceCount": date_result_count,
        "resultTxtMissingCount": int(missing_count),
        "resultTxtParseErrorCount": int(parse_error_count),
        "resultTxtOkCount": int(result_ok_count),
        "skippedExistingCount": 0,
        "fetchErrorCount": 0,
        "parseErrorCount": int(parse_error_count),
        "notHeldCount": int(not_held_count),
        "pendingCount": 0,
        "browserFallbackCount": 0,
        "oddsParsed120Count": 0,
        "resultOkCount": int(result_ok_count),
        "warnings": sorted(dict.fromkeys(parsed.get("parseWarnings") or [])),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    return {
        "summary": summary,
        "details": details,
        "normalized": normalized_written,
        "sourcePath": str(file_path),
        "fileStatus": "ok",
        "parsed": parsed,
    }


def collect_official_k_results_range(
    *,
    start_date: str,
    end_date: str,
    input_dir: str | None = None,
    jcd: str = "all",
    force: bool = False,
) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)
    all_details: list[dict[str, Any]] = []
    all_normalized: list[dict[str, Any]] = []
    summary_counts = defaultdict(int)
    warnings: set[str] = set()
    for day in days:
        result = collect_official_k_results(date8=day, input_dir=input_dir, jcd=jcd, force=force)
        all_details.extend(result.get("details") or [])
        all_normalized.extend(result.get("normalized") or [])
        summary = result.get("summary") or {}
        warnings.update(str(item) for item in summary.get("warnings") or [])
        for key in (
            "fetchedResultTxtCount",
            "parsedResultTxtRaceCount",
            "resultTxtMissingCount",
            "resultTxtParseErrorCount",
            "resultTxtOkCount",
            "fetchErrorCount",
            "parseErrorCount",
            "notHeldCount",
            "pendingCount",
            "browserFallbackCount",
            "resultOkCount",
            "skippedExistingCount",
        ):
            summary_counts[key] += int(summary.get(key) or 0)
    summary = {
        "dateRange": f"{start8}_{end8}",
        "targetDates": len(days),
        "targetVenues": len({row.get("jcd") for row in all_details if row.get("jcd")}),
        "targetRaces": len(all_details),
        "fetchedRacelistCount": 0,
        "fetchedOddsCount": 0,
        "fetchedResultCount": 0,
        "fetchedResultTxtCount": int(summary_counts.get("fetchedResultTxtCount", 0)),
        "parsedResultTxtRaceCount": int(summary_counts.get("parsedResultTxtRaceCount", 0)),
        "resultTxtMissingCount": int(summary_counts.get("resultTxtMissingCount", 0)),
        "resultTxtParseErrorCount": int(summary_counts.get("resultTxtParseErrorCount", 0)),
        "resultTxtOkCount": int(summary_counts.get("resultTxtOkCount", 0)),
        "skippedExistingCount": int(summary_counts.get("skippedExistingCount", 0)),
        "fetchErrorCount": int(summary_counts.get("fetchErrorCount", 0)),
        "parseErrorCount": int(summary_counts.get("parseErrorCount", 0)),
        "notHeldCount": int(summary_counts.get("notHeldCount", 0)),
        "pendingCount": int(summary_counts.get("pendingCount", 0)),
        "browserFallbackCount": int(summary_counts.get("browserFallbackCount", 0)),
        "oddsParsed120Count": 0,
        "resultOkCount": int(summary_counts.get("resultOkCount", 0)),
        "warnings": sorted(warnings),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    return {"summary": summary, "details": all_details, "normalized": all_normalized, "days": days}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Load and normalize official KYYMMDD.TXT result files.")
    parser.add_argument("--date", help="YYYYMMDD or today")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.date:
        date8 = _normalize_date(args.date)
        result = collect_official_k_results(date8=date8, input_dir=args.input_dir, jcd=args.jcd, force=args.force)
    else:
        if not args.start_date or not args.end_date:
            raise SystemExit("--date or --start-date/--end-date is required")
        result = collect_official_k_results_range(
            start_date=args.start_date,
            end_date=args.end_date,
            input_dir=args.input_dir,
            jcd=args.jcd,
            force=args.force,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

