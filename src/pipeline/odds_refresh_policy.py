from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.pipeline.pipeline_utils import ROOT


SUMMARY_PATH = ROOT / "reports" / "daily" / "odds_refresh_summary.csv"
TIME_SERIES_PATH = ROOT / "reports" / "daily" / "odds_time_series.csv"
TIME_SERIES_DIR = ROOT / "reports" / "daily"
POLICY_PATH = ROOT / "config" / "odds_refresh_policy.json"
ACTIVE_PHASE_STATUS_PATH = ROOT / "reports" / "daily" / "active_phase_status.json"

DEFAULT_POLICY: dict[str, Any] = {
    "lookback_days": 3,
    "default_phase": "final",
    "phases": ["morning", "late", "final"],
    "score_weights": {
        "real_odds_available": 100,
        "pending_unpublished": -10,
        "real_odds_missing_fetch": -5,
    },
}

PHASE_ORDER = {"morning": 1, "late": 2, "final": 3}
PHASES = ["morning", "late", "final"]
PHASE_ALIASES = {
    "morning": "morning",
    "late": "late",
    "late_refresh": "late",
    "final": "final",
    "final_refresh": "final",
}

DEFAULT_ACTIVE_PHASE_STATUS: dict[str, Any] = {
    "mode": "fixed",
    "active_phase": "final",
    "candidate_phase": "final",
    "candidate_streak": 0,
    "warning_streak": 0,
    "locked_until": "",
    "last_updated": "",
    "last_reevaluation_date": "",
    "reevaluation_interval_days": 7,
    "reason": "initial state",
    "fallback_reason": "",
    "promotion_reason": "",
    "reevaluation_reason": "",
    "baseline": {
        "avg_available": 0.0,
        "avg_pending": 0.0,
        "avg_missing": 0.0,
        "complete_dates": [],
    },
}

PROMOTION_STREAK_THRESHOLD = 2
LOCK_DAYS = 7
EMERGENCY_AVAILABILITY_DROP_RATIO = 0.25
EMERGENCY_AVAILABILITY_DROP_MIN = 1


@dataclass
class OddsRefreshSummary:
    date: str
    phase: str
    total_races: int
    real_odds_available: int
    pending_unpublished: int
    real_odds_missing_fetch: int
    fetch_error_count: int
    skipped_races: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_phase(value: str | None, *, default: str = "final") -> str:
    if value is None:
        return default
    return PHASE_ALIASES.get(str(value).strip().lower(), default)


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _parse_date_text(value: Any) -> date | None:
    text = _coerce_str(value, "")
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _normalize_active_phase_status(status: dict[str, Any] | None) -> dict[str, Any]:
    work = dict(DEFAULT_ACTIVE_PHASE_STATUS)
    if isinstance(status, dict):
        work.update(status)
    mode = _coerce_str(work.get("mode", "fixed"), "fixed").lower()
    work["mode"] = mode if mode in {"fixed", "reevaluation"} else "fixed"
    work["active_phase"] = normalize_phase(work.get("active_phase", "final"), default="final")
    work["candidate_phase"] = normalize_phase(work.get("candidate_phase", "final"), default="final")
    work["candidate_streak"] = max(0, _coerce_int(work.get("candidate_streak", 0)))
    work["warning_streak"] = max(0, _coerce_int(work.get("warning_streak", 0)))
    work["locked_until"] = _coerce_str(work.get("locked_until", ""), "")
    work["last_updated"] = _coerce_str(work.get("last_updated", ""), "")
    work["last_reevaluation_date"] = _coerce_str(work.get("last_reevaluation_date", ""), "")
    work["reevaluation_interval_days"] = max(1, _coerce_int(work.get("reevaluation_interval_days", 7), 7))
    work["reason"] = _coerce_str(work.get("reason", "initial state"), "initial state")
    work["fallback_reason"] = _coerce_str(work.get("fallback_reason", ""), "")
    work["promotion_reason"] = _coerce_str(work.get("promotion_reason", ""), "")
    work["reevaluation_reason"] = _coerce_str(work.get("reevaluation_reason", ""), "")
    baseline = work.get("baseline", {})
    if not isinstance(baseline, dict):
        baseline = {}
    work["baseline"] = {
        "avg_available": _coerce_float(baseline.get("avg_available", 0.0), 0.0),
        "avg_pending": _coerce_float(baseline.get("avg_pending", 0.0), 0.0),
        "avg_missing": _coerce_float(baseline.get("avg_missing", 0.0), 0.0),
        "complete_dates": list(baseline.get("complete_dates", [])) if isinstance(baseline.get("complete_dates", []), list) else [],
    }
    return work


