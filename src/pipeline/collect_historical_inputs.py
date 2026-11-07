from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.evaluation.audit_historical_inputs import audit_historical_inputs
from src.ingest.official_k_loader import collect_official_k_results, collect_official_k_results_range
from src.ingest.official_fetcher import fetch_day
from src.normalize.race_snapshot import build_race_snapshot
from src.pipeline.discover_venues import discover_venues_for_date
from src.pipeline.run_today import _build_snapshot as _run_today_build_snapshot


ROOT = Path(__file__).resolve().parents[2]
NORMALIZED_ROOT = ROOT / "data" / "normalized"
RAW_ROOT = ROOT / "data" / "raw" / "official"
REPORTS_ROOT = ROOT / "reports" / "backtest"


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
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
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


def _delete_raw_for_stage(date8: str, jcd: str, stage: str) -> None:
    kind = {"racelist": "racelist", "odds": "odds3t", "result": "result"}.get(stage, stage)
    if kind == "pre_race":
        kind = "racelist"
    day_dir = RAW_ROOT / date8 / jcd
    if not day_dir.exists():
        return
    for path in day_dir.glob(f"{kind}_*.html"):
        try:
            path.unlink()
        except Exception:
            pass


def _select_target_venues(date8: str, *, jcd: str, discovery: dict[str, Any], audit_rows: list[dict[str, Any]]) -> list[str]:
    venue_ids: set[str] = set()
    normalized_jcd = _normalize_jcd(jcd) if jcd != "all" else "all"
    for row in audit_rows:
        row_jcd = str(row.get("jcd") or "").zfill(2)
        if normalized_jcd != "all" and row_jcd != normalized_jcd:
            continue
        missing_reason = str(row.get("missingReason") or "")
        if missing_reason in {"date_not_held", "date_not_collected", "no_venue_discovery"}:
            venue_ids.update(venue.get("jcd") for venue in discovery.get("venues") or [] if venue.get("isOpen", True))
            continue
        if row_jcd and row_jcd != "all":
            venue_ids.add(row_jcd)
    if not venue_ids:
        venue_ids.update(venue.get("jcd") for venue in discovery.get("venues") or [] if venue.get("isOpen", True))
    return sorted(v for v in venue_ids if v)


def _normalize_stage_name(stage: str) -> str:
    token = str(stage or "").strip().lower()
    if token in {"pre_race", "racelist"}:
        return "racelist"
    if token in {"odds", "result", "beforeinfo", "result_txt"}:
        return token
    return "racelist"


def _target_stages(stage_spec: str) -> list[str]:
    stages = []
    for item in str(stage_spec or "").split(","):
        token = _normalize_stage_name(item)
        if token not in stages:
            stages.append(token)
    return stages or ["racelist"]


def _stage_needed(missing_reason: str, requested_stages: list[str], stage: str) -> bool:
    if stage not in requested_stages:
        return False
    if missing_reason in {"no_input_files", "no_venue_discovery", "date_not_collected", "date_not_held"}:
        return True
    if stage == "racelist" and missing_reason in {"raw_racelist_missing", "normalized_missing"}:
        return True
    if stage == "odds" and missing_reason in {"odds_missing", "normalized_missing"}:
        return True
    if stage == "result" and missing_reason in {"result_missing", "normalized_missing"}:
        return True
    if stage == "result_txt" and missing_reason in {"result_missing", "result_html_missing_but_txt_available", "result_txt_missing", "normalized_missing"}:
        return True
    return False


