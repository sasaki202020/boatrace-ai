from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"
REPORTS_MONITORING_ROOT = ROOT / "reports" / "monitoring"
REPORTS_ANALYSIS_ROOT = ROOT / "reports" / "analysis"
REPORTS_REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"


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


def _parse_date_text(value: str | None) -> date | None:
    if not value:
        return None
    token = str(value).strip()
    if not token:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(token, fmt).date()
        except Exception:
            continue
    return None


def _normalize_iso(value: str | None) -> str | None:
    parsed = _parse_date_text(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def _date_range(start_iso: str, end_iso: str) -> list[str]:
    start = _parse_date_text(start_iso)
    end = _parse_date_text(end_iso)
    if start is None or end is None or end < start:
        return []
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def _load_day_json(root: Path, date_iso: str, filename: str) -> dict[str, Any]:
    for candidate in (
        root / date_iso / filename,
        root / _compact_date(date_iso) / filename,
        root / f"{_compact_date(date_iso)}_{filename}",
    ):
        payload = _load_json(candidate)
        if payload:
            return payload
    return {}


def _existing_date_dirs(root: Path, filename: str) -> set[str]:
    if not root.exists():
        return set()
    found: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / filename).exists():
            found.add(_normalize_iso(child.name) or "")
    return {item for item in found if item}


def _discover_dates(start_iso: str, end_iso: str) -> list[str]:
    dates: set[str] = set()
    for root, filename in (
        (REPORTS_DAILY_ROOT, "daily_summary.json"),
        (REPORTS_DAILY_ROOT, "daily_paper_ops_check.json"),
        (REPORTS_PREDICTIONS_ROOT, "prediction_sheet.json"),
        (REPORTS_CONSENSUS_ROOT, "consensus_sheet.json"),
    ):
        dates.update(_existing_date_dirs(root, filename))
    if not dates:
        return _date_range(start_iso, end_iso)
    start = _parse_date_text(start_iso)
    end = _parse_date_text(end_iso)
    if start is None or end is None:
        return sorted(dates)
    return [day for day in _date_range(start_iso, end_iso) if day in dates]


def _load_skip_decisions(path: Path) -> list[dict[str, Any]]:
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


def _count_from_skip_decisions(rows: list[dict[str, Any]], token: str) -> int:
    token = token.lower()
    count = 0
    for row in rows:
        text = " ".join(str(row.get(key) or "") for key in ("stop_reason", "odds_status", "result_status", "reason", "status", "classification", "blocker"))
        if token in text.lower():
            count += 1
    return count


def _first_present(payloads: list[dict[str, Any]], *keys: str, default: Any = None) -> Any:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in keys:
            if key in payload and payload.get(key) not in (None, ""):
                return payload.get(key)
    return default


def _first_int(payloads: list[dict[str, Any]], *keys: str, default: int = 0) -> int:
    value = _first_present(payloads, *keys, default=default)
    try:
        return int(value)
    except Exception:
        return default


def _extract_results_status(payloads: list[dict[str, Any]]) -> str:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("results_status", "resultsStatus", "result_status", "status"):
            value = str(payload.get(key) or "").strip().lower()
            if value:
                return value
    return "missing"


def _extract_preflight_classification(preflight: dict[str, Any], ops_check: dict[str, Any]) -> str:
    for payload in (preflight, ops_check):
        if not isinstance(payload, dict):
            continue
        for key in ("sourceClassification", "preflightClassification", "classification"):
            value = str(payload.get(key) or "").strip().lower()
            if value:
                return value
        value = str(payload.get("status") or "").strip().lower()
        if value in {"ready", "source_not_ready", "result_data_missing", "future_date_not_ready", "source_not_ready_expected"}:
            return value
    return "missing"


def _consensus_candidate_count(consensus_sheet: dict[str, Any]) -> int:
    summary = consensus_sheet.get("summary") if isinstance(consensus_sheet.get("summary"), dict) else {}
    grade_counts = summary.get("gradeCounts") if isinstance(summary.get("gradeCounts"), dict) else {}
    if not grade_counts:
        return 0
    total = 0
    for value in grade_counts.values():
        try:
            total += int(value)
        except Exception:
            continue
    return total