def load_active_phase_status(path: Path | None = None) -> dict[str, Any]:
    status_path = path or ACTIVE_PHASE_STATUS_PATH
    if not status_path.exists():
        return _normalize_active_phase_status(None)
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return _normalize_active_phase_status(None)
    return _normalize_active_phase_status(raw if isinstance(raw, dict) else None)


def save_active_phase_status(status: dict[str, Any], path: Path | None = None) -> Path:
    status_path = path or ACTIVE_PHASE_STATUS_PATH
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_active_phase_status(status)
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return status_path


def _complete_dates_for_frame(
    frame: pd.DataFrame,
    *,
    phases: list[str],
    target_date: str | date | None = None,
) -> tuple[list[str], list[str]]:
    if frame.empty:
        return [], []

    work = _normalize_summary_frame(frame)
    work = work[work["run_status"].astype(str).str.lower().eq("ok")].copy()
    if work.empty:
        return [], []

    if target_date is not None:
        target_date_str = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        work["date"] = work["date"].astype(str)
        work = work[work["date"] < target_date_str].copy()
        if work.empty:
            return [], []

    complete_dates: list[str] = []
    incomplete_dates: list[str] = []
    for date_value in sorted(work["date"].dropna().astype(str).unique().tolist()):
        subset = work[work["date"].astype(str).eq(date_value)].copy()
        present = []
        for phase in phases:
            phase_rows = subset[subset["phase"].astype(str).eq(str(phase))]
            if not phase_rows.empty:
                present.append(phase)
        if len(present) == len(phases):
            complete_dates.append(date_value)
        else:
            incomplete_dates.append(date_value)
    return complete_dates, incomplete_dates


def load_policy(path: Path | None = None) -> dict[str, Any]:
    policy = deepcopy(DEFAULT_POLICY)
    policy_path = path or POLICY_PATH
    if not policy_path.exists():
        return policy

    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return policy

    if isinstance(raw, dict):
        if "lookback_days" in raw:
            try:
                policy["lookback_days"] = max(1, int(raw["lookback_days"]))
            except Exception:
                pass
        if "default_phase" in raw:
            policy["default_phase"] = normalize_phase(raw["default_phase"], default="final")
        if "phases" in raw and isinstance(raw["phases"], list):
            phases = [normalize_phase(phase, default="") for phase in raw["phases"]]
            phases = [phase for phase in phases if phase in PHASE_ORDER]
            if phases:
                policy["phases"] = phases
        score_weights = dict(policy.get("score_weights", {}))
        raw_weights = raw.get("score_weights", {})
        if isinstance(raw_weights, dict):
            for key, value in raw_weights.items():
                try:
                    score_weights[str(key)] = float(value)
                except Exception:
                    continue
        policy["score_weights"] = score_weights
    return policy


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return int(float(value))
    except Exception:
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except Exception:
        return default


def calculate_adoption_score(
    *,
    real_odds_available: Any,
    pending_unpublished: Any,
    real_odds_missing_fetch: Any,
    weights: dict[str, Any] | None = None,
) -> float:
    weights = weights or DEFAULT_POLICY["score_weights"]
    return (
        _coerce_int(real_odds_available)
        * float(weights.get("real_odds_available", 100))
        + _coerce_int(pending_unpublished) * float(weights.get("pending_unpublished", -10))
        + _coerce_int(real_odds_missing_fetch) * float(weights.get("real_odds_missing_fetch", -5))
    )


def _snapshot_from_row(row: dict[str, Any] | pd.Series) -> dict[str, int]:
    if isinstance(row, pd.Series):
        get = row.get
    else:
        get = row.get
    return {
        "total_races": _coerce_int(get("total_races", get("total_rows", 0))),
        "real_odds_available": _coerce_int(get("real_odds_available", 0)),
        "pending_unpublished": _coerce_int(get("pending_unpublished", 0)),
        "real_odds_missing_fetch": _coerce_int(
            get("real_odds_missing_fetch", get("real_odds_missing_fetch_failed", 0))
        ),
        "real_odds_missing_fetch_failed": _coerce_int(get("real_odds_missing_fetch_failed", get("real_odds_missing_fetch", 0))),
        "real_odds_missing_never_fetched": _coerce_int(get("real_odds_missing_never_fetched", 0)),
        "buy_count": _coerce_int(get("buy_count", 0)),
    }


