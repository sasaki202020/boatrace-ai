from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.utils.date_paths import normalize_date_str


ROOT = Path(__file__).resolve().parents[2]
DAILY_ROOT = ROOT / "reports" / "daily"
PRED_ROOT = ROOT / "reports" / "predictions"
UI_ROOT = ROOT / "data" / "ui"
MONITORING_ROOT = ROOT / "reports" / "monitoring"
AUDIT_ROOT = ROOT / "reports" / "repo_audit"
NORMALIZED_ROOT = ROOT / "data" / "normalized"

CORE_CARD_ORDER = [
    "pre_race_prediction",
    "odds_refresh",
    "post_race_settlement",
    "daily_summary_generation",
    "health_check",
    "complete_ops",
]


def _date_parts(target_date: str | date | datetime) -> tuple[str, str]:
    date_iso = normalize_date_str(target_date)
    compact = date_iso.replace("-", "")
    return date_iso, compact


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(int(value))
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "ok", "done", "available", "ready", "complete_ops"}


def _status_label(value: str | None) -> str:
    token = str(value or "").strip().lower()
    if token in {"done", "ok"}:
        return "done"
    if token in {"failed", "error", "exception"}:
        return "blocked"
    if token in {"skipped", "skipped_existing", "source_not_ready", "result_data_missing", "wait_for_publication", "wait_for_results", "no_action_needed"}:
        return "skipped"
    if token in {"pending", "running"}:
        return "in_progress"
    if token in {"todo", ""}:
        return "todo"
    if token in {"blocked", "unknown"}:
        return token
    return "unknown"


def _read_step_status(log_dir: Path, step_name: str) -> str:
    path = log_dir / f"step_{step_name}.status"
    if not path.exists():
        return "todo"
    try:
        return _status_label(path.read_text(encoding="utf-8").strip())
    except Exception:
        return "unknown"


def _latest_mtime(paths: list[Path]) -> str:
    mtimes = [p.stat().st_mtime for p in paths if p.exists()]
    if not mtimes:
        return datetime.now().isoformat(timespec="seconds")
    return datetime.fromtimestamp(max(mtimes)).isoformat(timespec="seconds")


def _make_card(
    *,
    card_id: str,
    title: str,
    stage: str,
    status: str,
    reason: str,
    evidence_paths: list[Path],
    blocking_reason: str,
    next_action: str,
    severity: str,
    metrics: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "title": title,
        "stage": stage,
        "status": status,
        "reason": reason,
        "evidence_paths": [str(p) for p in evidence_paths],
        "blocking_reason": blocking_reason,
        "next_action": next_action,
        "updated_at": updated_at,
        "severity": severity,
        "metrics": metrics,
    }


def _find_daily_file(daily_dirs: list[Path], filename: str) -> tuple[Path, dict[str, Any], Path | None]:
    for daily_dir in daily_dirs:
        path = daily_dir / filename
        if path.exists():
            return path, _load_json(path), daily_dir
    return daily_dirs[0] / filename, {}, None


def _find_prediction_file(date_compact: str, filename: str) -> tuple[Path, dict[str, Any], None]:
    path = PRED_ROOT / date_compact / filename
    return path, _load_json(path), None


def _ui_dir(date_compact: str) -> Path:
    return UI_ROOT / date_compact


def _build_status_counts(cards: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(card.get("status") or "unknown") for card in cards)
    return {key: int(value) for key, value in counts.items()}


def _build_next_action(cards: list[dict[str, Any]]) -> tuple[str, str]:
    for card in cards:
        status = str(card.get("status") or "unknown")
        if status in {"blocked", "todo", "in_progress", "unknown"}:
            return str(card.get("next_action") or ""), str(card.get("blocking_reason") or card.get("reason") or "")
    return "monitor_only", ""


