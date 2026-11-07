from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.odds.fetch_daily_trifecta_odds import JCD_TO_STADIUM


ROOT = Path(__file__).resolve().parents[2]
DAILY_SERIES_DIR = ROOT / "reports" / "daily"
YEARLY_SERIES_DIR = ROOT / "reports" / "yearly_backtest" / "odds_time_series"

PHASE_ORDER = {
    "morning": 1,
    "late_refresh": 2,
    "final_refresh": 3,
}

PHASE_COMMANDS = {
    "morning": [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_pre_race",
    ],
    "late_refresh": [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_odds_refresh_late",
    ],
    "final_refresh": [
        sys.executable,
        "-m",
        "src.pipeline.run_daily_odds_refresh",
    ],
}


def parse_race_id(race_id: str) -> tuple[str, str, int] | None:
    parts = str(race_id).strip().split("-")
    if len(parts) != 3:
        return None
    date8, jcd, race_no = parts
    try:
        return date8, str(int(jcd)).zfill(2), int(race_no)
    except ValueError:
        return None


def phase_command(phase: str, target_date: date, delay: float, wait_minutes: float = 0.0) -> list[str]:
    if phase not in PHASE_COMMANDS:
        raise ValueError(f"unknown phase: {phase}")
    cmd = list(PHASE_COMMANDS[phase])
    cmd.extend(["--date", target_date.isoformat()])
    if phase == "morning":
        cmd.extend(["--delay", str(delay)])
    elif phase == "late_refresh":
        cmd.extend(["--wait-minutes", str(wait_minutes), "--delay", str(delay)])
    elif phase == "final_refresh":
        cmd.extend(["--delay", str(delay), "--refresh", "--pending-only"])
    return cmd


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _truthy_series(series: pd.Series) -> pd.Series:
    values = series.fillna("").astype(str).str.lower()
    return values.isin({"true", "1", "yes", "y"})


def _count_matches(series: pd.Series, value: str) -> int:
    return int(series.fillna("").astype(str).eq(value).sum())


def _count_prefix(series: pd.Series, prefix: str) -> int:
    return int(series.fillna("").astype(str).str.startswith(prefix).sum())


def _decorate_venue_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()
    if "jcd" not in work.columns:
        work["jcd"] = ""
    if "stadium" not in work.columns:
        work["stadium"] = ""
    if "race_id" in work.columns:
        parsed = work["race_id"].astype(str).map(parse_race_id)
        parsed_df = pd.DataFrame(parsed.tolist(), columns=["race_date_key", "jcd_from_id", "race_no_from_id"])
        work = pd.concat([work.reset_index(drop=True), parsed_df], axis=1)
        work["jcd"] = work["jcd"].astype(str).where(work["jcd"].astype(str).str.len() > 0, work["jcd_from_id"].fillna(""))
        work["stadium"] = work["stadium"].astype(str).where(
            work["stadium"].astype(str).str.len() > 0,
            work["jcd"].map(JCD_TO_STADIUM).fillna(""),
        )
    return work


def load_skip_snapshot(skip_path: Path) -> dict[str, Any]:
    df = _safe_read_csv(skip_path)
    if df.empty:
        return {
            "buy_count": 0,
            "real_odds_available": 0,
            "pending_unpublished": 0,
            "real_odds_missing_fetch_failed": 0,
            "real_odds_missing_never_fetched": 0,
            "pending": 0,
            "total_rows": 0,
            "odds_status_counts": {},
            "stop_reason_counts": {},
        }

    work = _decorate_venue_columns(df)
    decision = work.get("decision", pd.Series(dtype=object)).fillna("").astype(str).str.upper()
    stop_reason = work.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str)
    odds_status = work.get("odds_status", pd.Series(dtype=object)).fillna("").astype(str)
    has_real_odds = _truthy_series(work.get("has_real_odds", pd.Series(dtype=object)))

    real_odds_available = int(
        (
            odds_status.eq("real_odds_available")
            | has_real_odds
            | work.get("odds_source", pd.Series(dtype=object)).fillna("").astype(str).eq("real_live")
        ).sum()
    )
    pending_unpublished = int(
        (
            odds_status.eq("real_odds_pending_unpublished")
            | stop_reason.eq("real_odds_pending_unpublished")
            | stop_reason.str.contains("pending_unpublished", na=False)
        ).sum()
    )
    real_odds_missing_fetch_failed = int(
        (
            odds_status.eq("real_odds_missing_fetch_failed")
            | stop_reason.eq("real_odds_missing_fetch_failed")
            | stop_reason.str.startswith("real_odds_missing_fetch_failed", na=False)
        ).sum()
    )
    real_odds_missing_never_fetched = int(
        (
            odds_status.eq("real_odds_missing_never_fetched")
            | stop_reason.eq("real_odds_missing_never_fetched")
            | stop_reason.str.startswith("real_odds_missing_never_fetched", na=False)
        ).sum()
    )

    return {
        "buy_count": int(decision.eq("BUY").sum()),
        "real_odds_available": real_odds_available,
        "pending_unpublished": pending_unpublished,
        "real_odds_missing_fetch_failed": real_odds_missing_fetch_failed,
        "real_odds_missing_never_fetched": real_odds_missing_never_fetched,
        "pending": int(decision.eq("PENDING").sum()),
        "total_rows": int(len(work)),
        "odds_status_counts": odds_status.value_counts().to_dict(),
        "stop_reason_counts": stop_reason.value_counts().to_dict(),
    }