def build_summary_row(
    *,
    target_date: date,
    phase: str,
    measured_at: str | None,
    run_status: str,
    snapshot: dict[str, Any],
    command: list[str] | None = None,
    pipeline_report_path: str = "",
    source: str = "odds_refresh",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or load_policy()
    normalized_phase = normalize_phase(phase, default=str(policy.get("default_phase", "final")))
    snapshot_row = _snapshot_from_row(snapshot)
    adoption_score = calculate_adoption_score(
        real_odds_available=snapshot_row["real_odds_available"],
        pending_unpublished=snapshot_row["pending_unpublished"],
        real_odds_missing_fetch=snapshot_row["real_odds_missing_fetch"],
        weights=dict(policy.get("score_weights", {})),
    )
    row = {
        "date": target_date.isoformat(),
        "phase": normalized_phase,
        "phase_order": PHASE_ORDER.get(normalized_phase, 99),
        "measured_at": measured_at or datetime.now().isoformat(timespec="seconds"),
        "run_status": run_status,
        "source": source,
        "pipeline_report_path": pipeline_report_path,
        "command": " ".join(command or []),
        **snapshot_row,
        "adoption_score": adoption_score,
    }
    return row


def _normalize_summary_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    work = frame.copy()
    if "phase" in work.columns:
        work["phase"] = work["phase"].astype(str).map(lambda value: normalize_phase(value, default=value))
    else:
        work["phase"] = "final"
    if "phase_order" not in work.columns:
        work["phase_order"] = work["phase"].map(PHASE_ORDER).fillna(99).astype(int)
    else:
        work["phase_order"] = pd.to_numeric(work["phase_order"], errors="coerce").fillna(99).astype(int)
    for column in (
        "total_races",
        "real_odds_available",
        "pending_unpublished",
        "real_odds_missing_fetch",
        "real_odds_missing_fetch_failed",
        "real_odds_missing_never_fetched",
        "buy_count",
    ):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0).astype(int)
        else:
            work[column] = 0
    if "adoption_score" in work.columns:
        work["adoption_score"] = pd.to_numeric(work["adoption_score"], errors="coerce").fillna(0.0)
    else:
        work["adoption_score"] = 0.0
    if "run_status" not in work.columns:
        work["run_status"] = "ok"
    if "measured_at" not in work.columns:
        work["measured_at"] = ""
    if "source" not in work.columns:
        work["source"] = "odds_refresh"
    if "pipeline_report_path" not in work.columns:
        work["pipeline_report_path"] = ""
    if "command" not in work.columns:
        work["command"] = ""
    return work


def _seed_summary_from_time_series(policy: dict[str, Any] | None = None) -> pd.DataFrame:
    source_paths = sorted(TIME_SERIES_DIR.glob("*/odds_time_series.csv"))
    if TIME_SERIES_PATH.exists():
        source_paths = [TIME_SERIES_PATH] + source_paths
    if not source_paths:
        return pd.DataFrame()

    policy = policy or load_policy()
    frames: list[pd.DataFrame] = []
    for path in source_paths:
        try:
            df = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()

    work = pd.concat(frames, ignore_index=True)
    if "date" not in work.columns or "phase" not in work.columns:
        return pd.DataFrame()

    work["phase"] = work["phase"].astype(str).map(lambda value: normalize_phase(value, default="final"))
    work = work[work["phase"].isin(PHASE_ORDER)]
    if work.empty:
        return pd.DataFrame()

    work["phase_order"] = work["phase"].map(PHASE_ORDER).fillna(99).astype(int)
    work["run_status"] = work.get("run_status", pd.Series(dtype=object)).fillna("ok").astype(str)
    work["source"] = "odds_time_series"
    work["pipeline_report_path"] = work.get("pipeline_report_path", pd.Series(dtype=object)).fillna("").astype(str)
    work["command"] = work.get("command", pd.Series(dtype=object)).fillna("").astype(str)

    for column in (
        "real_odds_available",
        "pending_unpublished",
        "real_odds_missing_fetch_failed",
        "real_odds_missing_never_fetched",
        "buy_count",
        "total_rows",
    ):
        work[column] = pd.to_numeric(work.get(column), errors="coerce").fillna(0).astype(int)

    work["total_races"] = work["total_rows"]
    work["real_odds_missing_fetch"] = work["real_odds_missing_fetch_failed"]
    work["adoption_score"] = work.apply(
        lambda row: calculate_adoption_score(
            real_odds_available=row["real_odds_available"],
            pending_unpublished=row["pending_unpublished"],
            real_odds_missing_fetch=row["real_odds_missing_fetch"],
            weights=dict(policy.get("score_weights", {})),
        ),
        axis=1,
    )

    cols = [
        "date",
        "phase",
        "phase_order",
        "measured_at",
        "run_status",
        "source",
        "pipeline_report_path",
        "command",
        "total_races",
        "real_odds_available",
        "pending_unpublished",
        "real_odds_missing_fetch",
        "real_odds_missing_fetch_failed",
        "real_odds_missing_never_fetched",
        "buy_count",
        "adoption_score",
    ]
    for column in cols:
        if column not in work.columns:
            work[column] = "" if column in {"date", "phase", "measured_at", "run_status", "source", "pipeline_report_path", "command"} else 0
    work = work[cols]
    work = work.sort_values(["date", "phase_order", "measured_at"], kind="stable").reset_index(drop=True)
    return work