def _build_official_source_card(
    *,
    preflight: dict[str, Any],
    preflight_path: Path,
    preflight_md_path: Path,
    pre_race_status: str,
    updated_at: str,
) -> dict[str, Any]:
    classification = str(preflight.get("sourceClassification") or "").strip() or "missing"
    source_ready = _truthy(preflight.get("sourceReady")) or classification == "ready"
    normal_not_ready = {
        "future_date_not_ready",
        "source_not_ready",
        "official_index_unavailable",
        "official_index_empty",
        "official_index_parse_failed",
    }
    if source_ready:
        status = "done"
        reason = f"sourceClassification={classification}"
        blocking_reason = ""
        next_action = "none"
    elif classification in normal_not_ready:
        status = "skipped"
        reason = classification
        blocking_reason = classification
        next_action = "wait_for_publication"
    elif pre_race_status in {"done", "in_progress"}:
        status = "blocked"
        reason = classification
        blocking_reason = "preflight_missing"
        next_action = "run_preflight"
    else:
        status = "todo"
        reason = "preflight_pending"
        blocking_reason = "preflight_pending"
        next_action = "run_preflight"
    return _make_card(
        card_id="official_source_preflight",
        title="公式ソース確認",
        stage="preflight",
        status=status,
        reason=reason,
        evidence_paths=[preflight_path, preflight_md_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "sourceClassification": classification,
            "sourceReady": source_ready,
            "htmlBodyLength": _safe_int(preflight.get("htmlBodyLength")),
            "officialVenueLinkCount": _safe_int(preflight.get("officialVenueLinkCount")),
            "todayVenuesDataStatus": str(preflight.get("todayVenuesDataStatus") or ""),
        },
        updated_at=updated_at,
    )


def _build_pre_race_card(
    *,
    pre_race: dict[str, Any],
    pre_race_path: Path,
    log_dir: Path,
    updated_at: str,
) -> dict[str, Any]:
    step_status = _read_step_status(log_dir, "pre_race")
    status = _status_label(str(pre_race.get("status") or "")) if pre_race else step_status
    source_classification = str(pre_race.get("sourceClassification") or pre_race.get("failure_reason") or "").strip()
    if status == "todo" and step_status in {"in_progress"}:
        status = "in_progress"
    if status == "done":
        reason = source_classification or "pre_race ok"
        blocking_reason = ""
        next_action = "none"
    elif status == "skipped":
        reason = source_classification or "source_not_ready"
        blocking_reason = reason
        next_action = "run_preflight"
    elif status == "blocked":
        reason = source_classification or "pre_race failed"
        blocking_reason = reason or "pre_race_failure"
        next_action = "rerun_pre_race"
    elif status == "in_progress":
        reason = "pre_race running"
        blocking_reason = "pre_race_in_progress"
        next_action = "wait"
    else:
        reason = "pre_race_pending"
        blocking_reason = "pre_race_pending"
        next_action = "run_pre_race"
    return _make_card(
        card_id="pre_race_prediction",
        title="pre_race 予想生成",
        stage="prediction",
        status=status,
        reason=reason,
        evidence_paths=[pre_race_path, log_dir / "step_pre_race.status", log_dir / "step_pre_race.done", log_dir / "step_pre_race.failed"],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "status": str(pre_race.get("status") or ""),
            "sourceClassification": source_classification,
            "failure_step": str(pre_race.get("failure_step") or ""),
        },
        updated_at=updated_at,
    )