def build_venue_snapshot(skip_path: Path, phase: str, measured_at: str, target_date: date) -> pd.DataFrame:
    df = _safe_read_csv(skip_path)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "phase",
                "measured_at",
                "jcd",
                "stadium",
                "real_odds_available",
                "pending_unpublished",
                "real_odds_missing_fetch_failed",
                "real_odds_missing_never_fetched",
                "buy_count",
                "total_rows",
            ]
        )

    work = _decorate_venue_columns(df)
    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    else:
        work["date"] = target_date.isoformat()

    work["decision"] = work.get("decision", pd.Series(dtype=object)).fillna("").astype(str).str.upper()
    work["stop_reason"] = work.get("stop_reason", pd.Series(dtype=object)).fillna("").astype(str)
    work["odds_status"] = work.get("odds_status", pd.Series(dtype=object)).fillna("").astype(str)
    work["has_real_odds"] = _truthy_series(work.get("has_real_odds", pd.Series(dtype=object)))
    work["odds_source"] = work.get("odds_source", pd.Series(dtype=object)).fillna("").astype(str)

    rows: list[dict[str, Any]] = []
    for (jcd, stadium), group in work.groupby(["jcd", "stadium"], dropna=False):
        odds_status = group["odds_status"]
        stop_reason = group["stop_reason"]
        rows.append(
            {
                "date": target_date.isoformat(),
                "phase": phase,
                "measured_at": measured_at,
                "jcd": str(jcd),
                "stadium": str(stadium),
                "real_odds_available": int(
                    (
                        odds_status.eq("real_odds_available")
                        | group["has_real_odds"]
                        | group["odds_source"].eq("real_live")
                    ).sum()
                ),
                "pending_unpublished": int(
                    (
                        odds_status.eq("real_odds_pending_unpublished")
                        | stop_reason.eq("real_odds_pending_unpublished")
                        | stop_reason.str.contains("pending_unpublished", na=False)
                    ).sum()
                ),
                "real_odds_missing_fetch_failed": int(
                    (
                        odds_status.eq("real_odds_missing_fetch_failed")
                        | stop_reason.eq("real_odds_missing_fetch_failed")
                        | stop_reason.str.startswith("real_odds_missing_fetch_failed", na=False)
                    ).sum()
                ),
                "real_odds_missing_never_fetched": int(
                    (
                        odds_status.eq("real_odds_missing_never_fetched")
                        | stop_reason.eq("real_odds_missing_never_fetched")
                        | stop_reason.str.startswith("real_odds_missing_never_fetched", na=False)
                    ).sum()
                ),
                "buy_count": int(group["decision"].eq("BUY").sum()),
                "total_rows": int(len(group)),
            }
        )

    venue_df = pd.DataFrame(rows)
    if not venue_df.empty:
        venue_df["jcd"] = venue_df["jcd"].astype(str).str.zfill(2)
        venue_df = venue_df.sort_values(["phase", "jcd"]).reset_index(drop=True)
    return venue_df