def _merge_stage_row(base: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("racelist", "beforeinfo", "odds3t", "result", "source", "dataStatusReason"):
        left = merged.get(key)
        right = row.get(key)
        if isinstance(left, dict) or isinstance(right, dict):
            combined = dict(left or {})
            if isinstance(right, dict):
                combined.update(right)
            merged[key] = combined
        elif right is not None:
            merged[key] = right
    for key in ("date", "jcd", "venue_name", "race_no", "race_id", "race_title", "deadline"):
        if row.get(key) not in (None, ""):
            merged[key] = row.get(key)
    merged["stage"] = row.get("stage") or merged.get("stage") or "pre_race"
    return merged


def _build_snapshot_from_row(row: dict[str, Any], *, stage: str) -> dict[str, Any]:
    snapshot = _run_today_build_snapshot(row, stage=stage)
    odds = (row.get("odds3t") or {}).get("parsed") if isinstance(row.get("odds3t"), dict) else {}
    result = (row.get("result") or {}).get("parsed") if isinstance(row.get("result"), dict) else {}
    before = (row.get("beforeinfo") or {}).get("parsed") if isinstance(row.get("beforeinfo"), dict) else {}
    if isinstance(odds, dict) and odds:
        snapshot.odds3t = odds
        snapshot.data_status["odds3t"] = str((row.get("odds3t") or {}).get("dataStatus") or "ok")
    if isinstance(before, dict) and before:
        snapshot.before_info = before.get("beforeInfo") or before.get("beforeinfo") or snapshot.before_info
        snapshot.weather = before.get("weather") or snapshot.weather
        snapshot.start_exhibition = before.get("start_exhibition") or before.get("startExhibition") or snapshot.start_exhibition
        snapshot.data_status["beforeinfo"] = str((row.get("beforeinfo") or {}).get("dataStatus") or "ok")
    if isinstance(result, dict) and result:
        snapshot.result = result
        snapshot.data_status["result"] = str((row.get("result") or {}).get("dataStatus") or "ok")
    return snapshot.to_dict()


def _stage_hint_from_row(row: dict[str, Any]) -> str:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    odds = row.get("odds3t") if isinstance(row.get("odds3t"), dict) else {}
    before = row.get("beforeinfo") if isinstance(row.get("beforeinfo"), dict) else {}
    result_status = str(result.get("dataStatus") or "").lower()
    odds_status = str(odds.get("dataStatus") or "").lower()
    before_status = str(before.get("dataStatus") or "").lower()
    if result_status in {"available", "ok", "refund", "canceled", "no_contest", "available_without_trifecta"}:
        return "result"
    if odds_status in {"available", "ok"}:
        return "odds"
    if before_status in {"available", "ok"}:
        return "beforeinfo"
    return "racelist"


def _collect_day(
    *,
    date8: str,
    jcd: str,
    stages: list[str],
    force: bool,
    discovery: dict[str, Any],
    audit_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    venue_ids = _select_target_venues(date8, jcd=jcd, discovery=discovery, audit_rows=audit_rows)
    details: list[dict[str, Any]] = []
    normalized_written: list[dict[str, Any]] = []
    counts = defaultdict(int)
    if not venue_ids:
        counts["notHeldCount"] += 1
        return details, normalized_written, counts

    for venue_jcd in venue_ids:
        merged_rows: dict[int, dict[str, Any]] = {}
        venue_stage = "racelist"
        venue_norm_dir = NORMALIZED_ROOT / date8 / venue_jcd
        existing_races = sorted(
            {
                int(path.stem.split("_", 1)[-1])
                for path in venue_norm_dir.glob("race_*.json")
                if path.stem.split("_", 1)[-1].isdigit()
            }
        ) if venue_norm_dir.exists() else []
        for stage in [item for item in stages if item != "result_txt"]:
            venue_audit_rows = [row for row in audit_rows if str(row.get("jcd") or "").zfill(2) == venue_jcd]
            stage_needed = any(_stage_needed(str(row.get("missingReason") or ""), stages, stage) for row in venue_audit_rows)
            target_races = existing_races or list(range(1, 13))
            if not stage_needed:
                continue
            if not target_races:
                continue
            if force:
                _delete_raw_for_stage(date8, venue_jcd, stage)
            fetch_stage = "pre_race" if stage == "racelist" else stage
            try:
                rows = fetch_day(target_date=date8, jcd=venue_jcd, races=target_races, stage=fetch_stage)
            except Exception as exc:
                counts["fetchErrorCount"] += 1
                details.append(
                    {
                        "date": date8,
                        "jcd": venue_jcd,
                        "rno": 0,
                        "stage": stage,
                        "action": "fetch_error",
                        "status": "error",
                        "httpStatus": "error",
                        "parsedCount": 0,
                        "rawPath": "",
                        "normalizedPath": str(NORMALIZED_ROOT / date8 / venue_jcd),
                        "errorType": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                continue
            venue_stage = stage
            for row in rows:
                race_no = int(row.get("race_no") or row.get("rno") or 0)
                if race_no <= 0:
                    continue
                merged_rows[race_no] = _merge_stage_row(merged_rows.get(race_no, {}), row)
                stage_row = (
                    row.get("racelist")
                    if stage == "racelist"
                    else row.get("odds3t")
                    if stage == "odds"
                    else row.get("result")
                    if stage == "result"
                    else {}
                ) or {}
                http_status = str(stage_row.get("fetchStatus") or "unavailable")
                data_status = str(stage_row.get("dataStatus") or "missing")
                parsed_count = 0
                if stage == "racelist":
                    parsed_count = len((stage_row.get("parsed") or {}).get("boats") or [])
                elif stage == "odds":
                    parsed_count = int(stage_row.get("parsedOddsCount") or len(stage_row.get("parsed") or {}))
                elif stage == "result":
                    parsed = stage_row.get("parsed") or {}
                    parsed_count = 1 if (parsed.get("trifectaCombo") or parsed.get("trifecta_combo")) else 0
                raw_path = str(stage_row.get("rawHtmlPath") or stage_row.get("resultRawPath") or "")
                normalized_path = NORMALIZED_ROOT / date8 / venue_jcd / f"race_{race_no}.json"
                action = "fetched"
                if http_status == "cache":
                    counts["skippedExistingCount"] += 1
                    action = "skip_existing"
                if stage_row.get("fallbackUsed") or stage_row.get("resultFallbackUsed"):
                    counts["browserFallbackCount"] += 1
                if data_status in {"available", "ok"}:
                    if stage == "racelist":
                        counts["fetchedRacelistCount"] += 1
                    elif stage == "odds":
                        counts["fetchedOddsCount"] += 1
                        if int(stage_row.get("parsedOddsCount") or 0) >= 120:
                            counts["oddsParsed120Count"] += 1
                elif stage == "result":
                    counts["fetchedResultCount"] += 1
                    result_parsed = stage_row.get("parsed") or {}
                    if str(result_parsed.get("raceStatus") or "").lower() == "ok":
                        counts["resultOkCount"] += 1
                elif data_status == "pending":
                    counts["pendingCount"] += 1
                elif data_status in {"missing", "unavailable"}:
                    counts["fetchErrorCount"] += 1
                elif data_status == "parse_error":
                    counts["parseErrorCount"] += 1
                elif data_status in {"refund", "canceled", "no_contest"}:
                    counts["notHeldCount"] += 1
                details.append(
                    {
                        "date": date8,
                        "jcd": venue_jcd,
                        "rno": race_no,
                        "stage": stage,
                        "action": action,
                        "status": data_status,
                        "httpStatus": http_status,
                        "parsedCount": parsed_count,
                        "rawPath": raw_path,
                        "normalizedPath": str(normalized_path),
                        "errorType": stage_row.get("errorType") or "",
                        "message": stage_row.get("errorMessage") or "",
                    }
                )

        for race_no, merged in merged_rows.items():
            normalized_path = NORMALIZED_ROOT / date8 / venue_jcd / f"race_{race_no}.json"
            normalized_path.parent.mkdir(parents=True, exist_ok=True)
            stage_hint = _stage_hint_from_row(merged)
            snapshot = _build_snapshot_from_row(merged, stage=stage_hint)
            _write_json(normalized_path, snapshot)
            normalized_written.append({"date": date8, "jcd": venue_jcd, "rno": race_no, "path": str(normalized_path)})

    return details, normalized_written, counts


def collect_historical_inputs(
    *,
    start_date: str,
    end_date: str,
    jcd: str = "all",
    stages: str = "racelist,odds,result",
    input_dir: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)
    requested_stages = _target_stages(stages)
    audit = audit_historical_inputs(start_date=start8, end_date=end8, jcd=jcd)
    audit_rows = audit.get("rows") or []
    all_details: list[dict[str, Any]] = []
    all_normalized: list[dict[str, Any]] = []
    summary_counts = defaultdict(int)
    discovered_days: dict[str, dict[str, Any]] = {}
    for day in days:
        discovered_days[day] = discover_venues_for_date(day, force=force)

    for day in days:
        day_audit_rows = [row for row in audit_rows if str(row.get("date") or "") == day]
        discovery = discovered_days.get(day) or {"venues": []}
        venue_ids = _select_target_venues(day, jcd=jcd, discovery=discovery, audit_rows=day_audit_rows)
        if not venue_ids:
            if "result_txt" in requested_stages:
                result_txt_rows = collect_official_k_results(date8=day, input_dir=input_dir, jcd=jcd, force=force)
                all_details.extend(result_txt_rows.get("details") or [])
                all_normalized.extend(result_txt_rows.get("normalized") or [])
                summary = result_txt_rows.get("summary") or {}
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
                    "skippedExistingCount",
                ):
                    summary_counts[key] += int(summary.get(key) or 0)
                if int(summary.get("parsedResultTxtRaceCount") or 0) > 0:
                    continue
            summary_counts["date_not_held"] += 1
            continue
        for venue_jcd in venue_ids:
            venue_audit_rows = [row for row in day_audit_rows if str(row.get("jcd") or "").zfill(2) in {venue_jcd, "all"}]
            if not any(
                _stage_needed(str(row.get("missingReason") or ""), requested_stages, stage)
                for row in venue_audit_rows
                for stage in requested_stages
            ):
                continue
            details, normalized_written, counts = _collect_day(
                date8=day,
                jcd=venue_jcd,
                stages=requested_stages,
                force=force,
                discovery=discovery,
                audit_rows=venue_audit_rows,
            )
            all_details.extend(details)
            all_normalized.extend(normalized_written)
            for key, value in counts.items():
                summary_counts[key] += value
        if "result_txt" in requested_stages:
            result_txt_rows = collect_official_k_results(date8=day, input_dir=input_dir, jcd=jcd, force=force)
            all_details.extend(result_txt_rows.get("details") or [])
            all_normalized.extend(result_txt_rows.get("normalized") or [])
            summary = result_txt_rows.get("summary") or {}
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
                "skippedExistingCount",
            ):
                summary_counts[key] += int(summary.get(key) or 0)

    collection_summary = {
        "dateRange": f"{start8}_{end8}",
        "targetDates": len(days),
        "targetVenues": len({row.get("jcd") for row in audit_rows if row.get("jcd")}),
        "targetRaces": len(all_details),
        "fetchedRacelistCount": int(summary_counts.get("fetchedRacelistCount", 0)),
        "fetchedOddsCount": int(summary_counts.get("fetchedOddsCount", 0)),
        "fetchedResultCount": int(summary_counts.get("fetchedResultCount", 0)),
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
        "oddsParsed120Count": int(summary_counts.get("oddsParsed120Count", 0)),
        "resultOkCount": int(summary_counts.get("resultOkCount", 0)),
        "warnings": sorted({str(item) for day in days for item in (discovered_days.get(day, {}).get("warnings") or [])}),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    details_path = REPORTS_ROOT / f"{start8}_{end8}_collection_details.csv"
    summary_path = REPORTS_ROOT / f"{start8}_{end8}_collection_summary.json"
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    with details_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "date",
            "jcd",
            "rno",
            "stage",
            "action",
            "status",
            "httpStatus",
            "parsedCount",
            "rawPath",
            "normalizedPath",
            "errorType",
            "message",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_details:
            writer.writerow({key: row.get(key) for key in fieldnames})
    summary_path.write_text(json.dumps(collection_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "summary": collection_summary,
        "details": all_details,
        "normalized": all_normalized,
        "files": {"summary": str(summary_path), "details": str(details_path)},
        "audit": audit,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Collect historical BOATRACE inputs for backfill and backtest.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--jcd", default="all")
    parser.add_argument("--stages", default="racelist,odds,result")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = collect_historical_inputs(start_date=args.start_date, end_date=args.end_date, jcd=args.jcd, stages=args.stages, input_dir=args.input_dir, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