def _build_ui_card(
    *,
    ui_dir: Path,
    ops_board_path: Path,
    updated_at: str,
) -> dict[str, Any]:
    ui_files = sorted(ui_dir.glob("raceyosou_*.json")) if ui_dir.exists() else []
    loaded = 0
    invalid = 0
    for path in ui_files:
        if _load_json(path):
            loaded += 1
        else:
            invalid += 1
    ops_board_exists = ops_board_path.exists()
    if loaded > 0 and invalid == 0:
        status = "done"
        reason = f"ui_json_count={loaded}"
        blocking_reason = ""
        next_action = "none"
    elif loaded > 0:
        status = "blocked"
        reason = f"ui_json_invalid={invalid}"
        blocking_reason = "ui_json_invalid"
        next_action = "fix_ui_json"
    else:
        status = "todo"
        reason = "ui_json_missing"
        blocking_reason = "ui_json_missing"
        next_action = "generate_ui_json"
    return _make_card(
        card_id="ui_json_generation",
        title="UI JSON 生成",
        stage="ui",
        status=status,
        reason=reason,
        evidence_paths=[ui_dir, ops_board_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="medium",
        metrics={
            "uiJsonCount": len(ui_files),
            "uiJsonLoadedCount": loaded,
            "uiJsonInvalidCount": invalid,
            "opsBoardExists": ops_board_exists,
        },
        updated_at=updated_at,
    )


def _build_odds_card(
    *,
    odds_refresh: dict[str, Any],
    odds_refresh_path: Path,
    log_dir: Path,
    updated_at: str,
) -> dict[str, Any]:
    step_status = _read_step_status(log_dir, "odds_refresh")
    status = _status_label(str(odds_refresh.get("status") or "")) if odds_refresh else step_status
    summary = odds_refresh.get("summary_row") if isinstance(odds_refresh.get("summary_row"), dict) else {}
    real_available = _safe_int(summary.get("real_odds_available"), _safe_int(summary.get("real_oddsAvailable")))
    pending_before = _safe_int(summary.get("pending_before_deadline"))
    pending_unpublished = _safe_int(summary.get("pending_unpublished"))
    fetch_error = _safe_int(summary.get("fetch_error_count"))
    if status == "todo" and step_status == "in_progress":
        status = "in_progress"
    if real_available > 0:
        status = "done"
        reason = f"real_odds_available={real_available}"
        blocking_reason = ""
        next_action = "none"
    elif fetch_error > 0:
        status = "blocked"
        reason = f"fetch_error_count={fetch_error}"
        blocking_reason = "real_odds_fetch_error"
        next_action = "rerun_odds_refresh"
    elif pending_before > 0 or pending_unpublished > 0:
        status = "skipped"
        reason = "real_odds_pending"
        blocking_reason = "real_odds_pending_before_deadline"
        next_action = "wait_for_publication"
    elif status == "blocked":
        reason = str(odds_refresh.get("failure_step") or "odds_refresh_failed")
        blocking_reason = reason
        next_action = "rerun_odds_refresh"
    elif status == "in_progress":
        reason = "odds_refresh running"
        blocking_reason = "odds_refresh_in_progress"
        next_action = "wait"
    else:
        status = "todo"
        reason = "odds_refresh_pending"
        blocking_reason = "odds_refresh_pending"
        next_action = "run_odds_refresh"
    return _make_card(
        card_id="odds_refresh",
        title="オッズ更新",
        stage="market",
        status=status,
        reason=reason,
        evidence_paths=[odds_refresh_path, log_dir / "step_odds_refresh.status", log_dir / "step_odds_refresh.done", log_dir / "step_odds_refresh.failed"],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "status": str(odds_refresh.get("status") or ""),
            "real_odds_available": real_available,
            "pending_before_deadline": pending_before,
            "pending_unpublished": pending_unpublished,
            "fetch_error_count": fetch_error,
        },
        updated_at=updated_at,
    )


def _build_real_odds_card(
    *,
    odds_refresh: dict[str, Any],
    odds_refresh_path: Path,
    updated_at: str,
) -> dict[str, Any]:
    summary = odds_refresh.get("summary_row") if isinstance(odds_refresh.get("summary_row"), dict) else {}
    real_available = _safe_int(summary.get("real_odds_available"), _safe_int(summary.get("realOddsAvailable")))
    pending_before = _safe_int(summary.get("pending_before_deadline"))
    pending_unpublished = _safe_int(summary.get("pending_unpublished"))
    fetch_error = _safe_int(summary.get("fetch_error_count"))
    if odds_refresh and real_available > 0:
        status = "done"
        reason = f"real_odds_available={real_available}"
        blocking_reason = ""
        next_action = "none"
    elif odds_refresh and (pending_before > 0 or pending_unpublished > 0):
        status = "skipped"
        reason = "real_odds_pending"
        blocking_reason = "real_odds_pending_before_deadline"
        next_action = "wait_for_publication"
    elif odds_refresh and fetch_error > 0:
        status = "blocked"
        reason = "fetch_error"
        blocking_reason = "real_odds_fetch_error"
        next_action = "rerun_odds_refresh"
    elif odds_refresh:
        status = "todo"
        reason = "real_odds_unknown"
        blocking_reason = "real_odds_unknown"
        next_action = "run_odds_refresh"
    else:
        status = "todo"
        reason = "odds_refresh_missing"
        blocking_reason = "odds_refresh_missing"
        next_action = "run_odds_refresh"
    return _make_card(
        card_id="real_odds_availability",
        title="実オッズ到着",
        stage="market",
        status=status,
        reason=reason,
        evidence_paths=[odds_refresh_path, odds_refresh_path.parent / "fetch_report.json"],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "real_odds_available": real_available,
            "pending_before_deadline": pending_before,
            "pending_unpublished": pending_unpublished,
            "fetch_error_count": fetch_error,
        },
        updated_at=updated_at,
    )