def load_summary_frame(path: Path | None = None, *, seed_from_time_series: bool = True, policy: dict[str, Any] | None = None) -> pd.DataFrame:
    summary_path = path or SUMMARY_PATH
    frame = _safe_read_csv(summary_path)
    if frame.empty and seed_from_time_series:
        frame = _seed_summary_from_time_series(policy=policy)
        if not frame.empty:
            _write_csv(summary_path, frame)
    else:
        frame = _normalize_summary_frame(frame)
    return frame


def upsert_summary_row(
    row: dict[str, Any],
    *,
    path: Path | None = None,
    seed_from_time_series: bool = True,
    policy: dict[str, Any] | None = None,
) -> tuple[Path, pd.DataFrame]:
    summary_path = path or SUMMARY_PATH
    policy = policy or load_policy()
    frame = load_summary_frame(summary_path, seed_from_time_series=seed_from_time_series, policy=policy)
    new_row = pd.DataFrame([row])
    if frame.empty:
        frame = new_row
    else:
        if "date" in frame.columns and "phase" in frame.columns:
            frame = frame[
                ~(
                    frame["date"].astype(str).eq(str(row.get("date", "")))
                    & frame["phase"].astype(str).eq(str(row.get("phase", "")))
                )
            ]
        frame = pd.concat([frame, new_row], ignore_index=True)
    frame = _normalize_summary_frame(frame)
    frame = frame.sort_values(["date", "phase_order", "measured_at"], kind="stable").reset_index(drop=True)
    _write_csv(summary_path, frame)
    return summary_path, frame


def upsert_daily_summary(
    row: dict[str, Any],
    *,
    path: Path | None = None,
    seed_from_time_series: bool = True,
    policy: dict[str, Any] | None = None,
) -> tuple[Path, pd.DataFrame]:
    return upsert_summary_row(
        row,
        path=path,
        seed_from_time_series=seed_from_time_series,
        policy=policy,
    )


def summarize_phase_performance(
    frame: pd.DataFrame,
    *,
    lookback_days: int,
    phases: list[str] | None = None,
    default_phase: str = "final",
    target_date: str | date | None = None,
) -> dict[str, Any]:
    phase_list = phases or ["morning", "late", "final"]
    complete_dates, incomplete_dates = _complete_dates_for_frame(
        frame,
        phases=phase_list,
        target_date=target_date,
    )

    if frame.empty:
        return {
            "lookback_days": lookback_days,
            "default_phase": default_phase,
            "target_date": target_date.isoformat() if isinstance(target_date, date) else str(target_date or ""),
            "complete_dates": [],
            "incomplete_dates": [],
            "eligible_phases": [],
            "phase_scores": [],
            "status": "fallback",
            "reason": "fallback: no usable summary data",
        }

    work = _normalize_summary_frame(frame)
    work = work[work["run_status"].astype(str).str.lower().eq("ok")].copy()
    if work.empty:
        return {
            "lookback_days": lookback_days,
            "default_phase": default_phase,
            "target_date": target_date.isoformat() if isinstance(target_date, date) else str(target_date or ""),
            "complete_dates": [],
            "incomplete_dates": incomplete_dates,
            "eligible_phases": [],
            "phase_scores": [],
            "status": "fallback",
            "reason": "fallback: no successful rows in summary",
        }

    if len(complete_dates) < lookback_days:
        return {
            "lookback_days": lookback_days,
            "default_phase": default_phase,
            "target_date": target_date.isoformat() if isinstance(target_date, date) else str(target_date or ""),
            "complete_dates": complete_dates,
            "incomplete_dates": incomplete_dates,
            "eligible_phases": [],
            "phase_scores": [],
            "status": "fallback",
            "reason": f"fallback: need {lookback_days} complete dates but only have {len(complete_dates)}",
        }

    compared_dates = complete_dates[-lookback_days:]
    recent = work[work["date"].isin(compared_dates)].copy()
    phase_scores: list[dict[str, Any]] = []
    eligible_phases: list[str] = []
    for phase in phase_list:
        subset = recent[recent["phase"].astype(str).eq(str(phase))].copy()
        if subset.empty:
            return {
                "lookback_days": lookback_days,
                "default_phase": default_phase,
                "target_date": target_date.isoformat() if isinstance(target_date, date) else str(target_date or ""),
                "complete_dates": compared_dates,
                "incomplete_dates": incomplete_dates,
                "eligible_phases": eligible_phases,
                "phase_scores": phase_scores,
                "status": "fallback",
                "reason": f"fallback: insufficient rows for phase={phase}",
            }
        if subset["date"].nunique() < lookback_days or len(subset) != lookback_days:
            return {
                "lookback_days": lookback_days,
                "default_phase": default_phase,
                "target_date": target_date.isoformat() if isinstance(target_date, date) else str(target_date or ""),
                "complete_dates": compared_dates,
                "incomplete_dates": incomplete_dates,
                "eligible_phases": eligible_phases,
                "phase_scores": phase_scores,
                "status": "fallback",
                "reason": f"fallback: phase={phase} does not have exactly one row per complete date",
            }

        eligible_phases.append(str(phase))
        phase_scores.append(
            {
                "phase": str(phase),
                "days": int(subset["date"].nunique()),
                "avg_adoption_score": round(float(subset["adoption_score"].mean()), 3),
                "avg_real_odds_available": round(float(subset["real_odds_available"].mean()), 3),
                "avg_pending_unpublished": round(float(subset["pending_unpublished"].mean()), 3),
                "avg_real_odds_missing_fetch": round(float(subset["real_odds_missing_fetch"].mean()), 3),
            }
        )

    return {
        "lookback_days": lookback_days,
        "default_phase": default_phase,
        "target_date": target_date.isoformat() if isinstance(target_date, date) else str(target_date or ""),
        "complete_dates": compared_dates,
        "incomplete_dates": incomplete_dates,
        "eligible_phases": eligible_phases,
        "phase_scores": phase_scores,
        "status": "selected",
        "reason": "",
    }


