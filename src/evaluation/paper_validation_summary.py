from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"
REPORTS_ANALYSIS_ROOT = ROOT / "reports" / "analysis"
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORTS_REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"
WATCH_PAPER_PERFORMANCE_JSON = REPORTS_ANALYSIS_ROOT / "watch_paper_performance.json"


def _normalize_date(value: str) -> str:
    token = str(value).strip().lower()
    if token == "today":
        return date.today().strftime("%Y%m%d")
    digits = "".join(ch for ch in token if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _daterange(start8: str, end8: str) -> list[str]:
    start = datetime.strptime(start8, "%Y%m%d").date()
    end = datetime.strptime(end8, "%Y%m%d").date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_combo(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = value
    else:
        text = str(value).strip().replace("－", "-").replace("–", "-").replace("—", "-").replace(" ", "")
        if not text:
            return ""
        parts = text.split("-") if "-" in text else [text]
    cleaned = []
    for part in parts:
        token = "".join(ch for ch in str(part) if ch.isdigit())
        if token:
            cleaned.append(token)
    return "-".join(cleaned)


def _normalize_race_key(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    text = text.replace("－", "-").replace("–", "-").replace("—", "-").replace("＿", "_").replace(" ", "")
    text = re.sub(r"[()]", "", text)
    patterns = (
        r"^d?(\d{8})[-_]?v?(\d{1,2})[-_]?r?(\d{1,2})$",
        r"^(\d{8})[-_](\d{1,2})[-_](\d{1,2})$",
        r"^jcd(\d{1,2})r?(\d{1,2})$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            if len(match.groups()) == 3:
                date8, jcd, race_no = match.groups()
                return f"d{date8}-v{int(jcd):02d}-r{int(race_no):02d}"
            if len(match.groups()) == 2 and text.startswith("jcd"):
                jcd, race_no = match.groups()
                return f"jcd{int(jcd):02d}-r{int(race_no):02d}"
            if len(match.groups()) == 2:
                date8, race_no = match.groups()
                return f"d{date8}-r{int(race_no):02d}"
    if "-" in text:
        parts = [part for part in text.split("-") if part]
        if len(parts) == 3 and len(parts[0]) == 8:
            date8, jcd, race_no = parts
            return f"d{date8}-v{int(jcd):02d}-r{int(race_no):02d}"
    return text


def _candidate_race_keys(candidate: dict[str, Any], date_iso: str) -> list[str]:
    keys: list[str] = []
    raw_candidates = (
        candidate.get("raceId"),
        candidate.get("race_id"),
        candidate.get("matched_race_id"),
        candidate.get("race_id_result"),
    )
    for raw in raw_candidates:
        token = _normalize_race_key(raw)
        if token and token not in keys:
            keys.append(token)
    race_no = candidate.get("raceNo") if candidate.get("raceNo") is not None else candidate.get("race_no")
    jcd = candidate.get("jcd")
    date8 = date_iso.replace("-", "")
    if race_no not in (None, ""):
        try:
            race_no_int = int(str(race_no))
        except Exception:
            race_no_int = None
        if race_no_int is not None:
            if jcd not in (None, ""):
                try:
                    jcd_int = int(str(jcd))
                    token = f"d{date8}-v{jcd_int:02d}-r{race_no_int:02d}"
                    if token not in keys:
                        keys.append(token)
                except Exception:
                    pass
            venue = str(candidate.get("venue") or "").strip()
            if venue:
                token = _normalize_race_key(f"{date8}_{venue}_{race_no_int}")
                if token and token not in keys:
                    keys.append(token)
    return keys


def _load_daily_result_index(date_iso: str) -> dict[str, dict[str, Any]]:
    path = REPORTS_DAILY_ROOT / date_iso / "daily_evaluation_race_results.csv"
    if not path.exists():
        return {}
    index: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not isinstance(row, dict):
                    continue
                for key in (
                    row.get("matched_race_id"),
                    row.get("race_id_result"),
                    row.get("normalized_race_key"),
                    row.get("normalized_race_key_legacy"),
                ):
                    token = _normalize_race_key(key)
                    if token and token not in index:
                        index[token] = row
                date_result = str(row.get("date_result") or row.get("date") or "").strip().replace("-", "")
                race_no = row.get("race_no") or row.get("raceNo")
                jcd = row.get("jcd")
                if date_result and race_no not in (None, "") and jcd not in (None, ""):
                    try:
                        token = f"d{date_result}-v{int(str(jcd)):02d}-r{int(str(race_no)):02d}"
                        if token not in index:
                            index[token] = row
                    except Exception:
                        pass
    except Exception:
        return {}
    return index


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _compact_to_iso(date8: str) -> str:
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"


def _load_prediction_review(date_iso: str) -> dict[str, Any]:
    for root in (REPORTS_PREDICTIONS_ROOT, REPORTS_DAILY_ROOT):
        for candidate in (
            root / date_iso / "prediction_review.json",
            root / date_iso.replace("-", "") / "prediction_review.json",
        ):
            payload = _load_json(candidate)
            if payload:
                return payload
    return {}


def _load_daily_summary(date_iso: str) -> dict[str, Any]:
    for candidate in (
        REPORTS_DAILY_ROOT / date_iso / "daily_summary.json",
        REPORTS_DAILY_ROOT / date_iso / "daily_report.json",
        REPORTS_DAILY_ROOT / date_iso.replace("-", "") / "daily_summary.json",
        REPORTS_DAILY_ROOT / date_iso.replace("-", "") / "daily_report.json",
        REPORTS_DAILY_ROOT / f"{date_iso.replace('-', '')}_summary.json",
        REPORTS_DAILY_ROOT / f"{date_iso.replace('-', '')}_settlement.json",
    ):
        payload = _load_json(candidate)
        if payload:
            return payload
    return {}


def _load_consensus_sheet(date_iso: str) -> dict[str, Any]:
    for candidate in (
        REPORTS_CONSENSUS_ROOT / date_iso / "consensus_sheet.json",
        REPORTS_CONSENSUS_ROOT / date_iso.replace("-", "") / "consensus_sheet.json",
    ):
        payload = _load_json(candidate)
        if payload:
            return payload
    return {}


def _load_watch_paper_performance() -> dict[str, Any]:
    payload = _load_json(WATCH_PAPER_PERFORMANCE_JSON)
    return payload.get("summary") if isinstance(payload.get("summary"), dict) else {}


def _result_available_from_row(row: dict[str, Any], result_status: str) -> bool:
    if result_status not in {"available", "ok", "settled"}:
        return False
    return str(row.get("result_available") or "").strip().lower() in {"1", "true", "yes"} or bool(str(row.get("actual_trifecta") or "").strip())


def _row_result_status(row: dict[str, Any], daily_result_status: str) -> str:
    for key in ("results_status", "result_status", "status"):
        token = str(row.get(key) or "").strip().lower()
        if token:
            return token
    return daily_result_status


def _resolve_result_row(
    candidate: dict[str, Any],
    date_iso: str,
    result_index: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    for key in _candidate_race_keys(candidate, date_iso):
        row = result_index.get(key)
        if isinstance(row, dict):
            return row, key
    return None, ""


def _int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(float(value))
    except Exception:
        return 0


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _grade_block(consensus_summary: dict[str, Any], grade: str) -> dict[str, Any]:
    by_grade = consensus_summary.get("byGrade") if isinstance(consensus_summary.get("byGrade"), dict) else {}
    row = by_grade.get(grade) if isinstance(by_grade.get(grade), dict) else {}
    return {
        "count": _int(row.get("count")),
        "resultAvailableCount": _int(row.get("resultAvailableCount")),
        "hitCount": _int(row.get("hitCount")),
        "resultPendingCount": _int(row.get("resultPendingCount")),
        "pnlSum": _float(row.get("pnlSum")) or 0.0,
        "returnRate": _float(row.get("returnRate")),
    }


def _load_live_summary() -> dict[str, Any]:
    payload = _load_json(REPORTS_MONITORING_ROOT / "live_operation_summary.json")
    return payload.get("summary") if isinstance(payload.get("summary"), dict) else {}


def _load_final_goal() -> dict[str, Any]:
    return _load_json(REPORTS_REPO_AUDIT_ROOT / "final_goal_progress.json")


def paper_validation_summary(*, start_date: str, end_date: str) -> dict[str, Any]:
    start8 = _normalize_date(start_date)
    end8 = _normalize_date(end_date)
    days = _daterange(start8, end8)
    live_summary = _load_live_summary()
    watch_paper_perf = _load_watch_paper_performance()
    final_goal = _load_final_goal()

    rows: list[dict[str, Any]] = []
    totals = {
        "watchCount": 0,
        "paperCount": 0,
        "watchSettledCount": 0,
        "paperSettledCount": 0,
        "watchHitCount": 0,
        "paperHitCount": 0,
        "watchResultAvailableCount": 0,
        "paperResultAvailableCount": 0,
        "consensusCount": 0,
        "consensusSettledCount": 0,
        "consensusHitCount": 0,
        "consensusResultPendingCount": 0,
        "paperCandidateCount": 0,
        "paperEligibleCandidateCount": 0,
        "paperSettledCandidateCount": 0,
        "paperIneligibleCandidateCount": 0,
        "paperPendingCandidateCount": 0,
        "paperSettlementCoverageRaw": 0.0,
        "paperSettlementCoverageEligible": 0.0,
        "ineligibleSourceNotReadyCount": 0,
        "raceIdMismatchCountBefore": 0,
        "raceIdMismatchCountAfter": 0,
        "backfillSettledCount": 0,
        "resultDataMissingDays": 0,
        "resultsAvailableDays": 0,
        "unconfirmedDays": 0,
        "predictionHashMissingDays": 0,
        "frozenBetsMissingDays": 0,
        "currentActiveBuyCount": 0,
        "liveSettledBetCount": 0,
    }
    warning_set: set[str] = set()

    for date8 in days:
        date_iso = _compact_to_iso(date8)
        review = _load_prediction_review(date_iso)
        daily = _load_daily_summary(date_iso)
        consensus_sheet = _load_consensus_sheet(date_iso)
        result_index = _load_daily_result_index(date_iso)
        review_summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
        if not isinstance(review_summary, dict):
            review_summary = {}
        groups = review.get("groups") if isinstance(review.get("groups"), dict) else {}
        if not isinstance(groups, dict):
            groups = {}
        watch = groups.get("WATCH") if isinstance(groups.get("WATCH"), dict) else {}
        paper = groups.get("PAPER") if isinstance(groups.get("PAPER"), dict) else {}
        consensus_summary = review_summary.get("consensusSummary") if isinstance(review_summary.get("consensusSummary"), dict) else {}
        if not isinstance(consensus_summary, dict):
            consensus_summary = {}
        consensus_top_matches = consensus_summary.get("topMatches") if isinstance(consensus_summary.get("topMatches"), list) else []
        if not consensus_top_matches:
            alt_consensus = review.get("consensusSummary") if isinstance(review.get("consensusSummary"), dict) else {}
            if isinstance(alt_consensus, dict):
                consensus_summary = alt_consensus
                consensus_top_matches = consensus_summary.get("topMatches") if isinstance(consensus_summary.get("topMatches"), list) else []
        if not consensus_top_matches and isinstance(consensus_sheet, dict):
            sheet_summary = consensus_sheet.get("summary") if isinstance(consensus_sheet.get("summary"), dict) else {}
            if isinstance(sheet_summary, dict):
                sheet_top_matches = sheet_summary.get("topMatches") if isinstance(sheet_summary.get("topMatches"), list) else []
                if sheet_top_matches:
                    consensus_summary = sheet_summary
                    consensus_top_matches = sheet_top_matches
        if not isinstance(consensus_summary, dict):
            consensus_summary = {}

        watch_rows = [row for row in list(watch.get("topRows") or []) if isinstance(row, dict)]
        paper_rows = [row for row in list(paper.get("topRows") or []) if isinstance(row, dict)]
        consensus_rows = [row for row in consensus_top_matches if isinstance(row, dict)]
        watch_count = len(watch_rows)
        paper_count = len(paper_rows)
        consensus_count = len(consensus_rows)
        daily_result_status = str(daily.get("results_status") or daily.get("status") or daily.get("resultsStatus") or "").lower()

        watch_settled = 0
        paper_settled = 0
        watch_hit = 0
        paper_hit = 0
        consensus_settled = 0
        consensus_hit = 0
        consensus_pending = 0
        paper_candidate_count = watch_count + paper_count + consensus_count
        paper_eligible_candidate_count = 0
        paper_ineligible_candidate_count = 0
        paper_pending_candidate_count = 0
        paper_settled_candidate_count = 0
        ineligible_source_not_ready_count = 0
        race_id_mismatch_count_before = 0
        race_id_mismatch_count_after = 0

        source_not_ready_statuses = {
            "unavailable",
            "unconfirmed",
            "pending",
            "before_publish",
            "after_close",
            "source_not_ready",
            "future_date_not_ready",
        }

        def _settlement_from_candidate(candidate: dict[str, Any], source_label: str) -> tuple[bool, bool, str, str, str, str]:
            nonlocal race_id_mismatch_count_before
            raw_race_id = str(candidate.get("raceId") or candidate.get("race_id") or "").strip()
            exact_raw_row = result_index.get(_normalize_race_key(raw_race_id)) if raw_race_id else None
            result_row, matched_by = _resolve_result_row(candidate, date_iso, result_index)
            exact_key = _normalize_race_key(raw_race_id) if raw_race_id else ""
            if raw_race_id and exact_raw_row is None and result_row is not None:
                race_id_mismatch_count_before += 1
            candidate_result_status = str(candidate.get("resultStatus") or candidate.get("result_status") or "").strip().lower()
            candidate_result_available = str(candidate.get("resultAvailable") or candidate.get("result_available") or "").strip().lower() in {"1", "true", "yes"}
            result_status = _row_result_status(result_row or {}, candidate_result_status or daily_result_status)
            result_combo = ""
            if isinstance(result_row, dict):
                result_combo = str(result_row.get("actual_trifecta") or result_row.get("result_combo") or result_row.get("trifecta_combo") or "").strip()
            candidate_combo = _normalize_combo(candidate.get("combo") or candidate.get("predictionCombo") or candidate.get("prediction_combo") or candidate.get("ai_combo"))
            hit = False
            if result_combo and candidate_combo:
                hit = _normalize_combo(candidate_combo) == _normalize_combo(result_combo)

            if not daily:
                return False, False, result_combo, "daily_summary_missing", matched_by, result_status
            if candidate_result_status in source_not_ready_statuses or result_status in source_not_ready_statuses or (not candidate_result_available and result_status not in {"available", "ok", "settled"} and not result_combo):
                return False, False, result_combo, "source_not_ready", matched_by, result_status
            if result_status in {"result_data_missing"}:
                return False, False, result_combo, "result_data_missing", matched_by, result_status
            if result_row is None:
                return False, False, "", "race_not_found_in_results", matched_by, result_status
            if not result_combo:
                return False, False, "", "result_combo_missing", matched_by, result_status
            if not candidate_combo:
                return False, False, result_combo, "combo_format_mismatch", matched_by, result_status
            return True, hit, result_combo, "", matched_by, result_status

        grade_a = _grade_block(consensus_summary, "A")
        grade_b = _grade_block(consensus_summary, "B")
        grade_c = _grade_block(consensus_summary, "C")

        for candidate in watch_rows:
            settled, hit, _, reason, matched_by, result_status = _settlement_from_candidate(candidate, "WATCH")
            paper_settled_candidate_count += int(settled)
            if settled:
                paper_eligible_candidate_count += 1
            elif reason == "result_data_missing":
                paper_pending_candidate_count += 1
            else:
                paper_ineligible_candidate_count += 1
                if reason == "source_not_ready":
                    ineligible_source_not_ready_count += 1
            watch_settled += int(settled)
            watch_hit += int(hit)
            if matched_by and _normalize_race_key(str(candidate.get("raceId") or candidate.get("race_id") or "")) != matched_by and not settled:
                race_id_mismatch_count_after += 1

        for candidate in paper_rows:
            settled, hit, _, reason, matched_by, result_status = _settlement_from_candidate(candidate, "PAPER")
            paper_settled_candidate_count += int(settled)
            if settled:
                paper_eligible_candidate_count += 1
            elif reason == "result_data_missing":
                paper_pending_candidate_count += 1
            else:
                paper_ineligible_candidate_count += 1
                if reason == "source_not_ready":
                    ineligible_source_not_ready_count += 1
            paper_settled += int(settled)
            paper_hit += int(hit)
            if matched_by and _normalize_race_key(str(candidate.get("raceId") or candidate.get("race_id") or "")) != matched_by and not settled:
                race_id_mismatch_count_after += 1

        for candidate in consensus_rows:
            settled, hit, _, reason, matched_by, result_status = _settlement_from_candidate(candidate, "CONSENSUS")
            consensus_settled += int(settled)
            consensus_hit += int(hit)
            consensus_pending += int(not settled)
            paper_settled_candidate_count += int(settled)
            if settled:
                paper_eligible_candidate_count += 1
            elif reason == "result_data_missing":
                paper_pending_candidate_count += 1
            else:
                paper_ineligible_candidate_count += 1
                if reason == "source_not_ready":
                    ineligible_source_not_ready_count += 1
            if matched_by and _normalize_race_key(str(candidate.get("raceId") or candidate.get("race_id") or "")) != matched_by and not settled:
                race_id_mismatch_count_after += 1

        paper_settlement_coverage_raw = round(paper_settled_candidate_count / paper_candidate_count, 4) if paper_candidate_count > 0 else None
        paper_settlement_coverage_eligible = round(paper_settled_candidate_count / paper_eligible_candidate_count, 4) if paper_eligible_candidate_count > 0 else None
        paper_settlement_coverage = paper_settlement_coverage_eligible

        current_active_buy_count = _int(live_summary.get("liveBetCount"))
        live_settled_bet_count = _int(live_summary.get("liveSettledBetCount"))
        live_settlement_coverage = _float(live_summary.get("liveSettlementCoverage"))
        if live_settlement_coverage is None and current_active_buy_count > 0:
            live_settlement_coverage = round(live_settled_bet_count / current_active_buy_count, 4)
        live_revenue_ready = bool(
            current_active_buy_count > 0
            and live_settled_bet_count >= 100
            and (live_settlement_coverage or 0.0) >= 0.5
        )

        result_status = daily_result_status or str(review.get("dailySummaryStatus") or "").lower()
        if result_status in {"available", "ok", "settled"}:
            totals["resultsAvailableDays"] += 1
        elif result_status in {"missing", "result_data_missing"}:
            totals["resultDataMissingDays"] += 1
        else:
            totals["unconfirmedDays"] += 1

        backfill_count = _int(daily.get("backfillSettledBetCount"))
        if backfill_count == 0:
            backfill_count = _int(daily.get("backfillSettledCount"))
        if backfill_count == 0:
            backfill_count = _int(daily.get("settledBetCount"))
        if backfill_count == 0:
            for candidate in (
                REPORTS_DAILY_ROOT / f"{date8}_summary.json",
                REPORTS_DAILY_ROOT / f"{date8}_settlement.json",
            ):
                legacy_daily = _load_json(candidate)
                if not legacy_daily:
                    continue
                backfill_count = _int(legacy_daily.get("backfillSettledBetCount"))
                if backfill_count == 0:
                    backfill_count = _int(legacy_daily.get("backfillSettledCount"))
                if backfill_count == 0:
                    backfill_count = _int(legacy_daily.get("settledBetCount"))
                if backfill_count > 0:
                    break

        row = {
            "date": date_iso,
            "watchCount": watch_count,
            "paperCount": paper_count,
            "watchSettledCount": watch_settled,
            "paperSettledCount": paper_settled,
            "watchHitCount": watch_hit,
            "paperHitCount": paper_hit,
            "consensusCount": consensus_count,
            "consensusSettledCount": consensus_settled,
            "consensusHitCount": consensus_hit,
            "consensusResultPendingCount": consensus_pending,
            "paperCandidateCount": paper_candidate_count,
            "paperEligibleCandidateCount": paper_eligible_candidate_count,
            "paperSettledCandidateCount": paper_settled_candidate_count,
            "paperIneligibleCandidateCount": paper_ineligible_candidate_count,
            "paperPendingCandidateCount": paper_pending_candidate_count,
            "paperSettlementCoverageRaw": paper_settlement_coverage_raw,
            "paperSettlementCoverageEligible": paper_settlement_coverage_eligible,
            "paperSettlementCoverage": paper_settlement_coverage,
            "backfillSettledCount": backfill_count,
            "currentActiveBuyCount": current_active_buy_count,
            "liveSettledBetCount": live_settled_bet_count,
            "liveSettlementCoverage": live_settlement_coverage,
            "liveRevenueValidationReady": live_revenue_ready,
            "resultStatus": result_status or "missing",
            "predictionReviewPath": str(REPORTS_PREDICTIONS_ROOT / date_iso / "prediction_review.json"),
            "dailySummaryPath": str(REPORTS_DAILY_ROOT / date_iso / "daily_summary.json"),
            "consensusSheetPath": str(REPORTS_CONSENSUS_ROOT / date_iso / "consensus_sheet.json"),
            "raceIdMismatchCountBefore": race_id_mismatch_count_before,
            "raceIdMismatchCountAfter": race_id_mismatch_count_after,
            "ineligibleSourceNotReadyCount": ineligible_source_not_ready_count,
        }
        rows.append(row)

        totals["watchCount"] += watch_count
        totals["paperCount"] += paper_count
        totals["watchSettledCount"] += watch_settled
        totals["paperSettledCount"] += paper_settled
        totals["watchHitCount"] += watch_hit
        totals["paperHitCount"] += paper_hit
        totals["watchResultAvailableCount"] += watch_settled
        totals["paperResultAvailableCount"] += paper_settled
        totals["consensusCount"] += consensus_count
        totals["consensusSettledCount"] += consensus_settled
        totals["consensusHitCount"] += consensus_hit
        totals["consensusResultPendingCount"] += consensus_pending
        totals["paperCandidateCount"] += paper_candidate_count
        totals["paperEligibleCandidateCount"] += paper_eligible_candidate_count
        totals["paperSettledCandidateCount"] += paper_settled_candidate_count
        totals["paperIneligibleCandidateCount"] += paper_ineligible_candidate_count
        totals["paperPendingCandidateCount"] += paper_pending_candidate_count
        totals["paperSettlementCoverageRaw"] += paper_settlement_coverage_raw or 0.0
        totals["paperSettlementCoverageEligible"] += paper_settlement_coverage_eligible or 0.0
        totals["ineligibleSourceNotReadyCount"] += ineligible_source_not_ready_count
        totals["raceIdMismatchCountBefore"] += race_id_mismatch_count_before
        totals["raceIdMismatchCountAfter"] += race_id_mismatch_count_after
        totals["backfillSettledCount"] += backfill_count
        totals["currentActiveBuyCount"] = max(totals["currentActiveBuyCount"], current_active_buy_count)
        totals["liveSettledBetCount"] = max(totals["liveSettledBetCount"], live_settled_bet_count)

    paper_settlement_coverage_raw = round(totals["paperSettledCandidateCount"] / totals["paperCandidateCount"], 4) if totals["paperCandidateCount"] > 0 else None
    paper_settlement_coverage_eligible = round(totals["paperSettledCandidateCount"] / totals["paperEligibleCandidateCount"], 4) if totals["paperEligibleCandidateCount"] > 0 else None
    paper_settlement_coverage = paper_settlement_coverage_eligible
    watch_hit_rate = round(totals["watchHitCount"] / totals["watchSettledCount"], 4) if totals["watchSettledCount"] > 0 else None
    paper_hit_rate = round(totals["paperHitCount"] / totals["paperSettledCount"], 4) if totals["paperSettledCount"] > 0 else None
    consensus_hit_rate = round(totals["consensusHitCount"] / totals["consensusSettledCount"], 4) if totals["consensusSettledCount"] > 0 else None
    live_revenue_gate_status = "NOT_READY" if totals["currentActiveBuyCount"] <= 0 else ("READY" if totals["liveRevenueValidationReady"] else "RUNNING")
    if totals["paperCandidateCount"] <= 0:
        paper_validation_gate_status = "NOT_READY"
    elif totals["paperEligibleCandidateCount"] <= 0:
        paper_validation_gate_status = "RUNNING"
    elif totals["paperEligibleCandidateCount"] < 100:
        paper_validation_gate_status = "RUNNING"
    elif totals["paperSettledCandidateCount"] >= 100 and (paper_settlement_coverage_eligible or 0.0) >= 0.5:
        paper_validation_gate_status = "READY"
    else:
        paper_validation_gate_status = "RUNNING"
    live_revenue_gate_reason = "current_active_buy_sample_zero" if totals["currentActiveBuyCount"] <= 0 else ("live_settlement_not_ready" if not totals["liveRevenueValidationReady"] else "ready")
    if totals["paperCandidateCount"] <= 0:
        paper_validation_gate_reason = "paper_candidate_missing"
    elif totals["paperEligibleCandidateCount"] < 100:
        paper_validation_gate_reason = "paper_eligible_candidate_count_too_low"
    elif totals["paperSettledCandidateCount"] < 100:
        paper_validation_gate_reason = "paper_settled_candidate_count_below_100"
    elif (paper_settlement_coverage_eligible or 0.0) < 0.5:
        paper_validation_gate_reason = "paper_settlement_coverage_below_0_5"
    else:
        paper_validation_gate_reason = "ready"

    summary = {
        "dateRange": f"{start8}_{end8}",
        "days": len(days),
        "watchDays": len([row for row in rows if row["watchCount"] > 0]),
        "paperDays": len([row for row in rows if row["paperCount"] > 0]),
        "watchCount": totals["watchCount"],
        "paperCount": totals["paperCount"],
        "watchSettledCount": totals["watchSettledCount"],
        "paperSettledCount": totals["paperSettledCount"],
        "watchHitCount": totals["watchHitCount"],
        "paperHitCount": totals["paperHitCount"],
        "watchResultAvailableCount": totals["watchResultAvailableCount"],
        "paperResultAvailableCount": totals["paperResultAvailableCount"],
        "watchHitRate": watch_hit_rate,
        "paperHitRate": paper_hit_rate,
        "watchRoi": watch_paper_perf.get("watchRoi"),
        "paperRoi": watch_paper_perf.get("paperRoi"),
        "resultsAvailableDays": totals["resultsAvailableDays"],
        "unconfirmedDays": totals["unconfirmedDays"],
        "stopReasonCounts": watch_paper_perf.get("stopReasonCounts") or {},
        "oddsStatusCounts": watch_paper_perf.get("oddsStatusCounts") or {},
        "consensusGradeCounts": watch_paper_perf.get("consensusGradeCounts") or {},
        "venueCounts": watch_paper_perf.get("venueCounts") or {},
        "approxProbBands": watch_paper_perf.get("approxProbBands") or {},
        "expectedValueBands": watch_paper_perf.get("expectedValueBands") or {},
        "consensusCount": totals["consensusCount"],
        "consensusSettledCount": totals["consensusSettledCount"],
        "consensusHitCount": totals["consensusHitCount"],
        "consensusHitRate": consensus_hit_rate,
        "consensusResultPendingCount": totals["consensusResultPendingCount"],
        "paperCandidateCount": totals["paperCandidateCount"],
        "paperEligibleCandidateCount": totals["paperEligibleCandidateCount"],
        "paperSettledCandidateCount": totals["paperSettledCandidateCount"],
        "paperIneligibleCandidateCount": totals["paperIneligibleCandidateCount"],
        "paperPendingCandidateCount": totals["paperPendingCandidateCount"],
        "paperSettlementCoverageRaw": paper_settlement_coverage_raw,
        "paperSettlementCoverageEligible": paper_settlement_coverage_eligible,
        "paperSettlementCoverage": paper_settlement_coverage,
        "ineligibleSourceNotReadyCount": totals["ineligibleSourceNotReadyCount"],
        "raceIdMismatchCountBefore": totals["raceIdMismatchCountBefore"],
        "raceIdMismatchCountAfter": totals["raceIdMismatchCountAfter"],
        "backfillSettledCount": totals["backfillSettledCount"],
        "currentActiveBuyCount": totals["currentActiveBuyCount"],
        "liveSettledBetCount": totals["liveSettledBetCount"],
        "liveRevenueValidationReady": live_revenue_gate_status == "READY",
        "paperValidationReady": paper_validation_gate_status == "READY",
        "liveRevenueGateStatus": live_revenue_gate_status,
        "paperValidationGateStatus": paper_validation_gate_status,
        "liveRevenueGateReason": live_revenue_gate_reason,
        "paperValidationGateReason": paper_validation_gate_reason,
        "livePrimaryBlocker": "current_active_buy_sample_zero" if current_active_buy_count <= 0 else live_revenue_gate_reason,
        "paperPrimaryBlocker": paper_validation_gate_reason,
        "primaryBlocker": paper_validation_gate_reason,
        "predictionHashMissingDays": int(final_goal.get("predictionHashMissingDays") or 0),
        "frozenBetsMissingDays": int(final_goal.get("frozenBetsMissingDays") or 0),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    payload = {
        "summary": summary,
        "rows": rows,
        "watchPaperPerformanceSummary": watch_paper_perf,
        "files": {
            "json": str(REPORTS_MONITORING_ROOT / "paper_validation_summary.json"),
            "md": str(REPORTS_REPO_AUDIT_ROOT / "paper_validation_progress.md"),
        },
    }

    _save_json(REPORTS_MONITORING_ROOT / "paper_validation_summary.json", payload)
    return payload


def _render_md(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Paper Validation Summary",
        "",
        f"- dateRange: {summary.get('dateRange', '')}",
        f"- liveSettledBetCount: {summary.get('liveSettledBetCount', 0)}",
        f"- currentActiveBuyCount: {summary.get('currentActiveBuyCount', 0)}",
        f"- paperCandidateCount: {summary.get('paperCandidateCount', 0)}",
        f"- paperSettledCandidateCount: {summary.get('paperSettledCandidateCount', 0)}",
        f"- paperSettlementCoverage: {summary.get('paperSettlementCoverage')}",
        f"- watchSettledCount: {summary.get('watchSettledCount', 0)}",
        f"- paperSettledCount: {summary.get('paperSettledCount', 0)}",
        f"- consensusSettledCount: {summary.get('consensusSettledCount', 0)}",
        f"- externalAgreementSettledCount: {summary.get('consensusSettledCount', 0)}",
        f"- backfillSettledCount: {summary.get('backfillSettledCount', 0)}",
        f"- liveRevenueGateStatus: {summary.get('liveRevenueGateStatus', '')}",
        f"- paperValidationGateStatus: {summary.get('paperValidationGateStatus', '')}",
        f"- liveRevenueGateReason: {summary.get('liveRevenueGateReason', '')}",
        f"- paperValidationGateReason: {summary.get('paperValidationGateReason', '')}",
        f"- livePrimaryBlocker: {summary.get('livePrimaryBlocker', '')}",
        f"- paperPrimaryBlocker: {summary.get('paperPrimaryBlocker', '')}",
        f"- predictionHashMissingDays: {summary.get('predictionHashMissingDays', 0)}",
        f"- frozenBetsMissingDays: {summary.get('frozenBetsMissingDays', 0)}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build paper validation summary.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    payload = paper_validation_summary(start_date=args.start_date, end_date=args.end_date)
    _save_text(REPORTS_REPO_AUDIT_ROOT / "paper_validation_progress.md", _render_md(payload))
    _save_json(REPORTS_REPO_AUDIT_ROOT / "paper_validation_progress.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