def _build_prediction_sheet_card(
    *,
    sheet: dict[str, Any],
    sheet_path: Path,
    updated_at: str,
) -> dict[str, Any]:
    data = sheet.get("data") if isinstance(sheet.get("data"), dict) else {}
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    buy_count = _safe_int(summary.get("buyCount"))
    watch_count = _safe_int(summary.get("watchCount"))
    paper_count = _safe_int(summary.get("paperCount"))
    skip_count = _safe_int(summary.get("skipCount"))
    candidate_count = len(candidates)
    if sheet:
        status = "done"
        reason = f"candidates={candidate_count}"
        blocking_reason = ""
        next_action = "none"
    else:
        status = "todo"
        reason = "prediction_sheet_missing"
        blocking_reason = "prediction_sheet_missing"
        next_action = "run_prediction_sheet"
    return _make_card(
        card_id="buy_candidate_generation",
        title="BUY 候補生成",
        stage="prediction",
        status=status,
        reason=reason,
        evidence_paths=[sheet_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "candidateCount": candidate_count,
            "buyCount": buy_count,
            "watchCount": watch_count,
            "paperCount": paper_count,
            "skipCount": skip_count,
        },
        updated_at=updated_at,
    )


def _build_skip_decision_card(
    *,
    skip_path: Path,
    prediction_sheet_exists: bool,
    updated_at: str,
) -> dict[str, Any]:
    rows = _load_csv_rows(skip_path)
    header_ok = False
    header_fields: list[str] = []
    if skip_path.exists():
        try:
            with skip_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header_fields = next(reader, [])
                header_ok = {"final_decision", "stop_reason", "odds_status"}.issubset({str(h).strip() for h in header_fields})
        except Exception:
            header_ok = False
    if rows and header_ok:
        status = "done"
        reason = f"rows={len(rows)}"
        blocking_reason = ""
        next_action = "none"
    elif skip_path.exists() and not header_ok:
        status = "blocked"
        reason = "skip_decisions_header_missing"
        blocking_reason = "skip_decisions_header_missing"
        next_action = "fix_skip_decisions_schema"
    elif prediction_sheet_exists:
        status = "todo"
        reason = "skip_decisions_missing"
        blocking_reason = "skip_decisions_missing"
        next_action = "run_prediction_sheet"
    else:
        status = "todo"
        reason = "skip_decisions_pending"
        blocking_reason = "skip_decisions_pending"
        next_action = "run_prediction_sheet"
    return _make_card(
        card_id="skip_decision_generation",
        title="skip_decisions 生成",
        stage="prediction",
        status=status,
        reason=reason,
        evidence_paths=[skip_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="medium",
        metrics={
            "rowCount": len(rows),
            "headerOk": header_ok,
            "headerFields": header_fields,
        },
        updated_at=updated_at,
    )


def _build_post_race_card(
    *,
    post_race: dict[str, Any],
    post_race_path: Path,
    daily_summary: dict[str, Any],
    daily_summary_path: Path,
    updated_at: str,
) -> dict[str, Any]:
    summary_status = str(daily_summary.get("results_status") or daily_summary.get("status") or "").strip().lower()
    result_ready = _safe_int(daily_summary.get("resultReadyCount"))
    settled_bets = _safe_int(daily_summary.get("settledBetCount"))
    if daily_summary:
        if summary_status in {"missing", "raw_missing", "result_data_missing", "source_not_ready", "future_date_not_ready"}:
            status = "skipped"
            reason = summary_status or "result_data_missing"
            blocking_reason = reason
            next_action = "wait_for_results"
        else:
            status = "done"
            reason = f"resultReadyCount={result_ready}"
            blocking_reason = ""
            next_action = "none"
    elif post_race:
        status = "blocked"
        reason = "daily_summary_missing"
        blocking_reason = "daily_summary_missing"
        next_action = "run_evening"
    else:
        status = "todo"
        reason = "post_race_pending"
        blocking_reason = "post_race_pending"
        next_action = "run_evening"
    return _make_card(
        card_id="post_race_settlement",
        title="post_race 精算",
        stage="settlement",
        status=status,
        reason=reason,
        evidence_paths=[post_race_path, daily_summary_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "status": str(post_race.get("status") or ""),
            "resultsStatus": summary_status,
            "resultReadyCount": result_ready,
            "settledBetCount": settled_bets,
            "unresolvedBetCount": _safe_int(daily_summary.get("unresolvedBetCount")),
        },
        updated_at=updated_at,
    )


def _build_daily_summary_card(
    *,
    daily_summary: dict[str, Any],
    daily_summary_path: Path,
    daily_report_path: Path,
    updated_at: str,
) -> dict[str, Any]:
    status_token = str(daily_summary.get("status") or daily_summary.get("results_status") or "").strip().lower()
    if daily_summary:
        status = "done"
        reason = status_token or "daily_summary_available"
        blocking_reason = ""
        next_action = "none"
    elif daily_report_path.exists():
        status = "blocked"
        reason = "daily_summary_missing"
        blocking_reason = "daily_summary_missing"
        next_action = "run_evening"
    else:
        status = "todo"
        reason = "daily_summary_pending"
        blocking_reason = "daily_summary_pending"
        next_action = "run_evening"
    return _make_card(
        card_id="daily_summary_generation",
        title="daily_summary 生成",
        stage="settlement",
        status=status,
        reason=reason,
        evidence_paths=[daily_summary_path, daily_report_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "status": status_token,
            "resultReadyCount": _safe_int(daily_summary.get("resultReadyCount")),
            "resultMissingCount": _safe_int(daily_summary.get("resultMissingCount")),
            "settledBetCount": _safe_int(daily_summary.get("settledBetCount")),
            "liveSettledBetCount": _safe_int(daily_summary.get("liveSettledBetCount")),
        },
        updated_at=updated_at,
    )


def _build_health_card(
    *,
    health_check: dict[str, Any],
    health_check_path: Path,
    updated_at: str,
) -> dict[str, Any]:
    if health_check:
        status = "done"
        reason = str(health_check.get("status") or "ok")
        blocking_reason = ""
        next_action = "none"
    else:
        status = "todo"
        reason = "health_check_missing"
        blocking_reason = "health_check_missing"
        next_action = "run_monitor"
    return _make_card(
        card_id="health_check",
        title="health_check",
        stage="monitoring",
        status=status,
        reason=reason,
        evidence_paths=[health_check_path],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="medium",
        metrics={
            "status": str(health_check.get("status") or ""),
            "dailyIssueClassification": str(health_check.get("dailyIssueClassification") or ""),
            "recommendedNextAction": str(health_check.get("recommendedNextAction") or ""),
            "warnings": health_check.get("warnings") or [],
        },
        updated_at=updated_at,
    )


def _build_complete_ops_card(
    *,
    daily_paper_ops_check: dict[str, Any],
    health_check: dict[str, Any],
    final_goal: dict[str, Any],
    updated_at: str,
    target_date: str,
) -> dict[str, Any]:
    daily_status = str(daily_paper_ops_check.get("status") or "").strip().lower()
    health_latest = str(health_check.get("latest_complete_ops_date") or "").strip()
    health_issue = str(health_check.get("dailyIssueClassification") or "").strip()
    if daily_status == "complete_ops" and health_latest == target_date:
        status = "done"
        reason = "complete_ops"
        blocking_reason = ""
        next_action = "none"
    elif daily_status in {"result_data_missing", "source_not_ready"} or health_issue in {"result_data_missing", "source_not_ready", "official_index_unavailable", "official_index_empty"}:
        status = "skipped"
        reason = daily_status or health_issue or "not_complete"
        blocking_reason = reason
        next_action = str(daily_paper_ops_check.get("nextAction") or health_check.get("recommendedNextAction") or "wait")
    else:
        status = "blocked"
        reason = daily_status or health_issue or "not_complete"
        blocking_reason = reason
        next_action = str(daily_paper_ops_check.get("nextAction") or health_check.get("recommendedNextAction") or "run_monitor")
    return _make_card(
        card_id="complete_ops",
        title="complete ops 判定",
        stage="goal",
        status=status,
        reason=reason,
        evidence_paths=[
            AUDIT_ROOT / "final_goal_progress.json",
            MONITORING_ROOT / f"{target_date.replace('-', '')}_health_check.json",
            DAILY_ROOT / target_date / "daily_paper_ops_check.json",
        ],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="high",
        metrics={
            "latest_complete_ops_date": health_latest or str(final_goal.get("latest_complete_ops_date") or ""),
            "dailyPaperOpsCheckStatus": daily_status,
            "primaryBlocker": str(final_goal.get("primaryBlocker") or ""),
            "nextAction": str(final_goal.get("nextAction") or ""),
        },
        updated_at=updated_at,
    )


def _build_tuning_card(
    *,
    final_goal: dict[str, Any],
    live_summary: dict[str, Any],
    paper_validation_summary: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    live_settled = _safe_int(live_summary.get("liveSettledBetCount"))
    revenue_ready = _truthy(final_goal.get("revenueValidationReady")) or _truthy(live_summary.get("revenueValidationReady"))
    live_gate = str(final_goal.get("liveRevenueGateStatus") or paper_validation_summary.get("liveRevenueGateStatus") or "")
    if revenue_ready and live_settled >= 100:
        status = "done"
        reason = "revenueValidationReady=true"
        blocking_reason = ""
        next_action = "none"
    elif live_settled < 100:
        status = "blocked"
        reason = "liveSettledBetCount_below_100"
        blocking_reason = "liveSettledBetCount_below_100"
        next_action = "continue_collecting_live_settled_bets"
    elif not revenue_ready:
        status = "blocked"
        reason = "revenueValidationReady=false"
        blocking_reason = "revenueValidationReady_false"
        next_action = "continue_collecting_live_settled_bets"
    else:
        status = "unknown"
        reason = "tuning_readiness_unknown"
        blocking_reason = "tuning_readiness_unknown"
        next_action = "continue_collecting_live_settled_bets"
    return _make_card(
        card_id="tuning_readiness",
        title="tuning 準備",
        stage="goal",
        status=status,
        reason=reason,
        evidence_paths=[
            AUDIT_ROOT / "final_goal_progress.json",
            MONITORING_ROOT / "live_operation_summary.json",
            MONITORING_ROOT / "paper_validation_summary.json",
        ],
        blocking_reason=blocking_reason,
        next_action=next_action,
        severity="critical",
        metrics={
            "liveSettledBetCount": live_settled,
            "revenueValidationReady": revenue_ready,
            "liveRevenueGateStatus": live_gate,
            "paperValidationGateStatus": str(paper_validation_summary.get("paperValidationGateStatus") or ""),
            "paperEligibleCandidateCount": _safe_int(final_goal.get("paperEligibleCandidateCount")),
            "paperEligibleDayCount": _safe_int(final_goal.get("paperEligibleDayCount")),
        },
        updated_at=updated_at,
    )


def build_ops_goal_board(target_date: str | date | datetime) -> dict[str, Any]:
    date_iso, compact = _date_parts(target_date)
    canonical_daily_dir = DAILY_ROOT / date_iso
    legacy_daily_dir = DAILY_ROOT / compact
    ui_dir = _ui_dir(compact)
    log_dir = canonical_daily_dir / "logs"
    updated_at = datetime.now().isoformat(timespec="seconds")

    preflight_path, preflight, preflight_dir = _find_daily_file([canonical_daily_dir, legacy_daily_dir], "preflight_source_check.json")
    preflight_md_path = (preflight_dir or canonical_daily_dir) / "preflight_source_check.md"
    pre_race_path, pre_race, _ = _find_daily_file([canonical_daily_dir, legacy_daily_dir], "pre_race_run.json")
    odds_refresh_path, odds_refresh, _ = _find_daily_file([canonical_daily_dir, legacy_daily_dir], "odds_refresh_run.json")
    post_race_path, post_race, _ = _find_daily_file([canonical_daily_dir, legacy_daily_dir], "post_race_run.json")
    daily_summary_path, daily_summary, _ = _find_daily_file([canonical_daily_dir, legacy_daily_dir], "daily_summary.json")
    daily_report_path, daily_report, _ = _find_daily_file([canonical_daily_dir, legacy_daily_dir], "daily_report.json")
    prediction_sheet_path, prediction_sheet, _ = _find_prediction_file(compact, "prediction_sheet.json")
    frozen_bets_path, frozen_bets, _ = _find_prediction_file(compact, "frozen_bets.json")
    prediction_review_path, prediction_review, _ = _find_prediction_file(compact, "prediction_review.json")
    consensus_sheet_path, consensus_sheet, _ = _find_prediction_file(compact, "consensus_sheet.json")
    skip_path = canonical_daily_dir / "skip_decisions.csv"
    if not skip_path.exists() and legacy_daily_dir != canonical_daily_dir:
        legacy_skip_path = legacy_daily_dir / "skip_decisions.csv"
        if legacy_skip_path.exists():
            skip_path = legacy_skip_path
    final_goal = _load_json(AUDIT_ROOT / "final_goal_progress.json")
    health_path = MONITORING_ROOT / f"{compact}_health_check.json"
    health_check = _load_json(health_path)
    if not health_check and legacy_daily_dir.exists():
        legacy_health_path = MONITORING_ROOT / f"{compact}_health_check.json"
        health_check = _load_json(legacy_health_path)
    paper_validation_summary_bundle = _load_json(MONITORING_ROOT / "paper_validation_summary.json")
    paper_validation_summary = paper_validation_summary_bundle.get("summary") if isinstance(paper_validation_summary_bundle.get("summary"), dict) else {}
    paper_validation_gate_bundle = _load_json(MONITORING_ROOT / "paper_validation_gate.json")
    paper_validation_gate = paper_validation_gate_bundle if isinstance(paper_validation_gate_bundle, dict) else {}
    live_summary_bundle = _load_json(MONITORING_ROOT / "live_operation_summary.json")
    live_summary = live_summary_bundle.get("summary") if isinstance(live_summary_bundle.get("summary"), dict) else {}
    daily_paper_ops_check = _load_json(canonical_daily_dir / "daily_paper_ops_check.json") or _load_json(legacy_daily_dir / "daily_paper_ops_check.json")

    cards = [
        _build_pre_race_card(
            pre_race=pre_race,
            pre_race_path=pre_race_path,
            log_dir=log_dir,
            updated_at=updated_at,
        ),
        _build_odds_card(
            odds_refresh=odds_refresh,
            odds_refresh_path=odds_refresh_path,
            log_dir=log_dir,
            updated_at=updated_at,
        ),
        _build_post_race_card(
            post_race=post_race,
            post_race_path=post_race_path,
            daily_summary=daily_summary,
            daily_summary_path=daily_summary_path,
            updated_at=updated_at,
        ),
        _build_daily_summary_card(
            daily_summary=daily_summary,
            daily_summary_path=daily_summary_path,
            daily_report_path=daily_report_path,
            updated_at=updated_at,
        ),
        _build_health_card(
            health_check=health_check,
            health_check_path=health_path,
            updated_at=updated_at,
        ),
        _build_complete_ops_card(
            daily_paper_ops_check=daily_paper_ops_check,
            health_check=health_check,
            final_goal=final_goal,
            updated_at=updated_at,
            target_date=date_iso,
        ),
    ]

    status_counts = _build_status_counts(cards)
    summary_status = "blocked"
    if not status_counts.get("blocked"):
        summary_status = "in_progress" if status_counts.get("in_progress") else "todo"
        if not status_counts.get("in_progress"):
            summary_status = "todo" if status_counts.get("todo") else "done"
            if not status_counts.get("todo"):
                summary_status = "done" if status_counts.get("done") else ("skipped" if status_counts.get("skipped") else "unknown")
    next_action, primary_blocker = _build_next_action(cards)
    complete_ops_ready = str(daily_paper_ops_check.get("status") or "").strip().lower() == "complete_ops"
    latest_complete_ops_date = str(final_goal.get("latest_complete_ops_date") or health_check.get("latest_complete_ops_date") or "")
    warnings: list[str] = []
    if legacy_daily_dir.exists():
        warnings.append("legacy_daily_dir_present")
    if canonical_daily_dir.exists() and legacy_daily_dir.exists():
        warnings.append("mixed_daily_dir_formats")
    if daily_summary and str(daily_summary.get("results_status") or "").strip().lower() in {"result_data_missing", "source_not_ready", "future_date_not_ready"}:
        warnings.append(str(daily_summary.get("results_status")))
    if str(health_check.get("dailyIssueClassification") or "").strip():
        warnings.append(f"health_issue={health_check.get('dailyIssueClassification')}")

    board = {
        "date": date_iso,
        "requestedDate": date_iso,
        "generatedAt": updated_at,
        "status": summary_status,
        "boardStatus": summary_status,
        "completeOpsReady": complete_ops_ready,
        "latestCompleteOpsDate": latest_complete_ops_date,
        "primaryBlocker": primary_blocker,
        "nextAction": next_action or "monitor_only",
        "warnings": sorted(dict.fromkeys(warnings)),
        "statusCounts": status_counts,
        "summary": {
            "cardCount": len(cards),
            "doneCount": status_counts.get("done", 0),
            "inProgressCount": status_counts.get("in_progress", 0),
            "blockedCount": status_counts.get("blocked", 0),
            "skippedCount": status_counts.get("skipped", 0),
            "todoCount": status_counts.get("todo", 0),
            "unknownCount": status_counts.get("unknown", 0),
            "completeOpsReady": complete_ops_ready,
            "primaryBlocker": primary_blocker,
            "nextAction": next_action or "monitor_only",
        },
        "directories": {
            "canonicalDailyDir": str(canonical_daily_dir),
            "legacyDailyDir": str(legacy_daily_dir),
            "uiDir": str(ui_dir),
        },
        "artifacts": {
            "preflight": str(preflight_path),
            "preRace": str(pre_race_path),
            "uiJsonDir": str(ui_dir),
            "oddsRefresh": str(odds_refresh_path),
            "realOddsReport": str(odds_refresh_path.parent / "fetch_report.json"),
            "predictionSheet": str(prediction_sheet_path),
            "frozenBets": str(frozen_bets_path),
            "skipDecisions": str(skip_path),
            "postRace": str(post_race_path),
            "dailySummary": str(daily_summary_path),
            "dailyReport": str(daily_report_path),
            "predictionReview": str(prediction_review_path),
            "consensusSheet": str(consensus_sheet_path),
            "healthCheck": str(health_path),
            "finalGoalProgress": str(AUDIT_ROOT / "final_goal_progress.json"),
        },
        "cards": cards,
        "data": {
            "preflight": preflight,
            "preRace": pre_race,
            "oddsRefresh": odds_refresh,
            "postRace": post_race,
            "dailySummary": daily_summary,
            "dailyReport": daily_report,
            "predictionSheet": prediction_sheet,
            "frozenBets": frozen_bets,
            "predictionReview": prediction_review,
            "consensusSheet": consensus_sheet,
            "healthCheck": health_check,
            "dailyPaperOpsCheck": daily_paper_ops_check,
            "paperValidationSummary": paper_validation_summary,
            "paperValidationGate": paper_validation_gate,
            "finalGoalProgress": final_goal,
            "liveSummary": live_summary,
        },
    }
    return board


def _render_md(board: dict[str, Any]) -> str:
    lines = [
        f"# Ops Goal Board ({board.get('date', '')})",
        "",
        f"- status: {board.get('status', '')}",
        f"- boardStatus: {board.get('boardStatus', '')}",
        f"- completeOpsReady: {board.get('completeOpsReady', False)}",
        f"- latestCompleteOpsDate: {board.get('latestCompleteOpsDate', '') or '-'}",
        f"- primaryBlocker: {board.get('primaryBlocker', '') or '-'}",
        f"- nextAction: {board.get('nextAction', '') or '-'}",
        f"- warnings: {', '.join(board.get('warnings') or []) or '-'}",
        "",
        "## Kanban",
    ]
    statuses = ["todo", "in_progress", "blocked", "skipped", "done", "unknown"]
    cards = board.get("cards") if isinstance(board.get("cards"), list) else []
    for status in statuses:
        lines.extend([f"### {status}"])
        group = [card for card in cards if isinstance(card, dict) and str(card.get("status") or "") == status]
        if not group:
            lines.append("- none")
        else:
            for card in group:
                lines.append(
                    f"- {card.get('id', '')} | {card.get('title', '')} | {card.get('reason', '')} | next={card.get('next_action', '')} | blocker={card.get('blocking_reason', '')}"
                )
        lines.append("")
    lines.extend(
        [
            "## Evidence",
            *(f"- {key}: `{value}`" for key, value in (board.get("artifacts") or {}).items()),
            "",
            "## Status Counts",
        ]
    )
    for key, value in sorted((board.get("statusCounts") or {}).items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def write_ops_goal_board(target_date: str | date | datetime) -> dict[str, Any]:
    date_iso, compact = _date_parts(target_date)
    board = build_ops_goal_board(date_iso)
    daily_dir = DAILY_ROOT / date_iso
    daily_dir.mkdir(parents=True, exist_ok=True)
    ui_dir = _ui_dir(compact)
    ui_dir.mkdir(parents=True, exist_ok=True)

    json_path = daily_dir / "ops_board.json"
    md_path = daily_dir / "ops_board.md"
    ui_json_path = ui_dir / "ops_board.json"
    for card in board.get("cards") or []:
        if isinstance(card, dict) and card.get("id") == "ui_json_generation":
            metrics = card.get("metrics")
            if isinstance(metrics, dict):
                metrics["opsBoardExists"] = True
    board["artifacts"] = dict(board.get("artifacts") or {})
    board["artifacts"]["opsBoardJson"] = str(json_path)
    board["artifacts"]["opsBoardMd"] = str(md_path)
    board["artifacts"]["opsBoardUiJson"] = str(ui_json_path)
    json_payload = json.dumps(board, ensure_ascii=False, indent=2)
    json_path.write_text(json_payload, encoding="utf-8")
    md_path.write_text(_render_md(board), encoding="utf-8")
    ui_json_path.write_text(json_payload, encoding="utf-8")
    board["files"] = {
        "json": str(json_path),
        "md": str(md_path),
        "ui_json": str(ui_json_path),
    }
    return board
