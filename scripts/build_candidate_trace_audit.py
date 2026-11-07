from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_PRED_ROOT = ROOT / "reports" / "predictions"
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"
CANDIDATE_QUALITY_REVIEW = REPORTS_MONITORING_ROOT / "candidate_quality_review.json"
PAPER_VALIDATION_SUMMARY = REPORTS_MONITORING_ROOT / "paper_validation_summary.json"
OUT_JSON = REPORTS_MONITORING_ROOT / "candidate_trace_audit.json"
OUT_MD = REPORTS_MONITORING_ROOT / "candidate_trace_audit.md"
OUT_CSV = REPORTS_MONITORING_ROOT / "candidate_trace_rows.csv"
LEGACY_UNKNOWN = "legacy_unknown"


CANONICAL_FIELDNAMES = [
    "candidateId",
    "raceId",
    "raceDate",
    "venueCode",
    "raceNo",
    "combination",
    "snapshotId",
    "snapshotHash",
    "snapshotCapturedAt",
    "featureVersion",
    "featureHash",
    "modelVersion",
    "calibratorVersion",
    "predictionHash",
    "rawProbability",
    "calibratedProbability",
    "odds",
    "oddsCapturedAt",
    "deadlineAt",
    "marketProbability",
    "estimatedEdge",
    "policyVersion",
    "policyDecision",
    "guardDecision",
    "guardReason",
    "frozenBetId",
    "frozenAt",
    "resultCombination",
    "payout",
    "settlementStatus",
    "settledAt",
]

TRACE_METADATA_FIELDNAMES = [
    "traceStatus",
    "traceReason",
    "traceReasonCategory",
    "predictionHashMatch",
    "frozenExists",
    "settlementExists",
    "resultAvailable",
    "resultStatus",
    "hit",
    "predictedTrifecta",
    "actualTrifecta",
    "settlementPredictedTrifectaMatch",
    "settledOdds",
    "payoutAmount",
    "pnl",
]

LEGACY_TRACE_FIELDNAMES = [
    "date",
    "venue",
    "jcd",
    "race_no",
    "race_id",
    "paperDecision",
    "finalDecision",
    "stopReason",
    "oddsStatus",
    "approxProb",
    "realOdds",
    "expectedValue",
    "riskFlag",
    "confidenceRank",
    "combo",
    "predictionHashComputed",
    "frozenFreezeType",
    "frozenSourceType",
    "frozenPredictionSource",
    "monitorPaperCandidateCount",
    "monitorPaperEligibleCandidateCount",
    "monitorPaperSettledCandidateCount",
    "monitorPaperIneligibleCandidateCount",
    "monitorPaperPendingCandidateCount",
    "monitorPaperSettlementCoverage",
    "monitorResultStatus",
]

