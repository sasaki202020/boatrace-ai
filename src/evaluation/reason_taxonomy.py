from __future__ import annotations

from typing import Any, Mapping

import pandas as pd


FAILURE_REASON_CODES = ("MISSING_RESULT", "MISSING_ODDS", "RAW_INCOMPLETE", "EVAL_ERROR")


def normalize_date_str(value: object) -> str:
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _filter_day_frame(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df.iloc[0:0].copy()

    work = df.copy()
    matched = False
    for column in ("date", "race_date"):
        if column in work.columns:
            work[column] = work[column].astype(str).map(normalize_date_str)
            matched = True

    if not matched:
        return work.iloc[0:0].copy()

    if "date" in work.columns:
        filtered = work[work["date"] == date_str].copy()
        if not filtered.empty:
            return filtered.reset_index(drop=True)

    if "race_date" in work.columns:
        filtered = work[work["race_date"] == date_str].copy()
        if not filtered.empty:
            return filtered.reset_index(drop=True)

    return work.iloc[0:0].copy()


def _coerce_bool_series(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes", "y"})


def count_result_available_races(results: pd.DataFrame, date_str: str) -> int:
    if results.empty or "race_id" not in results.columns:
        return 0
    work = _filter_day_frame(results, date_str)
    if work.empty:
        return 0
    if "status" in work.columns:
        work = work[work["status"].astype(str).str.lower() == "available"].copy()
    elif "result_available" in work.columns:
        work = work[_coerce_bool_series(work["result_available"])].copy()
    return int(work["race_id"].astype(str).nunique())


def count_odds_available_races(odds: pd.DataFrame, date_str: str) -> int:
    if odds.empty or "race_id" not in odds.columns:
        return 0
    work = _filter_day_frame(odds, date_str)
    if work.empty:
        return 0
    if "odds" in work.columns:
        work = work[pd.to_numeric(work["odds"], errors="coerce").notna()].copy()
    elif "odds_status" in work.columns:
        work = work[work["odds_status"].astype(str).str.lower().isin({"real_odds_available", "available", "real"})].copy()
    elif "real_odds_available" in work.columns:
        work = work[_coerce_bool_series(work["real_odds_available"])].copy()
    return int(work["race_id"].astype(str).nunique())


def compare_possible(
    *,
    compare_status: str,
    target_races: int,
    result_available_races: int,
    odds_available_races: int,
) -> bool:
    return bool(
        compare_status == "TARGET"
        and target_races > 0
        and result_available_races == target_races
        and odds_available_races > 0
    )


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        code = str(value or "").strip().upper()
        if not code or code in seen or code not in FAILURE_REASON_CODES:
            continue
        ordered.append(code)
        seen.add(code)
    return ordered


def build_failure_reasons(
    *,
    compare_status: str,
    target_races: int,
    result_available_races: int,
    odds_available_races: int,
    compare_possible: bool,
    v1_payload: Mapping[str, Any],
    evaluation_error: str | None = None,
) -> list[str]:
    if compare_status != "TARGET":
        return []

    reasons: list[str] = []
    if target_races <= 0 or result_available_races <= 0 or result_available_races < target_races:
        reasons.append("MISSING_RESULT")
    if target_races > 0 and odds_available_races <= 0:
        reasons.append("MISSING_ODDS")

    raw_exit = int(v1_payload.get("raw_sim_exit", 0) or 0)
    cal_exit = int(v1_payload.get("cal_sim_exit", 0) or 0)
    if v1_payload and (raw_exit != 0 or cal_exit != 0):
        reasons.append("RAW_INCOMPLETE")

    if evaluation_error:
        reasons.append("EVAL_ERROR")

    # Do not invent UNKNOWN; if the taxonomy is unclear, keep the list empty.
    return _dedupe_preserve_order(reasons)