def _eligible_day(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("has_prediction_sheet")
        and payload.get("has_frozen_bets")
        and payload.get("has_prediction_review")
        and payload.get("has_daily_summary")
        and payload.get("has_post_race_run")
        and str(payload.get("results_status") or "").lower() in {"available", "ok", "settled"}
        and int(payload.get("paper_candidate_count") or 0) > 0
        and int(payload.get("paper_eligible_candidate_count") or 0) > 0
    )


def _priority_class(payload: dict[str, Any]) -> str:
    if payload.get("eligible_day"):
        return "eligible"
    blocker = str(payload.get("blocker") or "")
    if blocker == "prediction_review_missing":
        return "A"
    if blocker == "prediction_sheet_missing":
        return "B"
    if blocker in {"daily_summary_missing", "post_race_run_missing", "result_data_missing"}:
        return "C"
    if blocker == "source_not_ready":
        return "D"
    if blocker == "result_data_missing_waiting":
        return "E"
    return "Z"


def _next_action(payload: dict[str, Any]) -> str:
    if not payload.get("has_preflight"):
        return "run_preflight"
    classification = str(payload.get("preflight_classification") or "").lower()
    if classification in {"source_not_ready", "source_not_ready_expected"}:
        return "wait_for_publication"
    if classification == "result_data_missing":
        return "wait_for_results"
    if not payload.get("has_prediction_sheet"):
        return "run_morning"
    if not payload.get("has_frozen_bets"):
        return "run_morning"
    if not payload.get("has_daily_summary") or not payload.get("has_post_race_run"):
        if str(payload.get("results_status") or "").lower() in {"result_data_missing", "raw_missing", "pending", "future_date_not_ready", "source_not_ready"}:
            return "import_k_results"
        return "run_evening"
    if not payload.get("has_prediction_review"):
        return "build_prediction_review"
    if not payload.get("has_consensus_sheet") and str(payload.get("results_status") or "").lower() in {"available", "ok", "settled"}:
        return "build_consensus_sheet"
    if str(payload.get("results_status") or "").lower() in {"result_data_missing", "raw_missing", "pending"}:
        return "wait_for_results"
    return "no_action_needed"


def _estimated_increase(payload: dict[str, Any]) -> int:
    if payload.get("eligible_day"):
        return 0
    blocker = str(payload.get("blocker") or "")
    if blocker in {"source_not_ready", "result_data_missing_waiting"}:
        return 0
    return 1