def build_phase_selection_reason(
    *,
    selected_phase: str,
    lookback_days: int,
    complete_dates: list[str],
    incomplete_dates: list[str],
    eligible_phases: list[str],
    phase_stats: list[dict[str, Any]],
    default_phase: str,
    fallback_reason: str = "",
) -> str:
    parts = [
        f"selected by phase comparison over {lookback_days} complete days",
        f"complete_dates={','.join(complete_dates) if complete_dates else '-'}",
        f"incomplete_dates={','.join(incomplete_dates) if incomplete_dates else '-'}",
        f"eligible_phases={','.join(eligible_phases) if eligible_phases else '-'}",
    ]
    for row in phase_stats:
        parts.append(
            f"{row['phase']}="
            f"{row['avg_adoption_score']} / available={row['avg_real_odds_available']} / "
            f"pending={row['avg_pending_unpublished']} / missing_fetch={row['avg_real_odds_missing_fetch']}"
        )
    if selected_phase == default_phase:
        parts.append("default phase matched best score")
    if fallback_reason:
        parts.append(f"fallback_reason={fallback_reason}")
    return "; ".join(parts)


def select_active_phase(
    *,
    target_date: date,
    policy: dict[str, Any] | None = None,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    policy = policy or load_policy()
    default_phase = normalize_phase(policy.get("default_phase", "final"), default="final")
    lookback_days = max(1, int(policy.get("lookback_days", 3) or 3))
    frame = load_summary_frame(summary_path, seed_from_time_series=True, policy=policy)
    phase_list = list(policy.get("phases", ["morning", "late", "final"]))
    result = summarize_phase_performance(
        frame,
        lookback_days=lookback_days,
        phases=phase_list,
        default_phase=default_phase,
        target_date=target_date,
    )
    result["target_date"] = target_date.isoformat()
    result["default_phase"] = default_phase
    result["lookback_days"] = lookback_days
    result["summary_path"] = str(summary_path or SUMMARY_PATH)
    result["source_rows"] = int(len(frame))
    result["active_phase"] = default_phase
    result["eligible_phases"] = list(result.get("eligible_phases", []))
    result["complete_dates"] = list(result.get("complete_dates", []))
    result["incomplete_dates"] = list(result.get("incomplete_dates", []))
    fallback_reason = str(result.get("reason", "")).strip()
    if result.get("status") != "selected" or not result.get("phase_scores"):
        result["active_phase"] = default_phase
        result["fallback_reason"] = fallback_reason or "fallback: no usable summary data"
        result["reason"] = build_phase_selection_reason(
            selected_phase=default_phase,
            lookback_days=lookback_days,
            complete_dates=list(result.get("complete_dates", [])),
            incomplete_dates=list(result.get("incomplete_dates", [])),
            eligible_phases=list(result.get("eligible_phases", [])),
            phase_stats=[],
            default_phase=default_phase,
            fallback_reason=str(result["fallback_reason"]),
        )
        return result

    phase_stats = result["phase_scores"]
    sorted_stats = sorted(
        phase_stats,
        key=lambda row: (
            float(row["avg_real_odds_available"]),
            -float(row["avg_pending_unpublished"]),
            -float(row["avg_real_odds_missing_fetch"]),
            float(row["avg_adoption_score"]),
            1 if row["phase"] == default_phase else 0,
        ),
        reverse=True,
    )
    selected = sorted_stats[0]
    result["phase_scores"] = sorted_stats
    result["active_phase"] = str(selected["phase"])
    result["fallback_reason"] = fallback_reason
    result["reason"] = build_phase_selection_reason(
        selected_phase=str(selected["phase"]),
        lookback_days=lookback_days,
        complete_dates=list(result.get("complete_dates", [])),
        incomplete_dates=list(result.get("incomplete_dates", [])),
        eligible_phases=list(result.get("eligible_phases", [])),
        phase_stats=sorted_stats,
        default_phase=default_phase,
        fallback_reason=fallback_reason,
    )
    if len(result.get("eligible_phases", [])) < len(phase_list):
        result["active_phase"] = default_phase
        result["status"] = "fallback"
        result["reason"] = build_phase_selection_reason(
            selected_phase=default_phase,
            lookback_days=lookback_days,
            complete_dates=list(result.get("complete_dates", [])),
            incomplete_dates=list(result.get("incomplete_dates", [])),
            eligible_phases=list(result.get("eligible_phases", [])),
            phase_stats=sorted_stats,
            default_phase=default_phase,
            fallback_reason="fallback: not all configured phases were eligible",
        )
        return result
    return result


def _phase_score_map(phase_scores: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("phase", "")): dict(row) for row in phase_scores if str(row.get("phase", ""))}


