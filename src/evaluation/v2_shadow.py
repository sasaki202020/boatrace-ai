from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from src.evaluation.reason_taxonomy import (
    compare_possible,
    count_odds_available_races,
    count_result_available_races,
    normalize_date_str,
)


def _count_distinct_for_date(df: pd.DataFrame, date_str: str, column: str = "race_id") -> int:
    if df.empty or "date" not in df.columns or column not in df.columns:
        return 0
    work = df.copy()
    work["date"] = work["date"].astype(str).map(normalize_date_str)
    return int(work.loc[work["date"] == date_str, column].astype(str).nunique())


def _sum_rows_for_date(df: pd.DataFrame, date_str: str) -> int:
    if df.empty or "date" not in df.columns:
        return 0
    work = df.copy()
    work["date"] = work["date"].astype(str).map(normalize_date_str)
    return int(len(work.loc[work["date"] == date_str]))


def _summarize_prob_frame(df: pd.DataFrame, prob_col: str) -> dict[str, Any]:
    if df.empty or prob_col not in df.columns:
        return {"rows": 0, "avg_prob": None}
    series = pd.to_numeric(df[prob_col], errors="coerce").dropna()
    return {
        "rows": int(len(df)),
        "avg_prob": round(float(series.mean()), 4) if not series.empty else None,
    }


def build_shadow_summary(
    date_str: str,
    compare_status: str,
    tables: Mapping[str, pd.DataFrame],
    raw_candidates: pd.DataFrame,
    calibrated_candidates: pd.DataFrame,
    v1_reference: Mapping[str, Any] | None = None,
    v1_compare: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    races = tables.get("races", pd.DataFrame())
    entries = tables.get("entries", pd.DataFrame())
    results = tables.get("results", pd.DataFrame())
    odds = tables.get("odds_snapshots", pd.DataFrame())

    target_races = _count_distinct_for_date(races, date_str, "race_id")
    entry_rows = _sum_rows_for_date(entries, date_str)
    result_rows = _sum_rows_for_date(results, date_str)
    odds_rows = _sum_rows_for_date(odds, date_str)
    result_available_races = count_result_available_races(results, date_str)
    odds_covered_races = count_odds_available_races(odds, date_str)
    odds_coverage = round(odds_covered_races / target_races, 4) if target_races > 0 else 0.0
    compare_ok = compare_possible(
        compare_status=compare_status,
        target_races=target_races,
        result_available_races=result_available_races,
        odds_available_races=odds_covered_races,
    )

    raw_summary = _summarize_prob_frame(raw_candidates, "approx_prob")
    cal_prob_col = "calibrated_prob" if "calibrated_prob" in calibrated_candidates.columns else "approx_prob"
    calibrated_summary = _summarize_prob_frame(calibrated_candidates, cal_prob_col)

    raw_rows = int(raw_summary["rows"])
    cal_rows = int(calibrated_summary["rows"])
    raw_avg_prob = raw_summary["avg_prob"]
    cal_avg_prob = calibrated_summary["avg_prob"]

    basic_diff = {
        "candidate_rows_diff": cal_rows - raw_rows,
        "avg_prob_diff": None if raw_avg_prob is None or cal_avg_prob is None else round(float(cal_avg_prob) - float(raw_avg_prob), 4),
    }

    return {
        "date": date_str,
        "compare_status": compare_status,
        "target_races": target_races,
        "entry_rows": entry_rows,
        "result_rows": result_rows,
        "result_available_races": result_available_races,
        "odds_rows": odds_rows,
        "odds_covered_races": odds_covered_races,
        "odds_coverage": odds_coverage,
        "compare_possible": compare_ok,
        "raw_candidates": raw_summary,
        "calibrated_candidates": calibrated_summary,
        "raw_calibrated_diff": basic_diff,
        "v1_reference": dict(v1_reference or {}),
        "v1_compare": dict(v1_compare or {}),
    }