def _command_for_row(row: dict[str, Any]) -> list[str]:
    date_iso = str(row.get("date") or "")
    if not date_iso:
        return []
    action = str(row.get("next_action") or "")
    if action == "run_preflight":
        return [f"scripts\\run_paper_ops_preflight.bat {date_iso}"]
    if action == "run_morning":
        return [f"scripts\\run_paper_ops_morning.bat {date_iso}"]
    if action == "run_evening":
        return [f"scripts\\run_paper_ops_evening.bat {date_iso}"]
    if action == "run_monitor":
        return [f"scripts\\run_paper_ops_monitor.bat {date_iso}"]
    if action == "import_k_results":
        return [
            f"scripts\\check_k_inbox.bat {date_iso}",
            f"scripts\\import_k_results.bat {date_iso}",
            f"scripts\\run_paper_ops_evening.bat {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "build_prediction_review":
        return [
            f"scripts\\run_prediction_review.bat {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "build_consensus_sheet":
        return [
            f"py scripts\\build_consensus_sheet.py --date {date_iso}",
            f"scripts\\run_paper_ops_monitor.bat {date_iso}",
        ]
    if action == "wait_for_publication":
        return [f"scripts\\run_paper_ops_monitor.bat {date_iso}"]
    if action == "wait_for_results":
        return [f"scripts\\check_k_inbox.bat {date_iso}"]
    if action == "no_action_needed":
        return [f"scripts\\run_paper_ops_monitor.bat {date_iso}"]
    return [f"scripts\\run_paper_ops_monitor.bat {date_iso}"]


def _build_row(date_iso: str) -> dict[str, Any]:
    preflight = _load_day_json(REPORTS_DAILY_ROOT, date_iso, "preflight_source_check.json")
    ops_check = _load_day_json(REPORTS_DAILY_ROOT, date_iso, "daily_paper_ops_check.json")
    prediction_sheet = _load_day_json(REPORTS_PREDICTIONS_ROOT, date_iso, "prediction_sheet.json")
    frozen_bets = _load_day_json(REPORTS_PREDICTIONS_ROOT, date_iso, "frozen_bets.json")
    prediction_review = _load_day_json(REPORTS_PREDICTIONS_ROOT, date_iso, "prediction_review.json")
    daily_summary = _load_day_json(REPORTS_DAILY_ROOT, date_iso, "daily_summary.json")
    daily_report = _load_day_json(REPORTS_DAILY_ROOT, date_iso, "daily_report.json")
    post_race_run = _load_day_json(REPORTS_DAILY_ROOT, date_iso, "post_race_run.json")
    consensus_sheet = _load_day_json(REPORTS_CONSENSUS_ROOT, date_iso, "consensus_sheet.json")
    results_status_diagnostic = _load_day_json(REPORTS_DAILY_ROOT, date_iso, "results_status_diagnostic.json")
    skip_decisions_rows = _load_skip_decisions(REPORTS_DAILY_ROOT / date_iso / "skip_decisions.csv")
    paper_validation_bundle = _load_json(REPORTS_MONITORING_ROOT / "paper_validation_summary.json")
    paper_validation_rows = paper_validation_bundle.get("rows") if isinstance(paper_validation_bundle.get("rows"), list) else []
    paper_validation_row = {}
    for candidate in paper_validation_rows:
        if isinstance(candidate, dict) and str(candidate.get("date") or "") == date_iso:
            paper_validation_row = candidate
            break

    has_prediction_sheet = bool(prediction_sheet)
    has_frozen_bets = bool(frozen_bets)
    has_prediction_review = bool(prediction_review)
    has_daily_summary = bool(daily_summary)
    has_post_race_run = bool(post_race_run)
    has_consensus_sheet = bool(consensus_sheet)
    has_preflight = bool(preflight)

    preflight_classification = _extract_preflight_classification(preflight, ops_check)
    results_status = _extract_results_status([daily_summary, daily_report, ops_check, post_race_run])
    paper_candidate_count = _first_int([paper_validation_row, ops_check, daily_summary, daily_report, post_race_run], "paperCandidateCount", "paper_candidate_count")
    paper_eligible_candidate_count = _first_int([paper_validation_row, ops_check, daily_summary, daily_report, post_race_run], "paperEligibleCandidateCount", "paper_eligible_candidate_count")
    paper_settled_candidate_count = _first_int([paper_validation_row, ops_check, daily_summary, daily_report, post_race_run], "paperSettledCandidateCount", "paper_settled_candidate_count")
    consensus_candidate_count = _consensus_candidate_count(consensus_sheet)
    source_not_ready_count = _first_int([paper_validation_row, ops_check, daily_summary, daily_report], "ineligibleSourceNotReadyCount", "sourceNotReadyCount", "source_not_ready_count")
    if source_not_ready_count <= 0:
        source_not_ready_count = _count_from_skip_decisions(skip_decisions_rows, "source_not_ready")
    result_data_missing_count = _first_int([results_status_diagnostic, ops_check, daily_summary, daily_report], "raw_missing_count", "resultDataMissingCount", "result_data_missing_count")
    if result_data_missing_count <= 0:
        result_data_missing_count = _count_from_skip_decisions(skip_decisions_rows, "result_data_missing") + _count_from_skip_decisions(skip_decisions_rows, "raw_missing")

    row: dict[str, Any] = {
        "date": date_iso,
        "preflight_classification": preflight_classification,
        "has_prediction_sheet": has_prediction_sheet,
        "has_frozen_bets": has_frozen_bets,
        "has_prediction_review": has_prediction_review,
        "has_daily_summary": has_daily_summary,
        "has_post_race_run": has_post_race_run,
        "results_status": results_status,
        "paper_candidate_count": paper_candidate_count,
        "paper_eligible_candidate_count": paper_eligible_candidate_count,
        "paper_settled_candidate_count": paper_settled_candidate_count,
        "consensus_candidate_count": consensus_candidate_count,
        "source_not_ready_count": source_not_ready_count,
        "result_data_missing_count": result_data_missing_count,
        "has_consensus_sheet": has_consensus_sheet,
        "eligible_day": False,
        "blocker": "",
        "next_action": "",
        "estimated_increase": 0,
        "command_preview": [],
    }

    if not has_preflight:
        row["blocker"] = "preflight_missing"
        row["next_action"] = "run_preflight"
    elif preflight_classification in {"source_not_ready", "source_not_ready_expected"}:
        row["blocker"] = "source_not_ready"
        row["next_action"] = "wait_for_publication"
    elif preflight_classification == "result_data_missing":
        row["blocker"] = "result_data_missing"
        row["next_action"] = "wait_for_results"
    elif not has_prediction_sheet:
        row["blocker"] = "prediction_sheet_missing"
        row["next_action"] = "run_morning"
    elif not has_frozen_bets:
        row["blocker"] = "frozen_bets_missing"
        row["next_action"] = "run_morning"
    elif not has_daily_summary:
        row["blocker"] = "daily_summary_missing"
        row["next_action"] = "run_evening"
    elif not has_post_race_run:
        row["blocker"] = "post_race_run_missing"
        row["next_action"] = "run_evening"
    elif results_status in {"result_data_missing", "raw_missing"}:
        row["blocker"] = "result_data_missing"
        row["next_action"] = "import_k_results"
    elif not has_prediction_review:
        row["blocker"] = "prediction_review_missing"
        row["next_action"] = "build_prediction_review"
    elif not has_consensus_sheet and results_status in {"available", "ok", "settled"}:
        row["blocker"] = "consensus_sheet_missing"
        row["next_action"] = "build_consensus_sheet"
    elif results_status in {"pending", "future_date_not_ready", "source_not_ready"}:
        row["blocker"] = results_status
        row["next_action"] = "wait_for_results" if results_status == "pending" else "wait_for_publication"
    else:
        row["eligible_day"] = _eligible_day(row)
        row["blocker"] = "eligible" if row["eligible_day"] else "unknown"
        row["next_action"] = "no_action_needed" if row["eligible_day"] else "run_monitor"

    if row["next_action"] == "build_consensus_sheet" and row["eligible_day"] is False:
        row["eligible_day"] = _eligible_day({**row, "has_consensus_sheet": False})
    if row["next_action"] == "no_action_needed":
        row["eligible_day"] = _eligible_day(row)

    row["estimated_increase"] = _estimated_increase(row)
    row["priorityClass"] = _priority_class(row)
    row["command_preview"] = _command_for_row(row)
    return row


def _render_md(rows: list[dict[str, Any]], summary: dict[str, Any], top_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Paper Validation Eligible Dates",
        "",
        f"- startDate: {summary.get('startDate', '')}",
        f"- endDate: {summary.get('endDate', '')}",
        f"- scannedDays: {summary.get('scannedDays', 0)}",
        f"- eligibleDayCount: {summary.get('eligibleDayCount', 0)}",
        f"- paperEligibleCandidateCount: {summary.get('paperEligibleCandidateCount', 0)}",
        f"- paperSettledCandidateCount: {summary.get('paperSettledCandidateCount', 0)}",
        f"- paperSettlementCoverageEligible: {summary.get('paperSettlementCoverageEligible', 0)}",
        f"- remainingPaperEligibleCandidateCount: {summary.get('remainingPaperEligibleCandidateCount', 0)}",
        f"- primaryBlocker: {summary.get('primaryBlocker', '')}",
        "",
        "## 次に回すべき日付 TOP10",
    ]
    if top_rows:
        for row in top_rows[:10]:
            lines.append(
                f"- {row.get('date', '')} | {row.get('priorityClass', '')} | {row.get('next_action', '')} | "
                f"est+{row.get('estimated_increase', 0)} | blocker={row.get('blocker', '')}"
            )
    else:
        lines.append("- なし")
    lines.extend(["", "## 全件一覧"])
    for row in rows:
        lines.append(
            f"- {row.get('date', '')} | preflight={row.get('preflight_classification', '')} | "
            f"sheet={row.get('has_prediction_sheet', False)} | frozen={row.get('has_frozen_bets', False)} | "
            f"review={row.get('has_prediction_review', False)} | daily={row.get('has_daily_summary', False)} | "
            f"post={row.get('has_post_race_run', False)} | results={row.get('results_status', '')} | "
            f"paper={row.get('paper_candidate_count', 0)} | eligible={row.get('paper_eligible_candidate_count', 0)} | "
            f"settled={row.get('paper_settled_candidate_count', 0)} | consensus={row.get('consensus_candidate_count', 0)} | "
            f"source_not_ready={row.get('source_not_ready_count', 0)} | result_data_missing={row.get('result_data_missing_count', 0)} | "
            f"eligible_day={row.get('eligible_day', False)} | blocker={row.get('blocker', '')} | next_action={row.get('next_action', '')}"
        )
    return "\n".join(lines) + "\n"


def _render_next_dates_md(top_rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# Paper Validation Next Dates",
        "",
        f"- eligibleDayCount: {summary.get('eligibleDayCount', 0)}",
        f"- currentPaperEligibleCandidateCount: {summary.get('paperEligibleCandidateCount', 0)}",
        f"- remainingPaperEligibleCandidateCount: {summary.get('remainingPaperEligibleCandidateCount', 0)}",
        f"- primaryBlocker: {summary.get('primaryBlocker', '')}",
        "",
        "## 次に回すべき日付 TOP10",
    ]
    for row in top_rows[:10]:
        lines.append(f"- {row.get('date', '')} -> {row.get('next_action', '')} ({row.get('priorityClass', '')})")
        for command in row.get("command_preview", []):
            lines.append(f"  - {command}")
    if not top_rows:
        lines.append("- なし")
    return "\n".join(lines) + "\n"


def _render_route_md(summary: dict[str, Any], top_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Paper Validation Eligible Growth Route",
        "",
        f"- currentPaperEligibleCandidateCount: {summary.get('paperEligibleCandidateCount', 0)}",
        f"- targetPaperEligibleCandidateCount: 100",
        f"- remainingPaperEligibleCandidateCount: {summary.get('remainingPaperEligibleCandidateCount', 0)}",
        f"- primaryBlocker: {summary.get('primaryBlocker', '')}",
        "",
        "## 次に回すべき日付 TOP10",
    ]
    for row in top_rows[:10]:
        lines.append(
            f"- {row.get('date', '')} | {row.get('priorityClass', '')} | {row.get('next_action', '')} | "
            f"est+{row.get('estimated_increase', 0)}"
        )
    if not top_rows:
        lines.append("- なし")
    lines.extend(
        [
            "",
            "## まだやらないこと",
            "- BUY閾値変更",
            "- EV計算変更",
            "- 予想ロジック変更",
            "- ダミー結果の作成",
            "- 実賭け前提の運用",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Find paper validation eligible dates.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    start_iso = _normalize_iso(args.start_date)
    end_iso = _normalize_iso(args.end_date)
    if start_iso is None or end_iso is None:
        raise SystemExit("invalid date range")

    dates = _discover_dates(start_iso, end_iso)
    rows = [_build_row(day) for day in dates]
    for row in rows:
        row["eligible_day"] = _eligible_day(row)
        if row["eligible_day"]:
            row["blocker"] = "eligible"
            row["next_action"] = "no_action_needed"
            row["estimated_increase"] = 0
            row["priorityClass"] = "eligible"
            row["command_preview"] = _command_for_row(row)
        elif not row["next_action"]:
            row["next_action"] = "run_monitor"
            row["priorityClass"] = _priority_class(row)
            row["estimated_increase"] = _estimated_increase(row)
            row["command_preview"] = _command_for_row(row)

    priority_order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "eligible": 5, "Z": 6}
    top_rows = sorted(rows, key=lambda item: (priority_order.get(str(item.get("priorityClass") or "Z"), 6), -int(item.get("estimated_increase") or 0), item.get("date", "")))[:10]
    eligible_days = [row for row in rows if row.get("eligible_day")]
    paper_eligible_candidate_count = sum(int(row.get("paper_eligible_candidate_count") or 0) for row in eligible_days)
    paper_settled_candidate_count = sum(int(row.get("paper_settled_candidate_count") or 0) for row in eligible_days)
    paper_settlement_coverage_eligible = round(paper_settled_candidate_count / paper_eligible_candidate_count, 4) if paper_eligible_candidate_count > 0 else None
    current_summary_bundle = _load_json(REPORTS_MONITORING_ROOT / "paper_validation_summary.json")
    current_summary = current_summary_bundle.get("summary") if isinstance(current_summary_bundle.get("summary"), dict) else {}
    if not isinstance(current_summary, dict):
        current_summary = {}
    current_count = int(current_summary.get("paperEligibleCandidateCount") or 0)
    remaining_count = max(100 - current_count, 0)
    summary = {
        "startDate": start_iso,
        "endDate": end_iso,
        "scannedDays": len(dates),
        "eligibleDayCount": len(eligible_days),
        "paperEligibleCandidateCount": current_count,
        "paperSettledCandidateCount": int(current_summary.get("paperSettledCandidateCount") or 0),
        "paperSettlementCoverageEligible": current_summary.get("paperSettlementCoverageEligible") if current_summary else paper_settlement_coverage_eligible,
        "remainingPaperEligibleCandidateCount": remaining_count,
        "primaryBlocker": current_summary.get("primaryBlocker") or "paper_eligible_candidate_count_too_low",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    csv_rows = []
    for row in rows:
        csv_rows.append(
            {
                "date": row.get("date", ""),
                "preflight_classification": row.get("preflight_classification", ""),
                "has_prediction_sheet": row.get("has_prediction_sheet", False),
                "has_frozen_bets": row.get("has_frozen_bets", False),
                "has_prediction_review": row.get("has_prediction_review", False),
                "has_daily_summary": row.get("has_daily_summary", False),
                "has_post_race_run": row.get("has_post_race_run", False),
                "results_status": row.get("results_status", ""),
                "paper_candidate_count": row.get("paper_candidate_count", 0),
                "paper_eligible_candidate_count": row.get("paper_eligible_candidate_count", 0),
                "paper_settled_candidate_count": row.get("paper_settled_candidate_count", 0),
                "consensus_candidate_count": row.get("consensus_candidate_count", 0),
                "source_not_ready_count": row.get("source_not_ready_count", 0),
                "result_data_missing_count": row.get("result_data_missing_count", 0),
                "eligible_day": row.get("eligible_day", False),
                "blocker": row.get("blocker", ""),
                "next_action": row.get("next_action", ""),
            }
        )

    csv_fieldnames = [
        "date",
        "preflight_classification",
        "has_prediction_sheet",
        "has_frozen_bets",
        "has_prediction_review",
        "has_daily_summary",
        "has_post_race_run",
        "results_status",
        "paper_candidate_count",
        "paper_eligible_candidate_count",
        "paper_settled_candidate_count",
        "consensus_candidate_count",
        "source_not_ready_count",
        "result_data_missing_count",
        "eligible_day",
        "blocker",
        "next_action",
    ]

    eligible_payload = {
        "status": "ok",
        "startDate": start_iso,
        "endDate": end_iso,
        "summary": summary,
        "rows": csv_rows,
        "generatedAt": summary["generatedAt"],
    }
    next_payload = {
        "status": "ok",
        "startDate": start_iso,
        "endDate": end_iso,
        "summary": summary,
        "topDates": top_rows,
        "generatedAt": summary["generatedAt"],
    }
    route_payload = {
        "status": "ok",
        "startDate": start_iso,
        "endDate": end_iso,
        "currentPaperEligibleCandidateCount": current_count,
        "remainingPaperEligibleCandidateCount": remaining_count,
        "primaryBlocker": summary["primaryBlocker"],
        "topDates": top_rows,
        "generatedAt": summary["generatedAt"],
    }

    _save_csv(REPORTS_ANALYSIS_ROOT / "paper_validation_eligible_dates.csv", csv_rows, csv_fieldnames)
    _save_text(REPORTS_ANALYSIS_ROOT / "paper_validation_eligible_dates.md", _render_md(rows, summary, top_rows))
    _save_json(REPORTS_ANALYSIS_ROOT / "paper_validation_eligible_dates.json", eligible_payload)
    _save_text(REPORTS_ANALYSIS_ROOT / "paper_validation_next_dates.md", _render_next_dates_md(top_rows, summary))
    _save_json(REPORTS_ANALYSIS_ROOT / "paper_validation_next_dates.json", next_payload)
    _save_text(REPORTS_REPO_AUDIT_ROOT / "paper_validation_eligible_growth_route.md", _render_route_md(summary, top_rows))
    _save_json(REPORTS_REPO_AUDIT_ROOT / "paper_validation_eligible_growth_route.json", route_payload)
    print(json.dumps(route_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