def _is_lock_active(locked_until: str, target_date: date) -> bool:
    lock_date = _parse_date_text(locked_until)
    if lock_date is None:
        return False
    return target_date <= lock_date


def _score_metrics(row: dict[str, Any] | None) -> dict[str, float]:
    row = row or {}
    return {
        "avg_available": _coerce_float(row.get("avg_real_odds_available", row.get("avg_available", 0.0)), 0.0),
        "avg_pending": _coerce_float(row.get("avg_pending_unpublished", row.get("avg_pending", 0.0)), 0.0),
        "avg_missing": _coerce_float(row.get("avg_real_odds_missing_fetch", row.get("avg_missing", 0.0)), 0.0),
    }


def _normalize_baseline(baseline: dict[str, Any] | None) -> dict[str, Any]:
    baseline = baseline if isinstance(baseline, dict) else {}
    return {
        "avg_available": _coerce_float(baseline.get("avg_available", 0.0), 0.0),
        "avg_pending": _coerce_float(baseline.get("avg_pending", 0.0), 0.0),
        "avg_missing": _coerce_float(baseline.get("avg_missing", 0.0), 0.0),
        "complete_dates": [str(value) for value in baseline.get("complete_dates", [])] if isinstance(baseline.get("complete_dates", []), list) else [],
    }