def build_phase_row(
    *,
    target_date: date,
    phase: str,
    measured_at: str,
    skip_path: Path,
    run_status: str,
    pipeline_report_path: Path | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    snapshot = load_skip_snapshot(skip_path)
    row = {
        "date": target_date.isoformat(),
        "phase": phase,
        "phase_order": PHASE_ORDER.get(phase, 99),
        "measured_at": measured_at,
        "run_status": run_status,
        "skip_path": str(skip_path),
        "pipeline_report_path": str(pipeline_report_path) if pipeline_report_path else "",
        "command": " ".join(command or []),
        **snapshot,
    }
    return row


def append_phase_rows(
    *,
    target_date: date,
    phase_row: dict[str, Any],
    venue_rows: pd.DataFrame,
) -> tuple[Path, Path, Path]:
    day_dir = DAILY_SERIES_DIR / target_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    phase_path = day_dir / "odds_time_series.csv"
    venue_path = day_dir / "odds_time_series_venue.csv"
    snapshot_dir = day_dir / "odds_time_series_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    phase_df = _safe_read_csv(phase_path)
    if not phase_df.empty and "phase" in phase_df.columns:
        phase_df = phase_df[phase_df["phase"].astype(str) != str(phase_row["phase"])]
    phase_df = pd.concat([phase_df, pd.DataFrame([phase_row])], ignore_index=True)
    phase_df = phase_df.sort_values(["phase_order", "measured_at"]).reset_index(drop=True)
    phase_df.to_csv(phase_path, index=False)

    if venue_path.exists():
        existing_venue = _safe_read_csv(venue_path)
        if not existing_venue.empty and "phase" in existing_venue.columns and "measured_at" in existing_venue.columns:
            existing_venue = existing_venue[
                ~(
                    (existing_venue["phase"].astype(str) == str(phase_row["phase"]))
                    & (existing_venue["measured_at"].astype(str) == str(phase_row["measured_at"]))
                )
            ]
            venue_df = pd.concat([existing_venue, venue_rows], ignore_index=True)
        else:
            venue_df = venue_rows.copy()
    else:
        venue_df = venue_rows.copy()
    if not venue_df.empty:
        venue_df = venue_df.sort_values(["phase", "jcd"]).reset_index(drop=True)
    venue_df.to_csv(venue_path, index=False)

    snapshot_path = snapshot_dir / f"{phase_row['phase']}_{phase_row['measured_at'].replace(':', '')}.json"
    snapshot_path.write_text(json.dumps({"phase_row": phase_row, "venue_rows": venue_rows.to_dict(orient="records")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return phase_path, venue_path, snapshot_path


def rebuild_global_series() -> tuple[Path, Path]:
    phase_paths = sorted(DAILY_SERIES_DIR.glob("*/odds_time_series.csv"))
    venue_paths = sorted(DAILY_SERIES_DIR.glob("*/odds_time_series_venue.csv"))

    phase_frames = []
    for path in phase_paths:
        df = _safe_read_csv(path)
        if not df.empty:
            phase_frames.append(df)
    phase_all = pd.concat(phase_frames, ignore_index=True) if phase_frames else pd.DataFrame()
    if not phase_all.empty:
        phase_all = phase_all.sort_values(["date", "phase_order", "measured_at"]).reset_index(drop=True)

    venue_frames = []
    for path in venue_paths:
        df = _safe_read_csv(path)
        if not df.empty:
            venue_frames.append(df)
    venue_all = pd.concat(venue_frames, ignore_index=True) if venue_frames else pd.DataFrame()
    if not venue_all.empty:
        venue_all = venue_all.sort_values(["date", "phase", "jcd"]).reset_index(drop=True)

    root_phase_path = DAILY_SERIES_DIR / "odds_time_series.csv"
    root_venue_path = DAILY_SERIES_DIR / "odds_time_series_venue.csv"
    DAILY_SERIES_DIR.mkdir(parents=True, exist_ok=True)
    phase_all.to_csv(root_phase_path, index=False)
    venue_all.to_csv(root_venue_path, index=False)
    return root_phase_path, root_venue_path


def build_summary_tables() -> dict[str, Any]:
    phase_path = DAILY_SERIES_DIR / "odds_time_series.csv"
    venue_path = DAILY_SERIES_DIR / "odds_time_series_venue.csv"
    phase_df = _safe_read_csv(phase_path)
    venue_df = _safe_read_csv(venue_path)

    if phase_df.empty:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "message": "No odds time-series data found yet.",
            "phase_rows": 0,
            "venue_rows": 0,
            "daily_comparison": [],
            "phase_aggregate": {},
            "venue_summary": [],
        }

    phase_df["phase_order"] = pd.to_numeric(phase_df.get("phase_order"), errors="coerce").fillna(99).astype(int)
    phase_df["date"] = phase_df["date"].astype(str)
    phase_df["phase"] = phase_df["phase"].astype(str)

    daily = phase_df[[
        "date",
        "phase",
        "real_odds_available",
        "pending_unpublished",
        "real_odds_missing_fetch_failed",
        "real_odds_missing_never_fetched",
        "buy_count",
        "total_rows",
        "measured_at",
        "run_status",
    ]].copy()
    daily = daily.sort_values(["date", "phase_order", "measured_at"]).reset_index(drop=True)

    pairs: list[dict[str, Any]] = []
    for target_date, group in phase_df.groupby("date"):
        group = group.sort_values("phase_order")
        lookup = {row.phase: row for row in group.itertuples(index=False)}
        if "morning" in lookup and "late_refresh" in lookup:
            morning = lookup["morning"]
            late = lookup["late_refresh"]
            pairs.append(
                {
                    "date": target_date,
                    "window": "morning_to_late",
                    "real_odds_available_delta": int(late.real_odds_available) - int(morning.real_odds_available),
                    "pending_unpublished_delta": int(late.pending_unpublished) - int(morning.pending_unpublished),
                    "buy_count_delta": int(late.buy_count) - int(morning.buy_count),
                    "real_odds_available_before": int(morning.real_odds_available),
                    "real_odds_available_after": int(late.real_odds_available),
                    "pending_unpublished_before": int(morning.pending_unpublished),
                    "pending_unpublished_after": int(late.pending_unpublished),
                    "buy_count_before": int(morning.buy_count),
                    "buy_count_after": int(late.buy_count),
                }
            )
        if "late_refresh" in lookup and "final_refresh" in lookup:
            late = lookup["late_refresh"]
            final = lookup["final_refresh"]
            pairs.append(
                {
                    "date": target_date,
                    "window": "late_to_final",
                    "real_odds_available_delta": int(final.real_odds_available) - int(late.real_odds_available),
                    "pending_unpublished_delta": int(final.pending_unpublished) - int(late.pending_unpublished),
                    "buy_count_delta": int(final.buy_count) - int(late.buy_count),
                    "real_odds_available_before": int(late.real_odds_available),
                    "real_odds_available_after": int(final.real_odds_available),
                    "pending_unpublished_before": int(late.pending_unpublished),
                    "pending_unpublished_after": int(final.pending_unpublished),
                    "buy_count_before": int(late.buy_count),
                    "buy_count_after": int(final.buy_count),
                }
            )

    pair_summary: dict[str, Any] = {}
    for window in ("morning_to_late", "late_to_final"):
        subset = [row for row in pairs if row["window"] == window]
        if subset:
            pair_summary[window] = {
                "days": int(len(subset)),
                "avg_real_odds_available_delta": round(float(pd.Series([row["real_odds_available_delta"] for row in subset]).mean()), 3),
                "avg_pending_unpublished_delta": round(float(pd.Series([row["pending_unpublished_delta"] for row in subset]).mean()), 3),
                "avg_buy_count_delta": round(float(pd.Series([row["buy_count_delta"] for row in subset]).mean()), 3),
                "days_with_real_odds_available_increase": int(sum(1 for row in subset if row["real_odds_available_delta"] > 0)),
                "days_with_pending_unpublished_decrease": int(sum(1 for row in subset if row["pending_unpublished_delta"] < 0)),
            }

    phase_aggregate_rows: list[dict[str, Any]] = []
    for phase, group in phase_df.groupby("phase"):
        phase_aggregate_rows.append(
            {
                "phase": phase,
                "days": int(group["date"].nunique()),
                "rows": int(len(group)),
                "avg_real_odds_available": round(float(group["real_odds_available"].mean()), 3),
                "avg_pending_unpublished": round(float(group["pending_unpublished"].mean()), 3),
                "avg_real_odds_missing_fetch_failed": round(float(group["real_odds_missing_fetch_failed"].mean()), 3),
                "avg_real_odds_missing_never_fetched": round(float(group["real_odds_missing_never_fetched"].mean()), 3),
                "avg_buy_count": round(float(group["buy_count"].mean()), 3),
            }
        )

    venue_summary_rows: list[dict[str, Any]] = []
    if not venue_df.empty:
        venue_df["phase"] = venue_df["phase"].astype(str)
        venue_df["jcd"] = venue_df["jcd"].astype(str)
        venue_df["stadium"] = venue_df["stadium"].astype(str)
        for (jcd, stadium), group in venue_df.groupby(["jcd", "stadium"], dropna=False):
            venue_summary_rows.append(
                {
                    "jcd": jcd,
                    "stadium": stadium,
                    "days": int(group["date"].nunique()),
                    "rows": int(len(group)),
                    "avg_real_odds_available": round(float(group["real_odds_available"].mean()), 3),
                    "avg_pending_unpublished": round(float(group["pending_unpublished"].mean()), 3),
                    "avg_real_odds_missing_fetch_failed": round(float(group["real_odds_missing_fetch_failed"].mean()), 3),
                    "avg_real_odds_missing_never_fetched": round(float(group["real_odds_missing_never_fetched"].mean()), 3),
                    "avg_buy_count": round(float(group["buy_count"].mean()), 3),
                }
            )
        venue_summary_rows = sorted(venue_summary_rows, key=lambda row: (row["avg_pending_unpublished"], row["avg_real_odds_available"]), reverse=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase_rows": int(len(phase_df)),
        "venue_rows": int(len(venue_df)),
        "phase_table": daily.to_dict(orient="records"),
        "daily_comparison": pairs,
        "pair_summary": pair_summary,
        "phase_aggregate": phase_aggregate_rows,
        "venue_summary": venue_summary_rows,
    }
    return payload
