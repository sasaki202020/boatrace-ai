from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.pipeline.pipeline_utils import parse_date, report_dir_for


ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = ROOT / "reports" / "daily"
ERRORS_ROOT = ROOT / "reports" / "errors"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_errors(date_key: str) -> list[dict[str, Any]]:
    errors_path = ERRORS_ROOT / f"{date_key}_errors.jsonl"
    if not errors_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in errors_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            return [dict(row) for row in reader]
    except Exception:
        return []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _daily_artifact_path(date_key: str, name: str) -> Path:
    canonical_dir = REPORTS_ROOT / f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    canonical = canonical_dir / name
    if canonical.exists():
        return canonical
    legacy = REPORTS_ROOT / f"{date_key}_{name.replace('daily_', '').replace('.json', '')}.json"
    if legacy.exists():
        return legacy
    return canonical


def _status_token(payload: dict[str, Any], *, keys: tuple[str, ...], default: str = "missing") -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        token = str(value).strip()
        if token:
            return token
    return default


def _build_daily_summary(
    *,
    target_date: str,
    report: dict[str, Any],
    pre_race: dict[str, Any],
    odds_refresh: dict[str, Any],
    post_race: dict[str, Any],
    skip_decisions_path: Path,
    summary_path: Path,
    daily_report_path: Path,
    settlement_path: Path,
) -> dict[str, Any]:
    existing = _load_json(summary_path)
    skip_rows = _load_csv_rows(skip_decisions_path)
    final_goal = _load_json(ROOT / "reports" / "repo_audit" / "final_goal_progress.json")
    odds_summary = odds_refresh.get("summary_row") if isinstance(odds_refresh.get("summary_row"), dict) else {}

    pre_race_status = _status_token(pre_race, keys=("status", "sourceClassification", "failure_reason"))
    odds_refresh_status = _status_token(odds_refresh, keys=("status", "sourceClassification", "failure_reason"))
    post_race_status = _status_token(post_race, keys=("status", "results_status", "sourceClassification", "failure_reason"))
    results_status = str(report.get("resultsStatus") or post_race.get("results_status") or post_race_status or "missing").strip().lower()

    real_odds_available_count = _safe_int(
        odds_summary.get("real_odds_available")
        or odds_summary.get("available_races")
        or odds_refresh.get("available_races")
        or odds_refresh.get("success_races")
    )
    real_odds_pending_count = _safe_int(
        odds_summary.get("pending_unpublished")
        or odds_refresh.get("pending_unpublished_races")
        or odds_refresh.get("unpublished_races")
        or odds_refresh.get("real_odds_pending_before_deadline")
    )
    skip_decision_count = len(skip_rows)
    buy_candidate_count = _safe_int(report.get("buyCount") or 0)
    if buy_candidate_count <= 0 and skip_rows:
        buy_candidate_count = sum(1 for row in skip_rows if str(row.get("final_decision") or "").strip().upper() == "BUY")
    live_settled_bet_count = _safe_int(report.get("settledBetCount") or 0)
    live_bet_count = _safe_int(report.get("betCount") or report.get("buyCount") or 0)
    live_settlement_coverage = None
    if live_bet_count > 0:
        live_settlement_coverage = round(live_settled_bet_count / live_bet_count, 4)

    complete_ops_ready = bool(
        pre_race_status.lower() == "ok"
        and odds_refresh_status.lower() == "ok"
        and post_race_status.lower() == "ok"
        and results_status in {"available", "ok", "settled"}
    )
    latest_complete_ops_date = str(
        final_goal.get("latest_complete_ops_date")
        or final_goal.get("latestCompleteOpsDate")
        or (target_date if complete_ops_ready else "")
    )
    if not latest_complete_ops_date:
        latest_complete_ops_date = target_date if complete_ops_ready else ""

    if complete_ops_ready:
        primary_blocker = ""
        next_action = "none"
    elif pre_race_status.lower() != "ok":
        primary_blocker = pre_race_status or "pre_race_pending"
        next_action = "run_morning"
    elif odds_refresh_status.lower() != "ok":
        primary_blocker = odds_refresh_status or "odds_refresh_pending"
        next_action = "run_morning"
    elif post_race_status.lower() != "ok":
        primary_blocker = post_race_status or "post_race_pending"
        next_action = "run_evening"
    elif results_status in {"missing", "result_data_missing", "source_not_ready", "future_date_not_ready"}:
        primary_blocker = results_status
        next_action = "wait_for_results"
    else:
        primary_blocker = "complete_ops_pending"
        next_action = "run_monitor"

    summary = {
        **report,
        "date": target_date,
        "status": results_status,
        "results_status": results_status,
        "completeOpsReady": complete_ops_ready,
        "complete_ops_ready": complete_ops_ready,
        "latestCompleteOpsDate": latest_complete_ops_date,
        "latest_complete_ops_date": latest_complete_ops_date,
        "preRaceStatus": pre_race_status,
        "oddsRefreshStatus": odds_refresh_status,
        "postRaceStatus": post_race_status,
        "resultReadyCount": _safe_int(report.get("resultReadyCount")),
        "settledBetCount": _safe_int(report.get("settledBetCount")),
        "buyCandidateCount": buy_candidate_count,
        "skipDecisionCount": skip_decision_count,
        "realOddsAvailableCount": real_odds_available_count,
        "realOddsPendingCount": real_odds_pending_count,
        "primaryBlocker": primary_blocker,
        "nextAction": next_action,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "evidencePaths": {
            "dailyReport": str(daily_report_path),
            "dailySummary": str(summary_path),
            "preRaceRun": str(_daily_artifact_path(target_date.replace("-", ""), "pre_race_run.json")),
            "oddsRefreshRun": str(_daily_artifact_path(target_date.replace("-", ""), "odds_refresh_run.json")),
            "postRaceRun": str(_daily_artifact_path(target_date.replace("-", ""), "post_race_run.json")),
            "skipDecisions": str(skip_decisions_path),
            "dailySettlement": str(settlement_path),
        },
        "liveBetCount": live_bet_count,
        "liveSettledBetCount": live_settled_bet_count,
        "liveSettlementCoverage": live_settlement_coverage,
    }
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in summary.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    return merged