def should_fallback_to_final(
    *,
    target_date: date,
    policy: dict[str, Any],
    status: dict[str, Any],
    summary_selection: dict[str, Any],
    score_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    default_phase = normalize_phase(policy.get("default_phase", "final"), default="final")
    lookback_days = max(1, int(policy.get("lookback_days", 3) or 3))
    phase_list = list(policy.get("phases", PHASES))

    current_active = normalize_phase(status.get("active_phase", default_phase), default=default_phase)
    baseline = _normalize_baseline(status.get("baseline", {}))
    current_score_row = score_map.get(current_active)
    candidate_phase = normalize_phase(summary_selection.get("active_phase", default_phase), default=default_phase)
    candidate_score_row = score_map.get(candidate_phase)

    complete_dates = list(summary_selection.get("complete_dates", []))
    incomplete_dates = list(summary_selection.get("incomplete_dates", []))
    eligible_phases = list(summary_selection.get("eligible_phases", []))
    compare_ready = (
        summary_selection.get("status") == "selected"
        and len(complete_dates) >= lookback_days
        and len(eligible_phases) == len(phase_list)
    )

    current_metrics = _score_metrics(current_score_row)
    candidate_metrics = _score_metrics(candidate_score_row)

    reasons: list[str] = []
    if not compare_ready:
        reasons.append("completeness collapsed")
    if current_score_row is None and current_active != "final":
        reasons.append("current active metrics unavailable")

    if baseline["avg_available"] > 0:
        if current_metrics["avg_available"] < baseline["avg_available"] * 0.8:
            reasons.append(
                f"avg_available degraded {current_metrics['avg_available']} < {baseline['avg_available'] * 0.8}"
            )
    elif current_metrics["avg_available"] < baseline["avg_available"]:
        reasons.append("avg_available degraded below baseline")

    if baseline["avg_missing"] > 0:
        if current_metrics["avg_missing"] > baseline["avg_missing"] * 1.5:
            reasons.append(
                f"avg_missing degraded {current_metrics['avg_missing']} > {baseline['avg_missing'] * 1.5}"
            )
    elif current_metrics["avg_missing"] > 0:
        reasons.append("avg_missing worsened from zero baseline")

    warning_streak = max(0, _coerce_int(status.get("warning_streak", 0)))
    if reasons:
        warning_streak += 1
    else:
        warning_streak = 0

    fallback_reason = "; ".join(reasons)
    should_fallback = bool(reasons and warning_streak >= 2)

    return {
        "should_fallback": should_fallback,
        "warning": bool(reasons and not should_fallback),
        "warning_streak": warning_streak,
        "fallback_reason": fallback_reason,
        "baseline": baseline,
        "current_metrics": current_metrics,
        "candidate_metrics": candidate_metrics,
        "current_active": current_active,
        "candidate_phase": candidate_phase,
        "complete_dates": complete_dates,
        "incomplete_dates": incomplete_dates,
        "eligible_phases": eligible_phases,
        "compare_ready": compare_ready,
    }


def _emergency_reason(*, decision: dict[str, Any]) -> str:
    return str(decision.get("fallback_reason", ""))


def _reevaluation_due(
    *,
    target_date: date,
    status: dict[str, Any],
    decision: dict[str, Any],
) -> tuple[bool, str]:
    interval_days = max(1, _coerce_int(status.get("reevaluation_interval_days", 7), 7))
    last_date = _parse_date_text(status.get("last_reevaluation_date", ""))
    current_mode = _coerce_str(status.get("mode", "fixed"), "fixed").lower()
    reasons: list[str] = []

    if current_mode == "reevaluation":
        return False, ""

    if not last_date:
        reasons.append("no prior reevaluation date")
        return True, "; ".join(reasons)

    days_since = (target_date - last_date).days
    if days_since >= interval_days:
        reasons.append(f"interval reached ({days_since} >= {interval_days})")

    if decision.get("warning"):
        reasons.append("warning streak active")

    if str(decision.get("fallback_reason", "")).strip():
        reasons.append("fallback just occurred")

    if decision.get("incomplete_dates"):
        reasons.append("completeness unstable")

    return bool(reasons), "; ".join(dict.fromkeys(reasons))


def update_active_phase_status(
    *,
    target_date: date,
    policy: dict[str, Any] | None = None,
    summary_path: Path | None = None,
    status_path: Path | None = None,
) -> dict[str, Any]:
    policy = policy or load_policy()
    default_phase = normalize_phase(policy.get("default_phase", "final"), default="final")
    summary_selection = select_active_phase(
        target_date=target_date,
        policy=policy,
        summary_path=summary_path,
    )
    status = load_active_phase_status(status_path)

    mode = _coerce_str(status.get("mode", "fixed"), "fixed").lower()
    if mode not in {"fixed", "reevaluation"}:
        mode = "fixed"

    current_active = normalize_phase(status.get("active_phase", default_phase), default=default_phase)
    current_candidate = normalize_phase(status.get("candidate_phase", default_phase), default=default_phase)
    current_streak = max(0, _coerce_int(status.get("candidate_streak", 0)))
    warning_streak = max(0, _coerce_int(status.get("warning_streak", 0)))
    locked_until = _coerce_str(status.get("locked_until", ""), "")
    last_reevaluation_date = _coerce_str(status.get("last_reevaluation_date", ""), "")
    interval_days = max(1, _coerce_int(status.get("reevaluation_interval_days", 7), 7))

    phase_scores = list(summary_selection.get("phase_scores", []) or [])
    score_map = _phase_score_map(phase_scores)
    decision = should_fallback_to_final(
        target_date=target_date,
        policy=policy,
        status=status,
        summary_selection=summary_selection,
        score_map=score_map,
    )

    candidate_phase = normalize_phase(summary_selection.get("active_phase", default_phase), default=default_phase)
    complete_dates = list(summary_selection.get("complete_dates", []))
    incomplete_dates = list(summary_selection.get("incomplete_dates", []))
    eligible_phases = list(summary_selection.get("eligible_phases", []))
    compare_ready = bool(decision["compare_ready"])

    if summary_selection.get("status") != "selected" or not phase_scores:
        candidate_phase = default_phase
        candidate_streak = 0
    elif candidate_phase == current_candidate:
        candidate_streak = current_streak + 1
    else:
        candidate_streak = 1 if candidate_phase else 0

    current_score = score_map.get(current_active)
    candidate_score = score_map.get(candidate_phase)
    baseline = _normalize_baseline(status.get("baseline", {}))
    current_metrics = decision["current_metrics"]
    baseline_metrics = dict(baseline)
    promotion_reason = ""

    reevaluation_due, reevaluation_reason = _reevaluation_due(
        target_date=target_date,
        status=status,
        decision=decision,
    )
    fallback_reason = decision["fallback_reason"]
    should_fallback = bool(decision["should_fallback"])

    if mode == "fixed" and reevaluation_due:
        mode = "reevaluation"

    if should_fallback:
        mode = "fixed"
        active_phase = "final"
        warning_streak = 0
        locked_until = ""
        promotion_reason = ""
        last_reevaluation_date = target_date.isoformat()
    elif mode == "reevaluation":
        last_reevaluation_date = target_date.isoformat()
        if compare_ready and candidate_phase != default_phase and candidate_streak >= PROMOTION_STREAK_THRESHOLD:
            active_phase = candidate_phase
            mode = "fixed"
            locked_until = (target_date + timedelta(days=LOCK_DAYS)).isoformat()
            promotion_reason = (
                f"promoted after reevaluation candidate_streak={candidate_streak} "
                f"with complete_dates={','.join(complete_dates) if complete_dates else '-'}"
            )
            warning_streak = 0
            baseline = {
                "avg_available": candidate_score.get("avg_real_odds_available", 0.0) if candidate_score else 0.0,
                "avg_pending": candidate_score.get("avg_pending_unpublished", 0.0) if candidate_score else 0.0,
                "avg_missing": candidate_score.get("avg_real_odds_missing_fetch", 0.0) if candidate_score else 0.0,
                "complete_dates": complete_dates,
            }
        else:
            active_phase = candidate_phase if compare_ready else default_phase
            promotion_reason = ""
    else:
        if locked_until and _is_lock_active(locked_until, target_date) and current_active != "final":
            active_phase = current_active
            promotion_reason = "locked period active"
        else:
            active_phase = current_active
            promotion_reason = ""
            if current_active == "final" and candidate_phase != default_phase and compare_ready and candidate_streak >= PROMOTION_STREAK_THRESHOLD:
                active_phase = candidate_phase
                locked_until = (target_date + timedelta(days=LOCK_DAYS)).isoformat()
                promotion_reason = (
                    f"promoted after candidate_streak={candidate_streak} "
                    f"with complete_dates={','.join(complete_dates) if complete_dates else '-'}"
                )
                baseline = {
                    "avg_available": candidate_score.get("avg_real_odds_available", 0.0) if candidate_score else 0.0,
                    "avg_pending": candidate_score.get("avg_pending_unpublished", 0.0) if candidate_score else 0.0,
                    "avg_missing": candidate_score.get("avg_real_odds_missing_fetch", 0.0) if candidate_score else 0.0,
                    "complete_dates": complete_dates,
                }

    if not should_fallback and reevaluation_due and mode == "fixed" and promotion_reason == "":
        mode = "reevaluation"

    if mode == "reevaluation" and reevaluation_reason:
        reason = reevaluation_reason
    else:
        reason = promotion_reason or fallback_reason or "active phase resolved"

    new_status = {
        "mode": mode,
        "active_phase": normalize_phase(active_phase, default=default_phase),
        "candidate_phase": candidate_phase,
        "candidate_streak": candidate_streak,
        "warning_streak": warning_streak,
        "locked_until": locked_until if mode == "fixed" and active_phase != "final" else "",
        "last_updated": target_date.isoformat(),
        "last_reevaluation_date": last_reevaluation_date,
        "reevaluation_interval_days": interval_days,
        "reason": reason,
        "fallback_reason": fallback_reason,
        "promotion_reason": promotion_reason,
        "reevaluation_reason": reevaluation_reason,
        "baseline": baseline,
        "candidate_score": candidate_score or {},
        "current_active_score": current_score or {},
        "current_metrics": current_metrics,
        "baseline_metrics": baseline_metrics,
        "complete_dates": complete_dates,
        "incomplete_dates": incomplete_dates,
        "eligible_phases": eligible_phases,
        "leaderboard": phase_scores,
        "selection": summary_selection,
        "reevaluation_due": reevaluation_due,
    }

    save_active_phase_status(new_status, status_path)
    return new_status