TRACE_REASON_CATEGORIES = {
    "complete": "complete",
    "result_unconfirmed": "no_settlement",
    "missing_prediction_sheet": "scope_mismatch",
    "missing_frozen_bets": "frozen_not_created",
    "missing_settlement": "no_settlement",
    "hash_mismatch": "prediction_hash_mismatch",
    "race_mismatch": "scope_mismatch",
    "combo_mismatch": "scope_mismatch",
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _save_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_date_text(value: str | None) -> str | None:
    if not value:
        return None
    token = str(value).strip()
    if not token:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(token, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    if len(token) == 15 and "_" in token:
        left, right = token.split("_", 1)
        if len(left) == 8 and len(right) == 8:
            try:
                start = datetime.strptime(left, "%Y%m%d").strftime("%Y-%m-%d")
                end = datetime.strptime(right, "%Y%m%d").strftime("%Y-%m-%d")
                return f"{start}_{end}"
            except Exception:
                return None
    return None


def _date_range(start_iso: str, end_iso: str) -> list[str]:
    start = datetime.strptime(start_iso, "%Y-%m-%d").date()
    end = datetime.strptime(end_iso, "%Y-%m-%d").date()
    if end < start:
        return []
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


def _load_candidate_quality_review() -> dict[str, Any]:
    return _load_json(CANDIDATE_QUALITY_REVIEW)


def _load_paper_validation_summary() -> dict[str, Any]:
    return _load_json(PAPER_VALIDATION_SUMMARY)


def _infer_date_range(start: str | None, end: str | None) -> tuple[str, str]:
    if start and end:
        return start, end

    quality = _load_candidate_quality_review()
    date_range = str(quality.get("dateRange") or "").strip()
    if not date_range:
        summary = _load_paper_validation_summary()
        date_range = str(summary.get("summary", {}).get("dateRange") or "").strip()

    if not date_range or "_" not in date_range:
        raise SystemExit("date range could not be inferred; pass --start and --end")

    left, right = date_range.split("_", 1)
    inferred_start = datetime.strptime(left, "%Y%m%d").strftime("%Y-%m-%d")
    inferred_end = datetime.strptime(right, "%Y%m%d").strftime("%Y-%m-%d")
    return start or inferred_start, end or inferred_end


def _load_day_json(root: Path, date_iso: str, filename: str) -> dict[str, Any]:
    for candidate in (
        root / date_iso / filename,
        root / date_iso.replace("-", "") / filename,
        root / f"{date_iso.replace('-', '')}_{filename}",
    ):
        payload = _load_json(candidate)
        if payload:
            return payload
    return {}


def _load_settlement_rows(date_iso: str) -> list[dict[str, Any]]:
    path = REPORTS_DAILY_ROOT / date_iso / "daily_evaluation_race_results.csv"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if isinstance(row, dict):
                    rows.append(row)
    except Exception:
        return []
    return rows


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "y", "ok", "available", "settled"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_optional_text(value: Any) -> str | None:
    token = _normalize_text(value)
    return token if token else None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    token = str(value).strip()
    if not token or token.lower() in {"none", "null", "nan"}:
        return None
    try:
        return float(token)
    except Exception:
        return None


def _parse_iso_datetime_text(value: Any):
    token = _normalize_text(value)
    if not token or token == LEGACY_UNKNOWN:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_missing_legacy(value: Any) -> bool:
    return value is None or value == "" or value == LEGACY_UNKNOWN


def _compose_race_id(date_text: Any, jcd: Any, race_no: Any) -> str:
    date_token = str(date_text or "").strip()
    if len(date_token) == 8 and date_token.isdigit():
        date_token = f"{date_token[:4]}-{date_token[4:6]}-{date_token[6:8]}"
    else:
        date_token = _normalize_text(date_text)
    try:
        jcd_token = f"{int(str(jcd).strip()):02d}"
    except Exception:
        jcd_token = _normalize_text(jcd)
    try:
        race_token = f"{int(str(race_no).strip()):02d}"
    except Exception:
        race_token = _normalize_text(race_no)
    return f"{date_token.replace('-', '')}-{jcd_token}-{race_token}"


def _candidate_id(race_date: str, race_id: str, combo: str, prediction_hash: str) -> str:
    date_token = _parse_date_text(str(race_date or "")) or _normalize_text(race_date)
    payload = "|".join(
        [
            date_token or LEGACY_UNKNOWN,
            _normalize_text(race_id) or LEGACY_UNKNOWN,
            _normalize_text(combo) or LEGACY_UNKNOWN,
            _normalize_text(prediction_hash) or LEGACY_UNKNOWN,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _trace_reason_category(trace_status: str, row: dict[str, Any]) -> str:
    if trace_status in TRACE_REASON_CATEGORIES:
        return TRACE_REASON_CATEGORIES[trace_status]
    if trace_status == "complete":
        return "complete"
    if any(_is_missing_legacy(row.get(field)) for field in ("modelVersion", "predictionHash", "policyDecision")):
        return "legacy_field_missing"
    return "unknown"


def _build_sheet_index(sheet_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in sheet_rows:
        race_id = _normalize_text(row.get("raceId") or row.get("race_id"))
        combo = _normalize_text(row.get("combo"))
        paper_decision = _normalize_text(row.get("paperDecision")).upper()
        if not race_id or not combo or not paper_decision:
            continue
        key = (race_id, combo, paper_decision)
        if key not in index:
            index[key] = row
    return index


def _build_frozen_indexes(
    frozen_payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    by_candidate_id: dict[str, dict[str, Any]] = {}
    by_hash: dict[str, dict[str, Any]] = {}
    by_race_combo: dict[tuple[str, str], dict[str, Any]] = {}
    races = frozen_payload.get("races") if isinstance(frozen_payload.get("races"), list) else []
    for race in races:
        if not isinstance(race, dict):
            continue
        race_id = _normalize_text(race.get("raceId"))
        for bet in race.get("bets") if isinstance(race.get("bets"), list) else []:
            if not isinstance(bet, dict):
                continue
            pred_hash = _normalize_text(bet.get("predictionHashComputed") or bet.get("predictionHash"))
            combo = _normalize_text(bet.get("combo"))
            candidate_id = _candidate_id(_parse_date_text(_normalize_text(race.get("date"))) or LEGACY_UNKNOWN, race_id, combo, pred_hash)
            persisted_candidate_id = _normalize_text(bet.get("candidateId"))
            if persisted_candidate_id and persisted_candidate_id not in by_candidate_id:
                by_candidate_id[persisted_candidate_id] = bet
            if candidate_id not in by_candidate_id:
                by_candidate_id[candidate_id] = bet
            if pred_hash and pred_hash not in by_hash:
                by_hash[pred_hash] = bet
            if race_id and combo and (race_id, combo) not in by_race_combo:
                by_race_combo[(race_id, combo)] = bet
    return by_candidate_id, by_hash, by_race_combo


def _build_settlement_index(settlement_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in settlement_rows:
        normalized = dict(row)
        if not _truthy(normalized.get("result_available")):
            normalized["result_available"] = True
        if not _normalize_text(normalized.get("resultStatus")):
            normalized["resultStatus"] = "available"
        race_id = _normalize_text(normalized.get("race_id"))
        if race_id and race_id not in index:
            index[race_id] = normalized
    return index


def _load_daily_report(date_iso: str) -> dict[str, Any]:
    return _load_day_json(REPORTS_DAILY_ROOT, date_iso, "daily_report.json")


def _build_daily_report_indexes(
    daily_report: dict[str, Any],
    date_iso: str,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    pred_index: dict[str, dict[str, Any]] = {}
    pred_key_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    settlement_index: dict[str, dict[str, Any]] = {}
    settlement_block = daily_report.get("settlement") if isinstance(daily_report.get("settlement"), dict) else {}
    settlements = settlement_block.get("settlements") if isinstance(settlement_block.get("settlements"), list) else []
    for race in settlements:
        if not isinstance(race, dict):
            continue
        race_id = _compose_race_id(race.get("date") or date_iso, race.get("jcd"), race.get("raceNo") or race.get("rno"))
        if race_id and race_id not in settlement_index:
            settlement_index[race_id] = race
        predictions = race.get("predictions") if isinstance(race.get("predictions"), list) else []
        for pred in predictions:
            if not isinstance(pred, dict):
                continue
            pred_hash = _normalize_text(pred.get("predictionHash") or pred.get("predictionHashComputed"))
            combo = _normalize_text(pred.get("combo"))
            candidate_id = _candidate_id(_parse_date_text(_normalize_text(race.get("date"))) or date_iso, race_id, combo, pred_hash)
            if candidate_id and candidate_id not in pred_index:
                pred_index[candidate_id] = pred
            pred_key = (race_id, combo, pred_hash)
            if all(pred_key) and pred_key not in pred_key_index:
                pred_key_index[pred_key] = pred
    return pred_index, pred_key_index, settlement_index


def _load_settlement_candidates(
    date_iso: str,
    *,
    daily_report: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    settlement_rows = _load_settlement_rows(date_iso)
    csv_index = _build_settlement_index(settlement_rows)
    if isinstance(daily_report, dict) and daily_report:
        _, _, report_settlement_index = _build_daily_report_indexes(daily_report, date_iso)
        for race_id, row in report_settlement_index.items():
            if race_id not in csv_index:
                csv_index[race_id] = row
    return csv_index


def _candidate_status(
    *,
    sheet_row: dict[str, Any] | None,
    frozen_row: dict[str, Any] | None,
    settlement_row: dict[str, Any] | None,
) -> tuple[str, str]:
    if not sheet_row:
        return "missing_prediction_sheet", "prediction_sheet row not found"
    if not frozen_row:
        return "missing_frozen_bets", "frozen_bets row not found"
    if not settlement_row:
        return "missing_settlement", "settlement row not found"

    prediction_hash = _normalize_text(sheet_row.get("predictionHash"))
    frozen_hash = _normalize_text(frozen_row.get("predictionHashComputed") or frozen_row.get("predictionHash"))
    if prediction_hash and frozen_hash and prediction_hash != frozen_hash:
        return "hash_mismatch", "predictionHash and predictionHashComputed differ"

    race_id = _normalize_text(sheet_row.get("raceId") or sheet_row.get("race_id"))
    frozen_race_id = _normalize_text(frozen_row.get("race_id") or frozen_row.get("raceId"))
    if race_id and frozen_race_id and race_id != frozen_race_id:
        return "race_mismatch", "race_id mismatch between prediction and frozen ledger"

    combo = _normalize_text(sheet_row.get("combo"))
    frozen_combo = _normalize_text(frozen_row.get("combo"))
    if combo and frozen_combo and combo != frozen_combo:
        return "combo_mismatch", "combo mismatch between prediction and frozen ledger"

    result_available = settlement_row.get("result_available")
    if result_available is None:
        settlement_status = _normalize_text(
            settlement_row.get("resultStatus")
            or settlement_row.get("result_status")
            or settlement_row.get("settleStatus")
        ).lower()
        result_available = settlement_status in {"ok", "available", "settled"}
    if not _truthy(result_available):
        return "result_unconfirmed", "settlement row present but result not confirmed"

    return "complete", "predictionHash → frozen_bets → settlement linked"


def build_candidate_trace_audit(*, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    start_iso, end_iso = _infer_date_range(start, end)
    dates = _date_range(start_iso, end_iso)

    quality = _load_candidate_quality_review()
    summary = _load_paper_validation_summary()
    scope = quality.get("scope") if isinstance(quality.get("scope"), dict) else {}
    current_state = quality.get("currentState") if isinstance(quality.get("currentState"), dict) else {}
    daily_summary_rows = summary.get("rows") if isinstance(summary.get("rows"), list) else []
    daily_summary_index = {
        str(row.get("date")): row for row in daily_summary_rows if isinstance(row, dict) and row.get("date")
    }

    candidate_rows: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []
    trace_status_counts: Counter[str] = Counter()
    trace_reason_counts: Counter[str] = Counter()
    trace_reason_category_counts: Counter[str] = Counter()
    result_status_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    missing_prediction_sheet_days: list[str] = []
    missing_frozen_bets_days: list[str] = []
    missing_settlement_days: list[str] = []
    prediction_review_days = 0
    prediction_sheet_days = 0

    for date_iso in dates:
        sheet_payload = _load_day_json(REPORTS_PRED_ROOT, date_iso, "prediction_sheet.json")
        review_payload = _load_day_json(REPORTS_PRED_ROOT, date_iso, "prediction_review.json")
        frozen_payload = _load_day_json(REPORTS_PRED_ROOT, date_iso, "frozen_bets.json")
        daily_report = _load_daily_report(date_iso)
        report_prediction_index, report_prediction_key_index, report_settlement_index = _build_daily_report_indexes(
            daily_report, date_iso
        )
        settlement_rows = _load_settlement_rows(date_iso)
        settlement_index = _build_settlement_index(settlement_rows)
        for race_id, row in report_settlement_index.items():
            settlement_index[race_id] = row
        sheet_rows = sheet_payload.get("candidates") if isinstance(sheet_payload.get("candidates"), list) else []
        review_rows = review_payload.get("topCandidates") if isinstance(review_payload.get("topCandidates"), list) else []
        if review_rows:
            prediction_review_days += 1
        if sheet_rows:
            prediction_sheet_days += 1

        sheet_index = _build_sheet_index(sheet_rows)
        frozen_by_candidate_id, frozen_by_hash, frozen_by_race_combo = _build_frozen_indexes(frozen_payload)

        daily_monitor_row = daily_summary_index.get(date_iso, {})
        candidate_count_authoritative = int(daily_monitor_row.get("paperCandidateCount") or 0)
        eligible_count_authoritative = int(daily_monitor_row.get("paperEligibleCandidateCount") or 0)
        settled_count_authoritative = int(daily_monitor_row.get("paperSettledCandidateCount") or 0)
        ineligible_count_authoritative = int(daily_monitor_row.get("paperIneligibleCandidateCount") or 0)
        pending_count_authoritative = int(daily_monitor_row.get("paperPendingCandidateCount") or 0)
        paper_validation_coverage = daily_monitor_row.get("paperSettlementCoverage")

        day_rows = [row for row in sheet_rows if _normalize_text(row.get("paperDecision")).upper() in {"PAPER", "WATCH"}]
        if not day_rows and review_rows:
            # fallback for dates where the review sheet exists but the raw sheet was unavailable
            for row in review_rows:
                copied = dict(row)
                copied.setdefault("paperDecision", row.get("paperDecision") or "PAPER")
                day_rows.append(copied)
        if not day_rows:
            if not sheet_rows:
                missing_prediction_sheet_days.append(date_iso)
            if not frozen_payload:
                missing_frozen_bets_days.append(date_iso)
            if not settlement_rows:
                missing_settlement_days.append(date_iso)
            day_summaries.append(
                {
                    "date": date_iso,
                    "candidate_rows": 0,
                    "sheet_rows": 0,
                    "review_rows": len(review_rows),
                    "settlement_rows": len(settlement_rows),
                    "traceable_rows": 0,
                    "complete_count": 0,
                    "result_unconfirmed_count": 0,
                    "missing_prediction_sheet_count": 0,
                    "missing_frozen_bets_count": 0,
                    "missing_settlement_count": 0,
                    "hash_mismatch_count": 0,
                    "race_mismatch_count": 0,
                    "combo_mismatch_count": 0,
                    "authoritativePaperCandidateCount": candidate_count_authoritative,
                    "authoritativePaperEligibleCandidateCount": eligible_count_authoritative,
                    "authoritativePaperSettledCandidateCount": settled_count_authoritative,
                    "authoritativePaperIneligibleCandidateCount": ineligible_count_authoritative,
                    "authoritativePaperPendingCandidateCount": pending_count_authoritative,
                    "paperSettlementCoverage": paper_validation_coverage,
                    "resultStatus": daily_monitor_row.get("resultStatus", ""),
                }
            )
            continue

        if not sheet_rows:
            missing_prediction_sheet_days.append(date_iso)
        if not frozen_payload:
            missing_frozen_bets_days.append(date_iso)
        if not settlement_rows:
            missing_settlement_days.append(date_iso)

        day_traceable = 0
        day_complete = 0
        day_unconfirmed = 0
        day_missing_prediction = 0
        day_missing_frozen = 0
        day_missing_settlement = 0
        day_hash_mismatch = 0
        day_race_mismatch = 0
        day_combo_mismatch = 0

        for raw_row in day_rows:
            probe_row = raw_row
            race_id = _normalize_text(probe_row.get("raceId") or probe_row.get("race_id"))
            combo = _normalize_text(probe_row.get("combo"))
            paper_decision = _normalize_text(probe_row.get("paperDecision")).upper()
            final_decision = _normalize_text(probe_row.get("finalDecision"))
            stop_reason = _normalize_text(probe_row.get("stopReason"))
            key = (race_id, combo, paper_decision)
            sheet_row = sheet_index.get(key)
            if not sheet_row:
                # fallback by race/combo only
                for candidate_key, candidate_sheet_row in sheet_index.items():
                    if candidate_key[0] == race_id and candidate_key[1] == combo:
                        sheet_row = candidate_sheet_row
                        break
            base_row = sheet_row or raw_row
            race_id = _normalize_text(base_row.get("raceId") or base_row.get("race_id"))
            combo = _normalize_text(base_row.get("combo"))
            paper_decision = _normalize_text(base_row.get("paperDecision")).upper()
            final_decision = _normalize_text(base_row.get("finalDecision"))
            stop_reason = _normalize_text(base_row.get("stopReason"))
            prediction_hash = _normalize_text(base_row.get("predictionHash"))
            candidate_id = _candidate_id(date_iso, race_id, combo, prediction_hash)
            report_prediction_row = report_prediction_index.get(candidate_id)
            if report_prediction_row is None and race_id and combo and prediction_hash:
                report_prediction_row = report_prediction_key_index.get((race_id, combo, prediction_hash))
            frozen_row = frozen_by_candidate_id.get(candidate_id)
            if frozen_row is None and prediction_hash and prediction_hash in frozen_by_hash:
                frozen_row = frozen_by_hash[prediction_hash]
            if frozen_row is None and race_id and combo and (race_id, combo) in frozen_by_race_combo:
                frozen_row = frozen_by_race_combo[(race_id, combo)]
            candidate_id = (
                _normalize_text(base_row.get("candidateId"))
                or _normalize_text(frozen_row.get("candidateId") if frozen_row else "")
                or _normalize_text((report_prediction_row or {}).get("candidateId"))
                or candidate_id
            )
            settlement_row = settlement_index.get(race_id)

            trace_status, trace_reason = _candidate_status(
                sheet_row=sheet_row or raw_row,
                frozen_row=frozen_row,
                settlement_row=settlement_row,
            )
            if trace_status == "missing_prediction_sheet":
                day_missing_prediction += 1
            elif trace_status == "missing_frozen_bets":
                day_missing_frozen += 1
            elif trace_status == "missing_settlement":
                day_missing_settlement += 1
            elif trace_status == "hash_mismatch":
                day_hash_mismatch += 1
            elif trace_status == "race_mismatch":
                day_race_mismatch += 1
            elif trace_status == "combo_mismatch":
                day_combo_mismatch += 1

            if trace_status == "complete":
                day_complete += 1
            if trace_status in {"complete", "result_unconfirmed"}:
                day_traceable += 1
            if trace_status == "result_unconfirmed":
                day_unconfirmed += 1

            settlement_result_status = _normalize_text(
                settlement_row.get("resultStatus") if settlement_row else ""
            )
            if not settlement_result_status and settlement_row:
                if _truthy(settlement_row.get("result_available")):
                    settlement_result_status = "available"
            settlement_result_available = _truthy(settlement_row.get("result_available")) if settlement_row else False
            if settlement_row and not settlement_result_available:
                settlement_result_available = settlement_result_status.lower() in {"ok", "available", "settled"}
            settlement_hit = settlement_row.get("hit") if settlement_row else None
            settlement_predicted_trifecta = _normalize_text(
                settlement_row.get("predicted_trifecta") if settlement_row else ""
            )
            settlement_actual_trifecta = ""
            settlement_payout = None
            if settlement_row:
                settlement_actual_trifecta = _normalize_text(
                    settlement_row.get("actualTrifecta")
                    or settlement_row.get("actual_trifecta")
                    or (
                        settlement_row.get("result", {}).get("trifectaCombo")
                        if isinstance(settlement_row.get("result"), dict)
                        else ""
                    )
                )
                payout_text = _normalize_text(settlement_row.get("payoutAmount"))
                settlement_payout = payout_text or settlement_row.get("payout_amount")
                if settlement_payout in {"", None} and isinstance(settlement_row.get("result"), dict):
                    settlement_payout = settlement_row["result"].get("trifectaPayout")
            settlement_settled_at = _normalize_text(
                (daily_report.get("generatedAt") if isinstance(daily_report, dict) else "")
                or (settlement_row.get("generatedAt") if settlement_row else "")
            )
            report_prob = _maybe_float(report_prediction_row.get("prob") if report_prediction_row else None)
            approx_prob = _maybe_float((sheet_row or raw_row).get("approxProb"))
            odds_value = _maybe_float((report_prediction_row or {}).get("odds"))
            if odds_value is None:
                odds_value = _maybe_float((sheet_row or raw_row).get("realOdds"))
            market_probability = (1.0 / odds_value) if odds_value and odds_value > 0 else None
            estimated_edge = _maybe_float((report_prediction_row or {}).get("edge"))
            if estimated_edge is None:
                estimated_edge = _maybe_float((sheet_row or raw_row).get("expectedValue"))
            raw_probability = approx_prob if approx_prob is not None else report_prob
            calibrated_probability = report_prob if report_prob is not None else approx_prob
            model_version = _normalize_text(
                (report_prediction_row or {}).get("modelVersion")
                or base_row.get("modelVersion")
                or (frozen_row.get("modelVersion") if frozen_row else "")
            )
            if not model_version:
                model_version = LEGACY_UNKNOWN
            candidate_trace_row = {
                "candidateId": candidate_id,
                "raceId": race_id,
                "raceDate": date_iso,
                "venueCode": _normalize_text((sheet_row or raw_row).get("jcd")),
                "raceNo": int((sheet_row or raw_row).get("raceNo") or (sheet_row or raw_row).get("race_no") or 0),
                "combination": combo,
                "snapshotId": LEGACY_UNKNOWN,
                "snapshotHash": _normalize_text(base_row.get("snapshotHash") or (frozen_row.get("snapshotHash") if frozen_row else "")) or LEGACY_UNKNOWN,
                "snapshotCapturedAt": _normalize_text(sheet_payload.get("generatedAt")) or LEGACY_UNKNOWN,
                "featureVersion": _normalize_text(base_row.get("featureVersion") or (frozen_row.get("featureVersion") if frozen_row else "")) or LEGACY_UNKNOWN,
                "featureHash": LEGACY_UNKNOWN,
                "modelVersion": model_version,
                "calibratorVersion": _normalize_text(base_row.get("calibratorVersion") or (frozen_row.get("calibratorVersion") if frozen_row else "")) or LEGACY_UNKNOWN,
                "predictionHash": prediction_hash,
                "rawProbability": base_row.get("rawProbability") or (frozen_row.get("rawProbability") if frozen_row else "") or raw_probability,
                "calibratedProbability": base_row.get("calibratedProbability") or (frozen_row.get("calibratedProbability") if frozen_row else "") or calibrated_probability,
                "odds": odds_value,
                "oddsCapturedAt": _normalize_text(base_row.get("oddsCapturedAt") or (frozen_row.get("oddsCapturedAt") if frozen_row else "")) or LEGACY_UNKNOWN,
                "deadlineAt": _normalize_text(base_row.get("deadlineAt") or (frozen_row.get("deadlineAt") if frozen_row else "") or (sheet_row or raw_row).get("deadline")) or LEGACY_UNKNOWN,
                "marketProbability": market_probability,
                "estimatedEdge": estimated_edge,
                "policyVersion": _normalize_text(base_row.get("policyVersion") or (frozen_row.get("policyVersion") if frozen_row else "")) or LEGACY_UNKNOWN,
                "policyDecision": _normalize_text(base_row.get("policyDecision")) or paper_decision,
                "guardDecision": _normalize_text(base_row.get("guardDecision") or (frozen_row.get("guardDecision") if frozen_row else "")) or final_decision,
                "guardReason": _normalize_text(base_row.get("guardReason") or (frozen_row.get("guardReason") if frozen_row else "")) or stop_reason or _normalize_text((report_prediction_row or {}).get("reason")) or LEGACY_UNKNOWN,
                "frozenBetId": _normalize_text(frozen_row.get("frozenBetId") if frozen_row else "") or LEGACY_UNKNOWN,
                "frozenAt": _normalize_text((frozen_row.get("frozenAt") if frozen_row else "") or frozen_payload.get("generatedAt")) or LEGACY_UNKNOWN,
                "resultCombination": settlement_actual_trifecta,
                "payout": settlement_payout,
                "settlementStatus": settlement_result_status or LEGACY_UNKNOWN,
                "settledAt": settlement_settled_at or LEGACY_UNKNOWN,
            }
            trace_reason_category = _trace_reason_category(trace_status, candidate_trace_row)
            candidate_trace_row["traceReasonCategory"] = trace_reason_category

            row = {
                "date": date_iso,
                "venue": _normalize_text((sheet_row or raw_row).get("venue")),
                "jcd": _normalize_text((sheet_row or raw_row).get("jcd")),
                "race_no": int((sheet_row or raw_row).get("raceNo") or (sheet_row or raw_row).get("race_no") or 0),
                "race_id": race_id,
                "paperDecision": paper_decision,
                "finalDecision": final_decision,
                "stopReason": stop_reason,
                "oddsStatus": _normalize_text((sheet_row or raw_row).get("oddsStatus")),
                "approxProb": (sheet_row or raw_row).get("approxProb"),
                "realOdds": (sheet_row or raw_row).get("realOdds"),
                "expectedValue": (sheet_row or raw_row).get("expectedValue"),
                "riskFlag": (sheet_row or raw_row).get("riskFlag"),
                "confidenceRank": (sheet_row or raw_row).get("confidenceRank"),
                "combo": combo,
                "predictionHashComputed": _normalize_text(frozen_row.get("predictionHashComputed") if frozen_row else ""),
                "predictionHashMatch": bool(
                    prediction_hash
                    and prediction_hash
                    == _normalize_text(frozen_row.get("predictionHashComputed") if frozen_row else "")
                ),
                "frozenExists": bool(frozen_row),
                "frozenFreezeType": _normalize_text(frozen_payload.get("freezeType")),
                "frozenSourceType": _normalize_text(frozen_row.get("sourceType") if frozen_row else ""),
                "frozenPredictionSource": _normalize_text(frozen_row.get("predictionSource") if frozen_row else ""),
                "settlementExists": bool(settlement_row),
                "resultAvailable": settlement_result_available,
                "resultStatus": settlement_result_status or _normalize_text(settlement_row.get("resultStatus") if settlement_row else ""),
                "hit": settlement_hit,
                "actualTrifecta": settlement_actual_trifecta,
                "predictedTrifecta": settlement_predicted_trifecta,
                "settlementPredictedTrifectaMatch": bool(
                    combo and settlement_predicted_trifecta and combo == settlement_predicted_trifecta
                ),
                "settledOdds": settlement_row.get("settled_odds") if settlement_row else "",
                "payoutAmount": settlement_row.get("payout_amount") if settlement_row else "",
                "pnl": settlement_row.get("pnl") if settlement_row else "",
                "traceStatus": trace_status,
                "traceReason": trace_reason,
                "traceReasonCategory": trace_reason_category,
                "candidateId": candidate_trace_row["candidateId"],
                "raceId": candidate_trace_row["raceId"],
                "raceDate": candidate_trace_row["raceDate"],
                "venueCode": candidate_trace_row["venueCode"],
                "raceNo": candidate_trace_row["raceNo"],
                "combination": candidate_trace_row["combination"],
                "snapshotId": candidate_trace_row["snapshotId"],
                "snapshotHash": candidate_trace_row["snapshotHash"],
                "snapshotCapturedAt": candidate_trace_row["snapshotCapturedAt"],
                "featureVersion": candidate_trace_row["featureVersion"],
                "featureHash": candidate_trace_row["featureHash"],
                "modelVersion": candidate_trace_row["modelVersion"],
                "calibratorVersion": candidate_trace_row["calibratorVersion"],
                "predictionHash": candidate_trace_row["predictionHash"],
                "rawProbability": candidate_trace_row["rawProbability"],
                "calibratedProbability": candidate_trace_row["calibratedProbability"],
                "odds": candidate_trace_row["odds"],
                "oddsCapturedAt": candidate_trace_row["oddsCapturedAt"],
                "deadlineAt": candidate_trace_row["deadlineAt"],
                "marketProbability": candidate_trace_row["marketProbability"],
                "estimatedEdge": candidate_trace_row["estimatedEdge"],
                "policyVersion": candidate_trace_row["policyVersion"],
                "policyDecision": candidate_trace_row["policyDecision"],
                "guardDecision": candidate_trace_row["guardDecision"],
                "guardReason": candidate_trace_row["guardReason"],
                "frozenBetId": candidate_trace_row["frozenBetId"],
                "frozenAt": candidate_trace_row["frozenAt"],
                "resultCombination": candidate_trace_row["resultCombination"],
                "payout": candidate_trace_row["payout"],
                "settlementStatus": candidate_trace_row["settlementStatus"],
                "settledAt": candidate_trace_row["settledAt"],
                "monitorPaperCandidateCount": candidate_count_authoritative,
                "monitorPaperEligibleCandidateCount": eligible_count_authoritative,
                "monitorPaperSettledCandidateCount": settled_count_authoritative,
                "monitorPaperIneligibleCandidateCount": ineligible_count_authoritative,
                "monitorPaperPendingCandidateCount": pending_count_authoritative,
                "monitorPaperSettlementCoverage": paper_validation_coverage,
                "monitorResultStatus": _normalize_text(daily_monitor_row.get("resultStatus")),
            }
            candidate_rows.append(row)
            trace_status_counts[trace_status] += 1
            trace_reason_counts[trace_reason] += 1
            trace_reason_category_counts[trace_reason_category] += 1
            if settlement_result_available:
                result_status_counts["result_available"] += 1
            else:
                result_status_counts["result_unconfirmed"] += 1
            source_counts["prediction_sheet"] += 1
            if frozen_row:
                source_counts["frozen_bets"] += 1
            if settlement_row:
                source_counts["settlement"] += 1
            if report_prediction_row:
                source_counts["daily_report_prediction"] += 1

        day_summaries.append(
            {
                "date": date_iso,
                "candidate_rows": len(day_rows),
                "sheet_rows": len(day_rows),
                "review_rows": len(review_rows),
                "settlement_rows": len(settlement_rows),
                "traceable_rows": day_traceable,
                "complete_count": day_complete,
                "result_unconfirmed_count": day_unconfirmed,
                "missing_prediction_sheet_count": day_missing_prediction,
                "missing_frozen_bets_count": day_missing_frozen,
                "missing_settlement_count": day_missing_settlement,
                "hash_mismatch_count": day_hash_mismatch,
                "race_mismatch_count": day_race_mismatch,
                "combo_mismatch_count": day_combo_mismatch,
                "authoritativePaperCandidateCount": candidate_count_authoritative,
                "authoritativePaperEligibleCandidateCount": eligible_count_authoritative,
                "authoritativePaperSettledCandidateCount": settled_count_authoritative,
                "authoritativePaperIneligibleCandidateCount": ineligible_count_authoritative,
                "authoritativePaperPendingCandidateCount": pending_count_authoritative,
                "paperSettlementCoverage": paper_validation_coverage,
                "resultStatus": daily_monitor_row.get("resultStatus", ""),
            }
        )

    for category in [
        "complete",
        "legacy_field_missing",
        "frozen_not_created",
        "no_shadow_decision",
        "no_settlement",
        "scope_mismatch",
        "prediction_hash_mismatch",
        "unknown",
    ]:
        trace_reason_category_counts.setdefault(category, 0)

    candidate_id_duplicates = max(0, len(candidate_rows) - len({row.get("candidateId") for row in candidate_rows}))
    traced_candidate_count = int(sum(1 for row in candidate_rows if row["traceStatus"] == "complete"))
    traceable_candidate_count = int(sum(1 for row in candidate_rows if row["traceStatus"] in {"complete", "result_unconfirmed"}))
    eligible_candidate_count = int(scope.get("authoritativePaperEligibleCandidateCount", 0) or 0)
    trace_coverage = round(traced_candidate_count / len(candidate_rows), 4) if candidate_rows else None
    traceable_coverage = round(traceable_candidate_count / len(candidate_rows), 4) if candidate_rows else None

    canonical_missing_counts = {
        "candidateIdMissing": int(sum(1 for row in candidate_rows if _is_missing_legacy(row.get("candidateId")))),
        "predictionHashMissing": int(sum(1 for row in candidate_rows if _is_missing_legacy(row.get("predictionHash")))),
        "modelVersionMissing": int(sum(1 for row in candidate_rows if _is_missing_legacy(row.get("modelVersion")))),
        "policyVersionMissing": int(sum(1 for row in candidate_rows if _is_missing_legacy(row.get("policyVersion")))),
        "oddsMissing": int(sum(1 for row in candidate_rows if _is_missing_legacy(row.get("odds")))),
        "oddsCapturedAtMissing": int(sum(1 for row in candidate_rows if _is_missing_legacy(row.get("oddsCapturedAt")))),
        "deadlineViolationCount": int(
            sum(
                1
                for row in candidate_rows
                if _parse_iso_datetime_text(row.get("oddsCapturedAt"))
                and _parse_iso_datetime_text(row.get("deadlineAt"))
                and _parse_iso_datetime_text(row.get("oddsCapturedAt"))
                >= _parse_iso_datetime_text(row.get("deadlineAt"))
            )
        ),
        "frozenJoinCount": int(sum(1 for row in candidate_rows if _truthy(row.get("frozenExists")))),
        "settlementJoinCount": int(sum(1 for row in candidate_rows if _truthy(row.get("settlementExists")))),
        "settlementJoinFailed": int(sum(1 for row in candidate_rows if row["traceStatus"] == "missing_settlement")),
    }

    summary_payload = {
        "dateRange": f"{start_iso.replace('-', '')}_{end_iso.replace('-', '')}",
        "startDate": start_iso,
        "endDate": end_iso,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "candidateQualityReview": str(CANDIDATE_QUALITY_REVIEW),
            "paperValidationSummary": str(PAPER_VALIDATION_SUMMARY),
            "predictionSheetRoot": str(REPORTS_PRED_ROOT),
            "frozenBetsRoot": str(REPORTS_PRED_ROOT),
            "settlementRoot": str(REPORTS_DAILY_ROOT),
        },
        "authoritative": {
            "paperValidationReady": bool(scope.get("authoritativePaperValidationReady", current_state.get("paperValidationReady", False))),
            "paperCandidateCount": int(scope.get("authoritativePaperCandidateCount", 0) or 0),
            "paperEligibleCandidateCount": int(scope.get("authoritativePaperEligibleCandidateCount", 0) or 0),
            "rawSheetScanRows": int(scope.get("rawSheetScanRows", 0) or 0),
            "rawScanVsGateNote": str(scope.get("rawScanVsGateNote") or ""),
            "qualityClassification": str(quality.get("quality", {}).get("classification") or ""),
            "sameDayDuplicateCount": int(quality.get("quality", {}).get("sameDayDuplicateCount") or 0),
            "repeatedHashCountAcrossDates": int(quality.get("quality", {}).get("repeatedHashCountAcrossDates") or 0),
            "requiredMissingCount": int(quality.get("quality", {}).get("requiredMissingCount") or 0),
            "anomalyCount": int(quality.get("quality", {}).get("anomalyCount") or 0),
        },
        "monitoring": {
            "paperValidationReady": bool(summary.get("summary", {}).get("paperValidationReady", False)),
            "paperCandidateCount": int(summary.get("summary", {}).get("paperCandidateCount", 0) or 0),
            "paperEligibleCandidateCount": int(summary.get("summary", {}).get("paperEligibleCandidateCount", 0) or 0),
            "paperSettledCandidateCount": int(summary.get("summary", {}).get("paperSettledCandidateCount", 0) or 0),
            "paperSettlementCoverage": summary.get("summary", {}).get("paperSettlementCoverage"),
            "predictionHashMissingDays": int(summary.get("summary", {}).get("predictionHashMissingDays", 0) or 0),
            "frozenBetsMissingDays": int(summary.get("summary", {}).get("frozenBetsMissingDays", 0) or 0),
        },
        "counts": {
            "candidateRowsScanned": len(candidate_rows),
            "eligibleCandidateCount": eligible_candidate_count,
            "tracedCandidateCount": traced_candidate_count,
            "traceCoverage": trace_coverage,
            "traceableCandidateCount": traceable_candidate_count,
            "traceableCoverage": traceable_coverage,
            "candidateIdDuplicateCount": candidate_id_duplicates,
            "predictionReviewDays": prediction_review_days,
            "predictionSheetDays": prediction_sheet_days,
            "traceableRows": int(sum(1 for row in candidate_rows if row["traceStatus"] in {"complete", "result_unconfirmed"})),
            "completeRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "complete")),
            "resultUnconfirmedRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "result_unconfirmed")),
            "missingPredictionSheetRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "missing_prediction_sheet")),
            "missingFrozenBetsRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "missing_frozen_bets")),
            "missingSettlementRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "missing_settlement")),
            "hashMismatchRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "hash_mismatch")),
            "raceMismatchRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "race_mismatch")),
            "comboMismatchRows": int(sum(1 for row in candidate_rows if row["traceStatus"] == "combo_mismatch")),
            "resultAvailableRows": int(sum(1 for row in candidate_rows if _truthy(row.get("resultAvailable")))),
            "resultUnconfirmedCountByJoin": int(sum(1 for row in candidate_rows if not _truthy(row.get("resultAvailable")))),
        },
        "traceStatusCounts": dict(sorted(trace_status_counts.items())),
        "traceReasonCounts": dict(sorted(trace_reason_counts.items())),
        "traceReasonCategoryCounts": dict(sorted(trace_reason_category_counts.items())),
        "resultStatusCounts": dict(sorted(result_status_counts.items())),
        "sourceCounts": dict(sorted(source_counts.items())),
        "canonicalMissingCounts": canonical_missing_counts,
        "days": day_summaries,
    }
    summary_payload["traceCoveragePct"] = (
        round(100.0 * summary_payload["counts"]["completeRows"] / summary_payload["counts"]["candidateRowsScanned"], 2)
        if summary_payload["counts"]["candidateRowsScanned"]
        else 0.0
    )
    summary_payload["traceCoverageEligiblePct"] = (
        round(100.0 * min(traced_candidate_count, eligible_candidate_count) / eligible_candidate_count, 2)
        if eligible_candidate_count
        else None
    )
    if candidate_id_duplicates > 0 or summary_payload["counts"]["missingPredictionSheetRows"] > 0 or summary_payload["counts"]["missingFrozenBetsRows"] > 0:
        quality_classification = "trace_blocked"
    elif (
        summary_payload["counts"]["candidateRowsScanned"]
        and summary_payload["counts"]["traceableRows"] == summary_payload["counts"]["candidateRowsScanned"]
        and summary_payload["counts"]["missingSettlementRows"] == 0
        and all(value == 0 for value in canonical_missing_counts.values())
    ):
        quality_classification = "trace_ready"
    else:
        quality_classification = "trace_warning"
    summary_payload["quality"] = {
        "classification": quality_classification,
        "candidateIdDuplicateCount": candidate_id_duplicates,
        "notes": "monitoring counts and canonical trace coverage",
    }

    md_lines = [
        "# Candidate Trace Audit",
        "",
        f"- dateRange: {start_iso} to {end_iso}",
        f"- authoritativePaperCandidateCount: {summary_payload['authoritative']['paperCandidateCount']}",
        f"- authoritativePaperEligibleCandidateCount: {summary_payload['authoritative']['paperEligibleCandidateCount']}",
        f"- paperValidationReady: {summary_payload['authoritative']['paperValidationReady']}",
        f"- rawSheetScanRows: {summary_payload['authoritative']['rawSheetScanRows']}",
        f"- candidateRowsScanned: {summary_payload['counts']['candidateRowsScanned']}",
        f"- eligibleCandidateCount: {summary_payload['counts']['eligibleCandidateCount']}",
        f"- tracedCandidateCount: {summary_payload['counts']['tracedCandidateCount']}",
        f"- traceCoverage: {summary_payload['counts']['traceCoverage']}",
        f"- traceCoverageEligiblePct: {summary_payload['traceCoverageEligiblePct']}%",
        f"- traceableRows: {summary_payload['counts']['traceableRows']}",
        f"- completeRows: {summary_payload['counts']['completeRows']}",
        f"- resultUnconfirmedRows: {summary_payload['counts']['resultUnconfirmedRows']}",
        f"- missingSettlementRows: {summary_payload['counts']['missingSettlementRows']}",
        f"- hashMismatchRows: {summary_payload['counts']['hashMismatchRows']}",
        f"- raceMismatchRows: {summary_payload['counts']['raceMismatchRows']}",
        f"- comboMismatchRows: {summary_payload['counts']['comboMismatchRows']}",
        f"- candidateIdDuplicateCount: {summary_payload['counts']['candidateIdDuplicateCount']}",
        f"- predictionHashMissingDays: {summary_payload['monitoring']['predictionHashMissingDays']}",
        f"- frozenBetsMissingDays: {summary_payload['monitoring']['frozenBetsMissingDays']}",
        f"- traceCoveragePct: {summary_payload['traceCoveragePct']}%",
        f"- quality: {summary_payload['quality']['classification']}",
        "",
        "## Trace status counts",
    ]
    for key, value in summary_payload["traceStatusCounts"].items():
        md_lines.append(f"- {key}: {value}")
    md_lines.append("")
    md_lines.append("## Trace reason categories")
    for key, value in summary_payload["traceReasonCategoryCounts"].items():
        md_lines.append(f"- {key}: {value}")
    md_lines.extend(
        [
            "",
            "## Canonical missing counts",
        ]
    )
    for key, value in summary_payload["canonicalMissingCounts"].items():
        md_lines.append(f"- {key}: {value}")
    md_lines.extend(
        [
            "",
            "## Quality",
            f"- classification: {summary_payload['quality']['classification']}",
            f"- candidateIdDuplicateCount: {summary_payload['quality']['candidateIdDuplicateCount']}",
            f"- notes: {summary_payload['quality']['notes']}",
        ]
    )
    md_lines.extend(
        [
            "",
            "## Source notes",
            f"- candidateQualityReview: {summary_payload['sources']['candidateQualityReview']}",
            f"- paperValidationSummary: {summary_payload['sources']['paperValidationSummary']}",
            f"- frozenLedgerSource: reports/predictions/YYYY-MM-DD/frozen_bets.json",
            f"- settlementSource: reports/daily/YYYY-MM-DD/daily_report.json (fallback: daily_evaluation_race_results.csv)",
            "",
            "## Notes",
            "- predictionHash を推測補完しない",
            "- frozen_bets を上書きしない",
            "- BUY / EV / 投票 / 本番購入には接続しない",
        ]
    )

    return {
        "summary": summary_payload,
        "rows": candidate_rows,
        "markdown": "\n".join(md_lines) + "\n",
        "dateRange": {"start": start_iso, "end": end_iso},
    }


def _build_row_fieldnames() -> list[str]:
    return [
        *CANONICAL_FIELDNAMES,
        *TRACE_METADATA_FIELDNAMES,
        "date",
        "venue",
        "jcd",
        "race_no",
        "race_id",
        "paperDecision",
        "finalDecision",
        "stopReason",
        "oddsStatus",
        "approxProb",
        "realOdds",
        "expectedValue",
        "riskFlag",
        "confidenceRank",
        "combo",
        "predictionHash",
        "predictionHashComputed",
        "predictionHashMatch",
        "frozenExists",
        "frozenFreezeType",
        "frozenSourceType",
        "frozenPredictionSource",
        "settlementExists",
        "resultAvailable",
        "resultStatus",
        "hit",
        "actualTrifecta",
        "predictedTrifecta",
        "settlementPredictedTrifectaMatch",
        "settledOdds",
        "payoutAmount",
        "pnl",
        "traceStatus",
        "traceReason",
        "monitorPaperCandidateCount",
        "monitorPaperEligibleCandidateCount",
        "monitorPaperSettledCandidateCount",
        "monitorPaperIneligibleCandidateCount",
        "monitorPaperPendingCandidateCount",
        "monitorPaperSettlementCoverage",
        "monitorResultStatus",
    ]


def _main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Build candidate trace audit")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    args = parser.parse_args(argv)

    audit = build_candidate_trace_audit(start=args.start, end=args.end)
    if not args.dry_run:
        _save_json(OUT_JSON, audit["summary"])
        _save_text(OUT_MD, audit["markdown"])
        _save_csv(OUT_CSV, audit["rows"], _build_row_fieldnames())

    print(json.dumps(
        {
            "dateRange": audit["dateRange"],
            "summaryPath": str(OUT_JSON),
            "rowsPath": str(OUT_CSV),
            "mdPath": str(OUT_MD),
            "counts": audit["summary"]["counts"],
            "traceStatusCounts": audit["summary"]["traceStatusCounts"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