def daily_report(*, target_date: str, jcd: str = "all") -> dict[str, Any]:
    date_key = target_date.replace("-", "")
    settlement_path = _daily_artifact_path(date_key, "daily_settlement.json")
    summary_path = _daily_artifact_path(date_key, "daily_summary.json")
    pre_race_path = _daily_artifact_path(date_key, "pre_race_run.json")
    odds_refresh_path = _daily_artifact_path(date_key, "odds_refresh_run.json")
    post_race_path = _daily_artifact_path(date_key, "post_race_run.json")
    skip_decisions_path = _daily_artifact_path(date_key, "skip_decisions.csv")
    settlement = _load_json(settlement_path)
    summary = _load_json(summary_path)
    pre_race = _load_json(pre_race_path)
    odds_refresh = _load_json(odds_refresh_path)
    post_race = _load_json(post_race_path)
    source = settlement or summary
    errors = _load_errors(date_key)

    report = {
        "date": date_key,
        "venues": source.get("venues", []),
        "raceCount": source.get("raceCount", 0),
        "resultReadyCount": source.get("resultReadyCount", 0),
        "resultMissingCount": source.get("resultMissingCount", 0),
        "resultOkCount": source.get("resultOkCount", 0),
        "resultPendingCount": source.get("resultPendingCount", 0),
        "resultParseErrorCount": source.get("resultParseErrorCount", 0),
        "resultRefundCount": source.get("resultRefundCount", 0),
        "resultCanceledCount": source.get("resultCanceledCount", 0),
        "resultNoContestCount": source.get("resultNoContestCount", 0),
        "buyCount": source.get("buyCount", 0),
        "watchCount": source.get("watchCount", 0),
        "skipCount": source.get("skipCount", 0),
        "betCount": source.get("betCount", source.get("buyCount", 0)),
        "frozenStakeAmount": source.get("frozenStakeAmount", source.get("stakeAmount", 0)),
        "settledBetCount": source.get("settledBetCount", 0),
        "settledStakeAmount": source.get("settledStakeAmount", 0),
        "unresolvedBetCount": source.get("unresolvedBetCount", 0),
        "unresolvedStakeAmount": source.get("unresolvedStakeAmount", 0),
        "voidBetCount": source.get("voidBetCount", 0),
        "voidStakeAmount": source.get("voidStakeAmount", 0),
        "hitCount": source.get("hitCount", 0),
        "missCount": source.get("missCount", 0),
        "pendingCount": source.get("pendingCount", 0),
        "voidCount": source.get("voidCount", 0),
        "parseErrorCount": source.get("parseErrorCount", 0),
        "noResultCount": source.get("noResultCount", 0),
        "stakeAmount": source.get("frozenStakeAmount", source.get("stakeAmount", 0)),
        "payoutAmount": source.get("payoutAmount", 0),
        "profit": source.get("profit", (source.get("payoutAmount", 0) or 0) - (source.get("settledStakeAmount", 0) or 0)),
        "roi": source.get("settledRoi", source.get("roi")),
        "settledRoi": source.get("settledRoi", source.get("roi")),
        "hitRate": source.get("hitRate"),
        "resultsStatus": source.get("resultsStatus", "missing"),
        "stakeUnit": source.get("stakeUnit", 100),
        "errorsCount": len(errors),
        "missingCount": source.get("missingCount", len([row for row in errors if "missing" in str(row.get("type", "")).lower()])),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "summaryPath": str(summary_path),
        "settlementPath": str(settlement_path),
        "errorsPath": str(ERRORS_ROOT / f"{date_key}_errors.jsonl"),
        "warnings": source.get("warnings", []),
        "settlement": settlement or summary,
    }
    warnings = list(report["warnings"])
    buy_count = int(report.get("buyCount") or 0)
    race_count = int(report.get("raceCount") or 0)
    settled_bet_count = int(report.get("settledBetCount") or 0)
    result_parse_error_count = int(report.get("resultParseErrorCount") or 0)
    if buy_count > 50:
        warnings.append("high_daily_buy_count")
    if race_count > 0 and buy_count / max(race_count, 1) > 3:
        warnings.append("high_buy_per_race")
    if buy_count > 0 and settled_bet_count / buy_count < 0.5:
        warnings.append("low_settlement_coverage")
    if result_parse_error_count >= max(5, race_count // 3):
        warnings.append("high_result_parse_error")
    report["warnings"] = sorted(dict.fromkeys(warnings))
    report_dir = report_dir_for(parse_date(target_date, default=date.today()))
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "daily_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    daily_summary = _build_daily_summary(
        target_date=target_date,
        report=report,
        pre_race=pre_race,
        odds_refresh=odds_refresh,
        post_race=post_race,
        skip_decisions_path=skip_decisions_path,
        summary_path=summary_path,
        daily_report_path=report_dir / "daily_report.json",
        settlement_path=settlement_path,
    )
    (report_dir / "daily_summary.json").write_text(json.dumps(daily_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Build a daily report.")
    parser.add_argument("--date", required=True, help="today, YYYYMMDD, or YYYY-MM-DD")
    parser.add_argument("--jcd", default="all")
    args = parser.parse_args()
    if args.date.lower() == "today":
        target_date = date.today().isoformat()
    else:
        target_date = args.date if "-" in args.date else f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}"
    print(json.dumps(daily_report(target_date=target_date, jcd=args.jcd), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
